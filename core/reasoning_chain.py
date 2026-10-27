"""의료 추론 체인 엔진 — 5단계 감별진단 파이프라인 (P4-1)

Pipeline:
  1. 증상 정규화 (Symptom Normalization)
  2. 감별 진단 생성 (Differential Diagnosis)
  3. 근거 기반 순위화 (Evidence Ranking)
  4. 추가 검사 제안 (Recommended Tests)
  5. 최종 평가 (Final Assessment)
"""
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger("ollama-admin")

_ollama_session = None
_OLLAMA_BASE = None
_medical_generate = None  # routes/medical._medical_generate 주입


def init_reasoning_chain(*, ollama_session, ollama_base, medical_generate_fn):
    global _ollama_session, _OLLAMA_BASE, _medical_generate
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _medical_generate = medical_generate_fn


# ---------------------------------------------------------------------------
# Step Prompts
# ---------------------------------------------------------------------------

STEP1_NORMALIZE = """당신은 임상 증상 정규화 AI입니다. 반드시 한국어로 답변하세요.

환자의 자유 텍스트 호소를 구조화된 증상 목록으로 정규화하세요.

[규칙]
1. 모호한 표현 → 의학 용어로 변환 (예: "배가 아파요" → "복통(abdominal pain)")
2. 시간 정보 추출 (발병 시점, 지속 기간, 빈도)
3. 관련 위험요인 추출 (나이, 성별, 기저질환, 약물, 가족력)
4. 음성 소견(없는 증상)도 기록

반드시 JSON으로만 출력:
{
  "symptoms": [{"name": "증상명", "name_en": "English term", "onset": "시작 시점", "duration": "지속기간", "severity": "mild/moderate/severe", "character": "양상 설명"}],
  "risk_factors": [{"factor": "위험요인", "detail": "상세"}],
  "negatives": ["부정 소견"],
  "demographics": {"age": null, "sex": null, "known_conditions": []}
}"""

STEP2_DIFFERENTIAL = """당신은 감별진단 전문 AI입니다. 반드시 한국어로 답변하세요.

정규화된 증상 데이터를 기반으로 감별진단 5~8개를 확률순으로 생성하세요.

[규칙]
1. Common → Uncommon 순서 (유병률 고려)
2. 각 진단에 ICD-10 코드 필수
3. 응급 질환(Red flag)은 확률과 무관하게 반드시 포함
4. 근거(supporting)와 반대 근거(against)를 명시

반드시 JSON 배열로만 출력:
[
  {
    "rank": 1,
    "diagnosis": "영문 진단명",
    "diagnosis_ko": "한국어 진단명",
    "icd10": "ICD-10 코드",
    "prior_probability": "확률 표현 (high/medium/low)",
    "supporting": ["근거1", "근거2"],
    "against": ["반대 근거"],
    "is_emergency": false,
    "reasoning": "이 진단을 고려하는 논리적 근거"
  }
]"""

STEP3_RANKING = """당신은 근거 기반 의학(EBM) 전문 AI입니다. 반드시 한국어로 답변하세요.

감별진단 목록을 임상 근거에 기반하여 재순위화하세요.

[평가 기준]
1. 증상 일치도 (0-100): 제시된 증상과 해당 질환의 전형적 증상 일치 정도
2. 유병률 반영 (0-100): 역학적 빈도
3. 위험요인 관련성 (0-100): 환자의 위험요인과의 관련성
4. Red flag 가중치: 응급 질환은 10점 가산

반드시 JSON 배열로만 출력:
[
  {
    "rank": 1,
    "diagnosis_ko": "진단명",
    "icd10": "코드",
    "score": 85,
    "score_breakdown": {"symptom_match": 90, "prevalence": 80, "risk_relevance": 85, "red_flag_bonus": 0},
    "confidence": "high/medium/low",
    "clinical_pearls": "감별 포인트"
  }
]"""

STEP4_TESTS = """당신은 진단 검사 전문 AI입니다. 반드시 한국어로 답변하세요.

순위화된 감별진단 목록에 대해 확진을 위한 최소 검사 세트를 제안하세요.

[규칙]
1. 비용 대비 효과가 높은 검사 우선
2. 비침습적 검사 → 침습적 검사 순서
3. 한 검사로 여러 진단을 감별할 수 있으면 우선 추천
4. 응급 검사와 외래 검사 구분

반드시 JSON으로만 출력:
{
  "immediate": [{"test": "검사명", "test_en": "English", "purpose": "목적", "targets": ["감별 대상 진단"], "priority": "urgent/routine"}],
  "followup": [{"test": "검사명", "test_en": "English", "purpose": "목적", "targets": ["감별 대상 진단"]}],
  "avoid": [{"test": "불필요 검사", "reason": "사유"}]
}"""

STEP5_ASSESSMENT = """당신은 종합 임상 평가 AI입니다. 반드시 한국어로 답변하세요.

전체 추론 결과를 종합하여 최종 임상 평가를 작성하세요.

[포함 항목]
1. 가장 가능성 높은 진단 + 근거 요약
2. 배제해야 할 위험 진단
3. 즉시 필요한 조치
4. 환자 교육 사항
5. 추적 관찰 계획

반드시 JSON으로만 출력:
{
  "primary_diagnosis": {"diagnosis_ko": "...", "icd10": "...", "confidence": "high/medium/low", "reasoning_summary": "..."},
  "red_flags": [{"condition": "위험 질환", "action": "필요 조치"}],
  "immediate_actions": ["즉시 조치 사항"],
  "patient_education": ["환자 설명 사항"],
  "follow_up": {"timeline": "추적 시기", "criteria": "재방문 기준"},
  "urgency": "low/medium/high/critical",
  "disclaimer": "이 분석은 AI 보조 도구이며, 최종 진단은 담당 의사의 임상 판단에 따릅니다."
}"""

