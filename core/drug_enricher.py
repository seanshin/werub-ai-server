"""Drug LLM Enricher — 배치 큐 연동 + 결과 파싱

수집된 원시 데이터를 LLM으로 구조화.
기존 /api/queue/batch 시스템에 priority=4(background)로 제출.
"""
import json
import logging
import re
import sqlite3
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from .config import get_self_base

logger = logging.getLogger("ollama-admin")

DRUG_ENRICH_SYSTEM = (
    "당신은 한국 병원에서 근무하는 약학 전문가입니다. "
    "요청에 따라 실제 존재하는 약물 정보를 정확한 JSON으로 생성하세요. "
    "반드시 실제 약물명, 실제 용량, 실제 ATC 코드를 사용하세요. "
    "가상의 약물을 만들지 마세요. JSON만 출력하세요."
)

INTERACTION_ENRICH_SYSTEM = (
    "약물 상호작용 전문가입니다. 두 약물의 상호작용을 분석하세요. "
    "JSON만 출력하세요."
)

# LLM_SEED용: 카테고리로부터 실제 약물을 생성하는 프롬프트
DRUG_GENERATE_TEMPLATE = (
    "'{category}' 카테고리에서 한국 임상에서 자주 처방되는 실제 약물 1개의 정보를 JSON으로 작성하세요.\n\n"
    "규칙:\n"
    "- 반드시 실제 존재하는 약물이어야 합니다\n"
    "- code는 D-영문3자+숫자2자 형식 (예: D-MER01)\n"
    "- name_generic은 영문 INN명, name_ko는 한국어명\n"
    "- atc_code는 실제 WHO ATC 코드\n"
    "- contraindications와 side_effects는 한국어 배열\n\n"
    "JSON 형식:\n"
    '{{"code":"D-???01","name_generic":"영문명","name_ko":"한국어명",'
    '"dosage_form":"정제|캡슐|주사|시럽","strength":"용량",'
    '"atc_code":"A01AA01","category":"{category}",'
    '"route":"경구|정맥|피하|근주","frequency":"1일1회",'
    '"max_daily_dose":"최대용량","pregnancy_category":"A|B|C|D|X",'
    '"renal_adjustment":"불필요|용량조절","contraindications":["금기1"],'
    '"side_effects":["부작용1","부작용2"],"insurance_type":"covered","is_common":0}}'
)

# 외부 소스 데이터 구조화용 프롬프트 (MFDS 등)
DRUG_STRUCTURE_TEMPLATE = (
    "다음 약물 원시 데이터를 JSON으로 구조화하세요:\n원시 데이터: {raw}\n\n"
    '출력 JSON:\n{{"code":"D-???01","name_generic":"","name_ko":"",'
    '"dosage_form":"","strength":"","atc_code":"","category":"",'
    '"route":"","frequency":"","max_daily_dose":"","pregnancy_category":"",'
    '"renal_adjustment":"","contraindications":[],"side_effects":[],'
    '"insurance_type":"covered","is_common":0}}'
)

INTERACTION_TEMPLATE = (
    "약물 A: {drug_a}\n약물 B: {drug_b}\n\n"
    '분석 JSON:\n{{"severity":"major|moderate|minor","category":"PK|PD|ADR",'
    '"description":"","mechanism":"","clinical_effect":"","management":"",'
    '"evidence_level":"A|B|C"}}'
)


def submit_enrich_batch(
    staging_rows: list[dict],
    enrich_type: str = "drug",
    model: str = "werub-medical:7b",
) -> str | None:
    """staging 레코드를 LLM 배치 큐에 제출. batch_id 반환."""
    base = get_self_base()
    jobs = []

    for row in staging_rows:
        if enrich_type == "drug":
            # LLM_SEED → 생성 프롬프트, 그 외 → 구조화 프롬프트
            raw = row["raw_data"]
            if row.get("source") == "LLM_SEED":
                try:
                    raw_obj = json.loads(raw) if isinstance(raw, str) else raw
                    category = raw_obj.get("category", "")
                except (json.JSONDecodeError, TypeError):
                    category = ""
                prompt = DRUG_GENERATE_TEMPLATE.format(category=category)
            else:
                prompt = DRUG_STRUCTURE_TEMPLATE.format(raw=raw)
            system = DRUG_ENRICH_SYSTEM
        elif enrich_type == "interaction":
            raw = json.loads(row["raw_data"]) if isinstance(row["raw_data"], str) else row["raw_data"]
            prompt = INTERACTION_TEMPLATE.format(drug_a=raw.get("drug_a", ""), drug_b=raw.get("drug_b", ""))
            system = INTERACTION_ENRICH_SYSTEM
        else:
            continue

        jobs.append({
            "prompt": prompt,
            "metadata": {
                "staging_id": row["id"],
                "type": f"{enrich_type}_enrich",
                "source": row.get("source", ""),
            },
        })

    if not jobs:
        return None

    payload = {
        "model": model,
        "system": system if enrich_type == "drug" else INTERACTION_ENRICH_SYSTEM,
        "priority": 4,
        "jobs": jobs,
    }

    try:
        req = urllib.request.Request(
            f"{base}/api/queue/batch",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            batch_id = data.get("batch_id")
            logger.info(f"[drug-enricher] submitted batch {batch_id}, {len(jobs)} jobs")
            return batch_id
    except urllib.error.HTTPError as e:
        logger.error(f"[drug-enricher] batch submit failed: {e.code}")
        return None
    except Exception as e:
        logger.error(f"[drug-enricher] batch submit error: {e}")
        return None


def poll_batch_results(batch_id: str, timeout_sec: int = 600, poll_interval: int = 5) -> list[dict] | None:
    """배치 완료 대기 후 결과 반환."""
    base = get_self_base()
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"{base}/api/queue/batch/{batch_id}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                pending = data.get("pending", 0) + data.get("running", 0)
                if pending == 0:
                    return data.get("jobs", [])
        except Exception:
            pass
        time.sleep(poll_interval)

    logger.warning(f"[drug-enricher] batch {batch_id} timed out after {timeout_sec}s")
    return None


def parse_llm_response(response: str) -> dict | None:
    """LLM 응답에서 JSON 추출."""
    if not response:
        return None

    # 1. ```json ... ``` 블록
    m = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 2. 첫 번째 { ... }
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # 3. 전체 시도
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        return None


def calc_confidence(source_tier: str, enrich_method: str, validations: list) -> float:
    """신뢰도 점수 산정."""
    base_scores = {"T1": 0.95, "T2": 0.85, "T3": 0.75, "T4": 0.60}
    enrich_factors = {"raw": 1.00, "translate": 0.95, "structure": 0.90, "infer": 0.80}

    base = base_scores.get(source_tier, 0.60)
    factor = enrich_factors.get(enrich_method, 0.90)
    score = base * factor

    # verification bonuses
    for v in validations:
        if v.get("passed"):
            layer = v.get("layer", "")
            if layer == "L1":
                score *= 1.02
            elif layer == "L2":
                score *= 1.02
            elif layer == "L3":
                score *= 1.03

    return round(min(score, 1.0), 4)