# ---------------------------------------------------------------------------
# Step executors
# ---------------------------------------------------------------------------

def _run_step(step_name, system_prompt, user_prompt, model, options=None):
    """단일 추론 단계 실행"""
    start = time.time()
    response, tokens, duration_ms = _medical_generate(
        model=model,
        prompt=user_prompt,
        system=system_prompt,
        options=options or {"temperature": 0.3, "top_p": 0.9},
        action=f"reasoning_{step_name}",
    )
    elapsed_ms = int((time.time() - start) * 1000)

    from core.quality import force_json, force_json_array
    from core.korean import force_korean_json
    if step_name in ("differential", "ranking"):
        parsed = force_korean_json(force_json_array(response))
    else:
        parsed = force_korean_json(force_json(response))

    return {
        "step": step_name,
        "raw": response,
        "parsed": parsed,
        "tokens": tokens,
        "duration_ms": elapsed_ms,
    }


def run_reasoning_chain(patient_text, *, model=None, specialty=None,
                         patient_info=None, options=None):
    """5단계 추론 체인 실행

    Args:
        patient_text: 환자 호소 자유 텍스트
        model: LLM 모델 (default: heavy model)
        specialty: 전문과 (선택)
        patient_info: {"age": int, "sex": str, "conditions": [...]} (선택)
        options: LLM 옵션

    Returns:
        {
            "chain_id": str,
            "steps": [...],
            "final_assessment": {...},
            "total_tokens": int,
            "total_duration_ms": int,
            "model": str,
        }
    """
    from routes.medical import get_heavy_model, SPECIALTY_HINTS

    chain_id = str(uuid.uuid4())
    if model is None:
        model = get_heavy_model()
    opts = options or {"temperature": 0.3, "top_p": 0.9}

    # 환자 컨텍스트 구성
    context_parts = [patient_text]
    if patient_info:
        info_lines = []
        if patient_info.get("age"):
            info_lines.append(f"나이: {patient_info['age']}세")
        if patient_info.get("sex"):
            info_lines.append(f"성별: {patient_info['sex']}")
        if patient_info.get("conditions"):
            info_lines.append(f"기저질환: {', '.join(patient_info['conditions'])}")
        if patient_info.get("medications"):
            info_lines.append(f"복용약물: {', '.join(patient_info['medications'])}")
        if info_lines:
            context_parts.append("\n[환자 정보]\n" + "\n".join(info_lines))

    full_input = "\n".join(context_parts)

    # 전문과 힌트
    specialty_hint = ""
    if specialty and specialty in SPECIALTY_HINTS:
        specialty_hint = SPECIALTY_HINTS[specialty]

    steps = []
    total_tokens = 0
    total_ms = 0

    # --- Step 1: 증상 정규화 ---
    step1 = _run_step("normalize", STEP1_NORMALIZE, full_input, model, opts)
    steps.append(step1)
    total_tokens += step1["tokens"]
    total_ms += step1["duration_ms"]

    # --- Step 2: 감별 진단 생성 ---
    step2_prompt = f"""[정규화된 증상]
{json.dumps(step1['parsed'], ensure_ascii=False, indent=2)}

[원문]
{patient_text}{specialty_hint}"""

    step2 = _run_step("differential", STEP2_DIFFERENTIAL, step2_prompt, model, opts)
    steps.append(step2)
    total_tokens += step2["tokens"]
    total_ms += step2["duration_ms"]

    # --- Step 3: 근거 기반 순위화 ---
    step3_prompt = f"""[정규화된 증상]
{json.dumps(step1['parsed'], ensure_ascii=False, indent=2)}

[감별진단 목록]
{json.dumps(step2['parsed'], ensure_ascii=False, indent=2)}"""

    step3 = _run_step("ranking", STEP3_RANKING, step3_prompt, model, opts)
    steps.append(step3)
    total_tokens += step3["tokens"]
    total_ms += step3["duration_ms"]

    # --- Step 4 & 5: 검사 제안 + 최종 평가 (병렬 실행) ---
    step4_prompt = f"""[순위화된 감별진단]
{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}

[환자 정보]
{full_input}"""

    step5_prompt = f"""[전체 추론 결과 요약]

1. 증상: {json.dumps(step1['parsed'], ensure_ascii=False)}

2. 감별진단 순위:
{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}

[원문] {patient_text}"""

    with ThreadPoolExecutor(max_workers=2) as pool:
        f4 = pool.submit(_run_step, "tests", STEP4_TESTS, step4_prompt, model, opts)
        f5 = pool.submit(_run_step, "assessment", STEP5_ASSESSMENT, step5_prompt, model, opts)
        step4 = f4.result()
        step5 = f5.result()

    steps.append(step4)
    steps.append(step5)
    total_tokens += step4["tokens"] + step5["tokens"]
    total_ms += max(step4["duration_ms"], step5["duration_ms"])  # 병렬이므로 max

    # 최종 평가에 환각 검증 추가
    from core.medical_validator import validate_medical_response
    validation = validate_medical_response(step5["parsed"], action="analyze")

    return {
        "chain_id": chain_id,
        "steps": [{
            "step": s["step"],
            "result": s["parsed"],
            "tokens": s["tokens"],
            "duration_ms": s["duration_ms"],
        } for s in steps],
        "final_assessment": step5["parsed"],
        "recommended_tests": step4["parsed"],
        "validation": validation,
        "total_tokens": total_tokens,
        "total_duration_ms": total_ms,
        "model": model,
        "specialty": specialty,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_reasoning_chain_stream(patient_text, *, model=None, specialty=None,
                                patient_info=None, options=None):
    """추론 체인 스트리밍 — 각 단계 완료 시 SSE 이벤트 전송

    Yields:
        SSE 형식 문자열: "data: {json}\n\n"
    """
    from routes.medical import get_heavy_model, SPECIALTY_HINTS

    chain_id = str(uuid.uuid4())
    if model is None:
        model = get_heavy_model()
    opts = options or {"temperature": 0.3, "top_p": 0.9}

    context_parts = [patient_text]
    if patient_info:
        info_lines = []
        if patient_info.get("age"):
            info_lines.append(f"나이: {patient_info['age']}세")
        if patient_info.get("sex"):
            info_lines.append(f"성별: {patient_info['sex']}")
        if patient_info.get("conditions"):
            info_lines.append(f"기저질환: {', '.join(patient_info['conditions'])}")
        if info_lines:
            context_parts.append("\n[환자 정보]\n" + "\n".join(info_lines))
    full_input = "\n".join(context_parts)

    specialty_hint = ""
    if specialty and specialty in SPECIALTY_HINTS:
        specialty_hint = SPECIALTY_HINTS[specialty]

    total_tokens = 0

    def _emit(event_type, data):
        return f"data: {json.dumps({'event': event_type, 'chain_id': chain_id, **data}, ensure_ascii=False)}\n\n"

    # Start
    yield _emit("chain_start", {"model": model, "steps_total": 5})

    # Step 1
    yield _emit("step_start", {"step": "normalize", "step_num": 1})
    step1 = _run_step("normalize", STEP1_NORMALIZE, full_input, model, opts)
    total_tokens += step1["tokens"]
    yield _emit("step_complete", {"step": "normalize", "result": step1["parsed"], "tokens": step1["tokens"], "duration_ms": step1["duration_ms"]})

    # Step 2
    yield _emit("step_start", {"step": "differential", "step_num": 2})
    step2_prompt = f"[정규화된 증상]\n{json.dumps(step1['parsed'], ensure_ascii=False, indent=2)}\n\n[원문]\n{patient_text}{specialty_hint}"
    step2 = _run_step("differential", STEP2_DIFFERENTIAL, step2_prompt, model, opts)
    total_tokens += step2["tokens"]
    yield _emit("step_complete", {"step": "differential", "result": step2["parsed"], "tokens": step2["tokens"], "duration_ms": step2["duration_ms"]})

    # Step 3
    yield _emit("step_start", {"step": "ranking", "step_num": 3})
    step3_prompt = f"[정규화된 증상]\n{json.dumps(step1['parsed'], ensure_ascii=False, indent=2)}\n\n[감별진단 목록]\n{json.dumps(step2['parsed'], ensure_ascii=False, indent=2)}"
    step3 = _run_step("ranking", STEP3_RANKING, step3_prompt, model, opts)
    total_tokens += step3["tokens"]
    yield _emit("step_complete", {"step": "ranking", "result": step3["parsed"], "tokens": step3["tokens"], "duration_ms": step3["duration_ms"]})

    # Step 4
    yield _emit("step_start", {"step": "tests", "step_num": 4})
    step4_prompt = f"[순위화된 감별진단]\n{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}\n\n[환자 정보]\n{full_input}"
    step4 = _run_step("tests", STEP4_TESTS, step4_prompt, model, opts)
    total_tokens += step4["tokens"]
    yield _emit("step_complete", {"step": "tests", "result": step4["parsed"], "tokens": step4["tokens"], "duration_ms": step4["duration_ms"]})

    # Step 5
    yield _emit("step_start", {"step": "assessment", "step_num": 5})
    step5_prompt = f"[전체 추론 결과 요약]\n\n1. 증상: {json.dumps(step1['parsed'], ensure_ascii=False)}\n\n2. 감별진단 순위:\n{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}\n\n[원문] {patient_text}"
    step5 = _run_step("assessment", STEP5_ASSESSMENT, step5_prompt, model, opts)
    total_tokens += step5["tokens"]
    yield _emit("step_complete", {"step": "assessment", "result": step5["parsed"], "tokens": step5["tokens"], "duration_ms": step5["duration_ms"]})

    # Validation
    from core.medical_validator import validate_medical_response
    validation = validate_medical_response(step5["parsed"], action="analyze")

    yield _emit("chain_complete", {
        "final_assessment": step5["parsed"],
        "recommended_tests": step4["parsed"],
        "validation": validation,
        "total_tokens": total_tokens,
    })


# ---------------------------------------------------------------------------
# Multi-Model Ensemble (chain_mode=multi)
# ---------------------------------------------------------------------------

# 앙상블 모델 설정
ENSEMBLE_MODELS = {
    "primary_ko": "werub-medical:7b",
    "primary_en": "thewindmom/llama3-med42-8b",
    "specialist": "meditron:latest",
}

STEP2_EN = """You are a differential diagnosis AI. Respond in English.

Based on the normalized symptom data below, generate 5-8 differential diagnoses in probability order.

Rules:
1. Common to uncommon order
2. Include ICD-10 codes
3. Emergency conditions (red flags) must be included regardless of probability
4. List supporting and against evidence

Output JSON array only:
[{"rank": 1, "diagnosis": "English name", "diagnosis_ko": "Korean name if known", "icd10": "code", "prior_probability": "high/medium/low", "supporting": ["evidence"], "against": ["counter-evidence"], "is_emergency": false}]"""


class DiagnosisNormalizer:
    """모델별 JSON 스키마를 표준 형식으로 변환"""

    @staticmethod
    def normalize(model_name, raw_diagnoses):
        """모델 출력을 표준 스키마로 정규화"""
        if not isinstance(raw_diagnoses, list):
            return []

        normalized = []
        for dx in raw_diagnoses:
            if not isinstance(dx, dict):
                continue
            entry = {
                "diagnosis": dx.get("diagnosis") or dx.get("diagnosis_en", ""),
                "diagnosis_ko": dx.get("diagnosis_ko") or dx.get("진단명", ""),
                "icd10": dx.get("icd10") or dx.get("icd_10") or dx.get("code", ""),
                "confidence": dx.get("confidence") or dx.get("prior_probability", "medium"),
                "supporting": dx.get("supporting") or dx.get("evidence", []),
                "is_emergency": dx.get("is_emergency", False),
                "model": model_name,
            }
            normalized.append(entry)
        return normalized

    @staticmethod
    def merge_votes(all_diagnoses):
        """여러 모델의 진단을 합집합 + 투표로 병합"""
        # ICD-10 코드 기준으로 그룹핑 (없으면 진단명)
        vote_map = {}
        for dx in all_diagnoses:
            key = dx.get("icd10") or dx.get("diagnosis", "").lower()
            if not key:
                continue
            if key not in vote_map:
                vote_map[key] = {
                    "diagnosis": dx["diagnosis"],
                    "diagnosis_ko": dx["diagnosis_ko"],
                    "icd10": dx["icd10"],
                    "supporting": [],
                    "is_emergency": False,
                    "models": [],
                    "vote_count": 0,
                }
            entry = vote_map[key]
            entry["vote_count"] += 1
            entry["models"].append(dx["model"])
            if dx.get("is_emergency"):
                entry["is_emergency"] = True
            # diagnosis_ko 보충
            if not entry["diagnosis_ko"] and dx.get("diagnosis_ko"):
                entry["diagnosis_ko"] = dx["diagnosis_ko"]
            if not entry["diagnosis"] and dx.get("diagnosis"):
                entry["diagnosis"] = dx["diagnosis"]
            # 근거 합산
            for s in (dx.get("supporting") or []):
                if s not in entry["supporting"]:
                    entry["supporting"].append(s)

        # 투표 수 + 응급 가중치로 정렬
        sorted_dx = sorted(vote_map.values(),
                           key=lambda x: (x["is_emergency"], x["vote_count"]),
                           reverse=True)

        # confidence 결정
        total_models = len(set(dx["model"] for dx in all_diagnoses))
        for dx in sorted_dx:
            ratio = dx["vote_count"] / max(total_models, 1)
            if ratio >= 0.8:
                dx["confidence"] = "high"
            elif ratio >= 0.5:
                dx["confidence"] = "medium"
            else:
                dx["confidence"] = "low"

        return sorted_dx


def _build_single_context(patient_text, patient_info):
    """single 모드용 환자 컨텍스트 — multi/consensus와 동일 구성."""
    context_parts = [patient_text]
    if patient_info:
        info_lines = []
        if patient_info.get("age"):
            info_lines.append(f"나이: {patient_info['age']}세")
        if patient_info.get("sex"):
            info_lines.append(f"성별: {patient_info['sex']}")
        if patient_info.get("conditions"):
            info_lines.append(f"기저질환: {', '.join(patient_info['conditions'])}")
        if info_lines:
            context_parts.append("\n[환자 정보]\n" + "\n".join(info_lines))
    return "\n".join(context_parts)


def run_ensemble_chain(patient_text, *, chain_mode="multi", patient_info=None,
                        options=None, model=None, depth="full"):
    """Multi-Model Ensemble 추론 체인

    chain_mode:
        - "single": 단일 모델 (스왑 0 — 컨시어지/속도 우선)
        - "multi": 단계별 전문 모델 (werub→Med42→werub, 스왑 2회)
        - "consensus": 감별진단 3모델 투표 (순차, 스왑 4회)

    model: single 모드 단일 모델 (None → diagnosis 1순위 werub-medical:7b).
           multi/consensus에서는 무시(단계별 고정 모델 사용).
    depth: single 모드 단계 깊이.
        - "full"(기본): normalize→differential→ranking→tests+assessment (5단계)
        - "triage": normalize→differential→assessment (3단계, ranking·tests 생략 — 컨시어지 속도)
           multi/consensus에서는 무시(항상 full).
    """
    from routes.medical import get_heavy_model

    chain_id = str(uuid.uuid4())
    opts = options or {"temperature": 0.3, "top_p": 0.9}
    model_ko = ENSEMBLE_MODELS["primary_ko"]
    model_en = ENSEMBLE_MODELS["primary_en"]

    # --- single: 단일 모델 (모델 스왑 제거); depth로 단계 축소 ---
    if chain_mode == "single":
        m = model or model_ko
        full_input = _build_single_context(patient_text, patient_info)
        s1 = _run_step("normalize", STEP1_NORMALIZE, full_input, m, opts)
        s2 = _run_step("differential", STEP2_DIFFERENTIAL,
                       f"[정규화된 증상]\n{json.dumps(s1['parsed'], ensure_ascii=False, indent=2)}\n\n[원문]\n{patient_text}",
                       m, opts)

        if depth == "triage":
            # 3단계 경량: ranking·tests 생략, assessment는 감별진단 직접 사용
            s5_prompt = f"[전체 추론 결과]\n\n1. 증상: {json.dumps(s1['parsed'], ensure_ascii=False)}\n\n2. 감별진단:\n{json.dumps(s2['parsed'], ensure_ascii=False, indent=2)}\n\n[원문] {patient_text}"
            s5 = _run_step("assessment", STEP5_ASSESSMENT, s5_prompt, m, opts)
            single_steps = [s1, s2, s5]
            tests_result = None
            total_ms_single = s1["duration_ms"] + s2["duration_ms"] + s5["duration_ms"]
        else:
            s3 = _run_step("ranking", STEP3_RANKING,
                           f"[정규화된 증상]\n{json.dumps(s1['parsed'], ensure_ascii=False, indent=2)}\n\n[감별진단 목록]\n{json.dumps(s2['parsed'], ensure_ascii=False, indent=2)}",
                           m, opts)
            s4_prompt = f"[순위화된 감별진단]\n{json.dumps(s3['parsed'], ensure_ascii=False, indent=2)}\n\n[환자 정보]\n{full_input}"
            s5_prompt = f"[전체 추론 결과]\n\n1. 증상: {json.dumps(s1['parsed'], ensure_ascii=False)}\n\n2. 감별진단:\n{json.dumps(s3['parsed'], ensure_ascii=False, indent=2)}\n\n[원문] {patient_text}"
            with ThreadPoolExecutor(max_workers=2) as pool:
                f4 = pool.submit(_run_step, "tests", STEP4_TESTS, s4_prompt, m, opts)
                f5 = pool.submit(_run_step, "assessment", STEP5_ASSESSMENT, s5_prompt, m, opts)
                s4 = f4.result()
                s5 = f5.result()
            single_steps = [s1, s2, s3, s4, s5]
            tests_result = s4["parsed"]
            total_ms_single = (s1["duration_ms"] + s2["duration_ms"] + s3["duration_ms"]
                               + max(s4["duration_ms"], s5["duration_ms"]))

        from core.medical_validator import validate_medical_response
        validation = validate_medical_response(s5["parsed"], action="analyze")
        return {
            "chain_id": chain_id,
            "chain_mode": "single",
            "depth": depth,
            "steps": [{"step": s["step"], "result": s["parsed"], "tokens": s["tokens"],
                       "duration_ms": s["duration_ms"]} for s in single_steps],
            "final_assessment": s5["parsed"],
            "recommended_tests": tests_result,
            "validation": validation,
            "total_tokens": sum(s["tokens"] for s in single_steps),
            "total_duration_ms": total_ms_single,
            "models_used": {"normalize": m, "differential": m, "ranking": m, "tests_assessment": m},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # 환자 컨텍스트 구성
    context_parts = [patient_text]
    if patient_info:
        info_lines = []
        if patient_info.get("age"):
            info_lines.append(f"나이: {patient_info['age']}세")
        if patient_info.get("sex"):
            info_lines.append(f"성별: {patient_info['sex']}")
        if patient_info.get("conditions"):
            info_lines.append(f"기저질환: {', '.join(patient_info['conditions'])}")
        if info_lines:
            context_parts.append("\n[환자 정보]\n" + "\n".join(info_lines))
    full_input = "\n".join(context_parts)

    steps = []
    total_tokens = 0
    total_ms = 0
    normalizer = DiagnosisNormalizer()

    # --- Step 1: 증상 정규화 (KO model) ---
    step1 = _run_step("normalize", STEP1_NORMALIZE, full_input, model_ko, opts)
    steps.append(step1)
    total_tokens += step1["tokens"]
    total_ms += step1["duration_ms"]

    # 영어 증상 요약 추출
    symptom_summary_en = ""
    if isinstance(step1["parsed"], dict):
        symptoms = step1["parsed"].get("symptoms", [])
        demographics = step1["parsed"].get("demographics", {})
        parts = []
        if demographics.get("age"):
            parts.append(f"{demographics['age']}-year-old")
        if demographics.get("sex"):
            parts.append(f"{demographics['sex']}")
        parts.append("presenting with")
        symptom_names = [s.get("name_en", s.get("name", "")) for s in symptoms if isinstance(s, dict)]
        parts.append(", ".join(symptom_names[:5]))
        symptom_summary_en = " ".join(parts)

    if chain_mode == "consensus":
        # --- Consensus: 3모델 순차 감별진단 → 투표 ---
        all_dx = []

        # Model 1: werub (KO)
        step2a = _run_step("differential_ko", STEP2_DIFFERENTIAL,
                           f"[정규화된 증상]\n{json.dumps(step1['parsed'], ensure_ascii=False)}\n[원문]\n{patient_text}",
                           model_ko, opts)
        steps.append(step2a)
        total_tokens += step2a["tokens"]
        total_ms += step2a["duration_ms"]
        all_dx.extend(normalizer.normalize(model_ko, step2a["parsed"] if isinstance(step2a["parsed"], list) else []))

        # Model 2: Med42 (EN)
        step2b = _run_step("differential_en", STEP2_EN,
                           f"Patient: {symptom_summary_en}\n\nNormalized symptoms:\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                           model_en, opts)
        steps.append(step2b)
        total_tokens += step2b["tokens"]
        total_ms += step2b["duration_ms"]
        all_dx.extend(normalizer.normalize(model_en, step2b["parsed"] if isinstance(step2b["parsed"], list) else []))

        # Model 3: meditron (EN) — 있으면
        try:
            model_spec = ENSEMBLE_MODELS["specialist"]
            step2c = _run_step("differential_spec", STEP2_EN,
                               f"Patient: {symptom_summary_en}\n\nSymptoms:\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                               model_spec, opts)
            steps.append(step2c)
            total_tokens += step2c["tokens"]
            total_ms += step2c["duration_ms"]
            all_dx.extend(normalizer.normalize(model_spec, step2c["parsed"] if isinstance(step2c["parsed"], list) else []))
        except Exception as e:
            logger.warning(f"Specialist model failed: {e}")

        # 투표 병합
        merged = normalizer.merge_votes(all_dx)
        consensus_result = {
            "merged_diagnoses": merged[:8],
            "models_used": list(set(dx.get("model", "") for dx in all_dx)),
            "total_candidates": len(all_dx),
            "unique_diagnoses": len(merged),
        }

    else:
        # --- Multi: 단계별 전문 모델 ---
        # Step 2: Med42로 감별진단 (EN)
        step2 = _run_step("differential", STEP2_EN,
                          f"Patient: {symptom_summary_en}\n\nNormalized symptoms:\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                          model_en, opts)
        steps.append(step2)
        total_tokens += step2["tokens"]
        total_ms += step2["duration_ms"]
        merged = step2["parsed"] if isinstance(step2["parsed"], list) else []
        consensus_result = None

    # --- Step 3: 근거 기반 순위화 (Med42 연속) ---
    step3_input = merged if consensus_result else (step2["parsed"] if not consensus_result else merged)
    step3 = _run_step("ranking", STEP3_RANKING,
                      f"[감별진단]\n{json.dumps(step3_input, ensure_ascii=False, indent=2)}\n\n[증상]\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                      model_en, opts)
    steps.append(step3)
    total_tokens += step3["tokens"]
    total_ms += step3["duration_ms"]

    # --- Step 4 & 5: 검사 + 최종 평가 (werub KO) ---
    step4_prompt = f"[순위화된 감별진단]\n{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}\n\n[환자 정보]\n{full_input}"
    step5_prompt = f"[전체 추론 결과]\n\n1. 증상: {json.dumps(step1['parsed'], ensure_ascii=False)}\n\n2. 감별진단:\n{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}\n\n[원문] {patient_text}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        f4 = pool.submit(_run_step, "tests", STEP4_TESTS, step4_prompt, model_ko, opts)
        f5 = pool.submit(_run_step, "assessment", STEP5_ASSESSMENT, step5_prompt, model_ko, opts)
        step4 = f4.result()
        step5 = f5.result()

    steps.extend([step4, step5])
    total_tokens += step4["tokens"] + step5["tokens"]
    total_ms += max(step4["duration_ms"], step5["duration_ms"])

    # 환각 검증
    from core.medical_validator import validate_medical_response
    validation = validate_medical_response(step5["parsed"], action="analyze")

    result = {
        "chain_id": chain_id,
        "chain_mode": chain_mode,
        "steps": [{
            "step": s["step"],
            "result": s["parsed"],
            "tokens": s["tokens"],
            "duration_ms": s["duration_ms"],
        } for s in steps],
        "final_assessment": step5["parsed"],
        "recommended_tests": step4["parsed"],
        "validation": validation,
        "total_tokens": total_tokens,
        "total_duration_ms": total_ms,
        "models_used": {
            "normalize": model_ko,
            "differential": f"{model_en} + {model_ko}" if chain_mode == "consensus" else model_en,
            "ranking": model_en,
            "tests_assessment": model_ko,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if consensus_result:
        result["consensus"] = consensus_result

    return result


def run_ensemble_chain_stream(patient_text, *, chain_mode="multi", patient_info=None,
                               options=None, model=None, depth="full"):
    """Multi-Model Ensemble 추론 체인 — SSE 스트리밍 (단계별 실시간 방출)

    run_ensemble_chain 과 동일 로직·동일 모델 경로를 각 단계 완료 시 SSE 이벤트로
    방출한다. 비스트림(/ensemble) 대비 30~90s 블롭 대기를 제거해 컨시어지가
    단계별로 결과를 채울 수 있다(체감 지연 ↓). chain_mode="single"은 단일 모델로
    5단계를 돌려 모델 스왑을 제거(서버 전체 경합 ↓)한다.

    Yields:
        SSE 형식 문자열: "data: {json}\\n\\n"
    """
    chain_id = str(uuid.uuid4())
    opts = options or {"temperature": 0.3, "top_p": 0.9}
    model_ko = ENSEMBLE_MODELS["primary_ko"]
    model_en = ENSEMBLE_MODELS["primary_en"]

    full_input = _build_single_context(patient_text, patient_info)

    steps = []
    total_tokens = 0
    total_ms = 0
    normalizer = DiagnosisNormalizer()

    def _emit(event_type, data):
        return f"data: {json.dumps({'event': event_type, 'chain_id': chain_id, **data}, ensure_ascii=False)}\n\n"

    # --- single: 단일 모델 (모델 스왑 제거); depth로 단계 축소 ---
    if chain_mode == "single":
        m = model or model_ko
        is_triage = (depth == "triage")
        yield _emit("chain_start", {"chain_mode": "single", "depth": depth,
                                    "steps_total": 3 if is_triage else 5, "models": {"single": m}})

        yield _emit("step_start", {"step": "normalize", "step_num": 1, "model": m})
        s1 = _run_step("normalize", STEP1_NORMALIZE, full_input, m, opts)
        total_tokens += s1["tokens"]; total_ms += s1["duration_ms"]
        yield _emit("step_complete", {"step": "normalize", "result": s1["parsed"],
                                      "tokens": s1["tokens"], "duration_ms": s1["duration_ms"]})

        yield _emit("step_start", {"step": "differential", "step_num": 2, "model": m})
        s2 = _run_step("differential", STEP2_DIFFERENTIAL,
                       f"[정규화된 증상]\n{json.dumps(s1['parsed'], ensure_ascii=False, indent=2)}\n\n[원문]\n{patient_text}",
                       m, opts)
        total_tokens += s2["tokens"]; total_ms += s2["duration_ms"]
        yield _emit("step_complete", {"step": "differential", "result": s2["parsed"],
                                      "tokens": s2["tokens"], "duration_ms": s2["duration_ms"]})

        if is_triage:
            # 3단계 경량: ranking·tests 생략, assessment는 감별진단 직접 사용
            s5_prompt = f"[전체 추론 결과]\n\n1. 증상: {json.dumps(s1['parsed'], ensure_ascii=False)}\n\n2. 감별진단:\n{json.dumps(s2['parsed'], ensure_ascii=False, indent=2)}\n\n[원문] {patient_text}"
            yield _emit("step_start", {"step": "assessment", "step_num": 3, "model": m})
            s5 = _run_step("assessment", STEP5_ASSESSMENT, s5_prompt, m, opts)
            total_tokens += s5["tokens"]; total_ms += s5["duration_ms"]
            yield _emit("step_complete", {"step": "assessment", "result": s5["parsed"],
                                          "tokens": s5["tokens"], "duration_ms": s5["duration_ms"]})
            tests_result = None
        else:
            yield _emit("step_start", {"step": "ranking", "step_num": 3, "model": m})
            s3 = _run_step("ranking", STEP3_RANKING,
                           f"[정규화된 증상]\n{json.dumps(s1['parsed'], ensure_ascii=False, indent=2)}\n\n[감별진단 목록]\n{json.dumps(s2['parsed'], ensure_ascii=False, indent=2)}",
                           m, opts)
            total_tokens += s3["tokens"]; total_ms += s3["duration_ms"]
            yield _emit("step_complete", {"step": "ranking", "result": s3["parsed"],
                                          "tokens": s3["tokens"], "duration_ms": s3["duration_ms"]})

            s4_prompt = f"[순위화된 감별진단]\n{json.dumps(s3['parsed'], ensure_ascii=False, indent=2)}\n\n[환자 정보]\n{full_input}"
            s5_prompt = f"[전체 추론 결과]\n\n1. 증상: {json.dumps(s1['parsed'], ensure_ascii=False)}\n\n2. 감별진단:\n{json.dumps(s3['parsed'], ensure_ascii=False, indent=2)}\n\n[원문] {patient_text}"
            yield _emit("step_start", {"step": "tests", "step_num": 4, "model": m})
            yield _emit("step_start", {"step": "assessment", "step_num": 5, "model": m})
            with ThreadPoolExecutor(max_workers=2) as pool:
                f4 = pool.submit(_run_step, "tests", STEP4_TESTS, s4_prompt, m, opts)
                f5 = pool.submit(_run_step, "assessment", STEP5_ASSESSMENT, s5_prompt, m, opts)
                s4 = f4.result()
                s5 = f5.result()
            total_tokens += s4["tokens"] + s5["tokens"]
            total_ms += max(s4["duration_ms"], s5["duration_ms"])
            yield _emit("step_complete", {"step": "tests", "result": s4["parsed"],
                                          "tokens": s4["tokens"], "duration_ms": s4["duration_ms"]})
            yield _emit("step_complete", {"step": "assessment", "result": s5["parsed"],
                                          "tokens": s5["tokens"], "duration_ms": s5["duration_ms"]})
            tests_result = s4["parsed"]

        from core.medical_validator import validate_medical_response
        validation = validate_medical_response(s5["parsed"], action="analyze")
        yield _emit("chain_complete", {
            "chain_mode": "single",
            "depth": depth,
            "final_assessment": s5["parsed"],
            "recommended_tests": tests_result,
            "validation": validation,
            "total_tokens": total_tokens,
            "total_duration_ms": total_ms,
            "models_used": {"normalize": m, "differential": m, "ranking": m, "tests_assessment": m},
        })
        return

    steps_total = 7 if chain_mode == "consensus" else 5
    yield _emit("chain_start", {
        "chain_mode": chain_mode,
        "steps_total": steps_total,
        "models": {"ko": model_ko, "en": model_en, "specialist": ENSEMBLE_MODELS["specialist"]},
    })

    # --- Step 1: 증상 정규화 (KO model) ---
    yield _emit("step_start", {"step": "normalize", "step_num": 1, "model": model_ko})
    step1 = _run_step("normalize", STEP1_NORMALIZE, full_input, model_ko, opts)
    steps.append(step1)
    total_tokens += step1["tokens"]
    total_ms += step1["duration_ms"]
    yield _emit("step_complete", {"step": "normalize", "result": step1["parsed"],
                                  "tokens": step1["tokens"], "duration_ms": step1["duration_ms"]})

    # 영어 증상 요약 추출
    symptom_summary_en = ""
    if isinstance(step1["parsed"], dict):
        symptoms = step1["parsed"].get("symptoms", [])
        demographics = step1["parsed"].get("demographics", {})
        parts = []
        if demographics.get("age"):
            parts.append(f"{demographics['age']}-year-old")
        if demographics.get("sex"):
            parts.append(f"{demographics['sex']}")
        parts.append("presenting with")
        symptom_names = [s.get("name_en", s.get("name", "")) for s in symptoms if isinstance(s, dict)]
        parts.append(", ".join(symptom_names[:5]))
        symptom_summary_en = " ".join(parts)

    consensus_result = None
    if chain_mode == "consensus":
        all_dx = []

        # Model 1: werub (KO)
        yield _emit("step_start", {"step": "differential_ko", "step_num": 2, "model": model_ko})
        step2a = _run_step("differential_ko", STEP2_DIFFERENTIAL,
                           f"[정규화된 증상]\n{json.dumps(step1['parsed'], ensure_ascii=False)}\n[원문]\n{patient_text}",
                           model_ko, opts)
        steps.append(step2a)
        total_tokens += step2a["tokens"]
        total_ms += step2a["duration_ms"]
        all_dx.extend(normalizer.normalize(model_ko, step2a["parsed"] if isinstance(step2a["parsed"], list) else []))
        yield _emit("step_complete", {"step": "differential_ko", "result": step2a["parsed"],
                                      "tokens": step2a["tokens"], "duration_ms": step2a["duration_ms"]})

        # Model 2: Med42 (EN)
        yield _emit("step_start", {"step": "differential_en", "step_num": 3, "model": model_en})
        step2b = _run_step("differential_en", STEP2_EN,
                           f"Patient: {symptom_summary_en}\n\nNormalized symptoms:\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                           model_en, opts)
        steps.append(step2b)
        total_tokens += step2b["tokens"]
        total_ms += step2b["duration_ms"]
        all_dx.extend(normalizer.normalize(model_en, step2b["parsed"] if isinstance(step2b["parsed"], list) else []))
        yield _emit("step_complete", {"step": "differential_en", "result": step2b["parsed"],
                                      "tokens": step2b["tokens"], "duration_ms": step2b["duration_ms"]})

        # Model 3: meditron (EN) — 있으면
        try:
            model_spec = ENSEMBLE_MODELS["specialist"]
            yield _emit("step_start", {"step": "differential_spec", "step_num": 4, "model": model_spec})
            step2c = _run_step("differential_spec", STEP2_EN,
                               f"Patient: {symptom_summary_en}\n\nSymptoms:\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                               model_spec, opts)
            steps.append(step2c)
            total_tokens += step2c["tokens"]
            total_ms += step2c["duration_ms"]
            all_dx.extend(normalizer.normalize(model_spec, step2c["parsed"] if isinstance(step2c["parsed"], list) else []))
            yield _emit("step_complete", {"step": "differential_spec", "result": step2c["parsed"],
                                          "tokens": step2c["tokens"], "duration_ms": step2c["duration_ms"]})
        except Exception as e:
            logger.warning(f"Specialist model failed: {e}")
            yield _emit("step_skipped", {"step": "differential_spec", "reason": str(e)[:160]})

        merged = normalizer.merge_votes(all_dx)
        consensus_result = {
            "merged_diagnoses": merged[:8],
            "models_used": list(set(dx.get("model", "") for dx in all_dx)),
            "total_candidates": len(all_dx),
            "unique_diagnoses": len(merged),
        }
    else:
        # --- Multi: 단계별 전문 모델 (Step 2: Med42 감별진단 EN) ---
        yield _emit("step_start", {"step": "differential", "step_num": 2, "model": model_en})
        step2 = _run_step("differential", STEP2_EN,
                          f"Patient: {symptom_summary_en}\n\nNormalized symptoms:\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                          model_en, opts)
        steps.append(step2)
        total_tokens += step2["tokens"]
        total_ms += step2["duration_ms"]
        merged = step2["parsed"] if isinstance(step2["parsed"], list) else []
        yield _emit("step_complete", {"step": "differential", "result": step2["parsed"],
                                      "tokens": step2["tokens"], "duration_ms": step2["duration_ms"]})

    # --- Step 3: 근거 기반 순위화 (Med42 연속) ---
    ranking_num = 5 if chain_mode == "consensus" else 3
    yield _emit("step_start", {"step": "ranking", "step_num": ranking_num, "model": model_en})
    step3 = _run_step("ranking", STEP3_RANKING,
                      f"[감별진단]\n{json.dumps(merged, ensure_ascii=False, indent=2)}\n\n[증상]\n{json.dumps(step1['parsed'], ensure_ascii=False)}",
                      model_en, opts)
    steps.append(step3)
    total_tokens += step3["tokens"]
    total_ms += step3["duration_ms"]
    yield _emit("step_complete", {"step": "ranking", "result": step3["parsed"],
                                  "tokens": step3["tokens"], "duration_ms": step3["duration_ms"]})

    # --- Step 4 & 5: 검사 + 최종 평가 (werub KO, 병렬) ---
    tests_num = 6 if chain_mode == "consensus" else 4
    assess_num = 7 if chain_mode == "consensus" else 5
    step4_prompt = f"[순위화된 감별진단]\n{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}\n\n[환자 정보]\n{full_input}"
    step5_prompt = f"[전체 추론 결과]\n\n1. 증상: {json.dumps(step1['parsed'], ensure_ascii=False)}\n\n2. 감별진단:\n{json.dumps(step3['parsed'], ensure_ascii=False, indent=2)}\n\n[원문] {patient_text}"

    yield _emit("step_start", {"step": "tests", "step_num": tests_num, "model": model_ko})
    yield _emit("step_start", {"step": "assessment", "step_num": assess_num, "model": model_ko})
    with ThreadPoolExecutor(max_workers=2) as pool:
        f4 = pool.submit(_run_step, "tests", STEP4_TESTS, step4_prompt, model_ko, opts)
        f5 = pool.submit(_run_step, "assessment", STEP5_ASSESSMENT, step5_prompt, model_ko, opts)
        step4 = f4.result()
        step5 = f5.result()

    steps.extend([step4, step5])
    total_tokens += step4["tokens"] + step5["tokens"]
    total_ms += max(step4["duration_ms"], step5["duration_ms"])
    yield _emit("step_complete", {"step": "tests", "result": step4["parsed"],
                                  "tokens": step4["tokens"], "duration_ms": step4["duration_ms"]})
    yield _emit("step_complete", {"step": "assessment", "result": step5["parsed"],
                                  "tokens": step5["tokens"], "duration_ms": step5["duration_ms"]})

    # 환각 검증
    from core.medical_validator import validate_medical_response
    validation = validate_medical_response(step5["parsed"], action="analyze")

    complete_payload = {
        "chain_mode": chain_mode,
        "final_assessment": step5["parsed"],
        "recommended_tests": step4["parsed"],
        "validation": validation,
        "total_tokens": total_tokens,
        "total_duration_ms": total_ms,
        "models_used": {
            "normalize": model_ko,
            "differential": f"{model_en} + {model_ko}" if chain_mode == "consensus" else model_en,
            "ranking": model_en,
            "tests_assessment": model_ko,
        },
    }
    if consensus_result:
        complete_payload["consensus"] = consensus_result
    yield _emit("chain_complete", complete_payload)
