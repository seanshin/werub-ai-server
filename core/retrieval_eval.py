"""검색(Retrieval) 품질 측정 — context precision + 근거 충분성 (v2.124.0).

현 스택은 생성 충실도(faithfulness)만 측정 — 검색 결함(v2.114.1 제21조 rank 탈락,
의안 게이트)은 충실도 하락을 통해 간접·수동으로만 드러났다. 본 모듈이 검색 자체를
직접 측정해 "검색이 나쁜지 생성이 나쁜지"를 배치에서 분리한다(RAGAS 계열 개념).

- **context_precision**: 검색된 청크 중 질문 관련 비율 — 청크별 judge 이진 판정
  (노이즈 지표: 낮으면 무관 청크가 컨텍스트를 희석).
- **context_sufficiency**: 이 근거만으로 질문에 완전히 답할 수 있는가
  (yes=1.0 / partial=0.5 / no=0.0 — 커버리지 지표: 낮으면 검색 누락).
judge=상주 qwen2.5:14b + format 제약 디코딩(faithfulness와 동일 패턴·외부 API 0).
ask_json 주입 가능(테스트). 집계는 순수 로직 분리.
"""
from __future__ import annotations

import json
import os

JUDGE_MODEL = os.environ.get("RETRIEVAL_JUDGE_MODEL", "qwen2.5:14b")
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MAX_CHUNKS = 10          # 판정 상한(청크 과다 시 judge 호출 폭주 방지)
_CHUNK_CLIP = 1800       # 청크 판정 입력 상한(문자)

# 청크별 **개별 호출**(단건 boolean) — 10청크 일괄 번호 판정은 라이브 검증서 전량 False
# 오판(정답 청크 포함, 2026-07-07 실측) = 긴 다중 문서 인덱스 매핑에 judge 불안정.
_REL_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "boolean"}},
    "required": ["relevant"],
}

_SUFF_SCHEMA = {
    "type": "object",
    "properties": {"sufficiency": {"type": "string", "enum": ["yes", "partial", "no"]}},
    "required": ["sufficiency"],
}

# 부정 방향 질문("거부할 수 있는가")이 허용 요건 조문을 무관 오판하던 프레이밍 편향
# (07-22 id277 실측: 동일 청크 5개가 부정 질문 0/5·긍정 재구성 3/5 — 법에서 거부 가능
# 여부의 근거는 허용 의무·요건 조문 그 자체) → 방향 무관 판정 지시를 명시.
# ⚠️시스템 프롬프트 지시만으로는 "~할 수 있는 경우는?"(사유·요건 열거형) 문형에서
# 판정이 안 뒤집힘(07-27 id355 실측: 제15조 진료거부 금지 조각이 시스템 지시
# 강화판에서도 5/5 False) — 같은 지시를 청크별 유저 프롬프트에도 병기해야 반영됨
# (병기 후 5/5 True·무관 디코이 3종 False 유지·id277 회귀 없음). 프롬프트 참조.
_REL_SYS = ("당신은 검색 품질 평가관입니다. 각 문서 조각이 질문에 답하는 데 "
            "직접적으로 관련된 내용을 담고 있는지 엄격히 판정하세요. "
            "주제만 비슷하고 질문에 기여하지 않으면 관련 없음(false)입니다. "
            "질문이 가능 여부·거부 여부·금지 여부를 묻는 경우, 그 판단의 법적 근거가 "
            "되는 요건·의무·허용·금지 규정을 담은 조각은 서술 방향이 질문과 반대여도 "
            "관련 있음(true)입니다.")

# 유저 프롬프트 병기용 방향 무관 지시(위 주석 참조 — 시스템 단독으론 미반영).
_REL_DIRECTION_NOTE = (
    "주의: 질문이 허용·거부·면제되는 경우나 요건을 물을 때, 그 행위의 원칙적 "
    "금지·의무를 규정한 조문은 예외 경우를 판단하는 법적 근거이므로 관련 있음입니다.")

_SUFF_SYS = ("당신은 검색 품질 평가관입니다. 제공된 근거만으로 질문에 완전하고 "
             "정확하게 답할 수 있는지 판정하세요 — yes(완전)/partial(일부만)/no(불가).")


def _default_ask_json(system: str, prompt: str, schema: dict,
                      model: str = None, timeout: int = 180) -> dict:
    """format 제약 디코딩 호출 → dict. v2.125.12(D1 슬라이스 1) task 라우터 경유 — 이관은 변경이 아니다:
    페이로드는 종전 직접 호출과 동일(model·system·prompt·stream·format·options) — 회귀 `tests/test_router_migration_slice1.py`."""
    from core import model_router
    r = model_router.generate("eval.retrieval_judge", prompt, model=model or JUDGE_MODEL,
                              system=system, schema=schema,
                              options={"temperature": 0, "num_predict": 512}, timeout=timeout)
    if not r.ok:
        raise RuntimeError(f"[eval.retrieval_judge] 생성 실패: {r.error}")   # 종전 raise_for_status와 같은 층
    return json.loads(r.text or "{}")


def judge_chunk_relevance(question: str, chunks: list, ask_json=None,
                          model: str = None) -> list:
    """청크 리스트 → [True/False/None] 관련성 — 청크별 개별 판정(상한 MAX_CHUNKS).

    None=판정 불능(호출 실패/비정형) — 집계 분모에서 제외(무관으로 오집계 방지)."""
    chunks = list(chunks or [])[:MAX_CHUNKS]
    if not chunks:
        return []
    ask = ask_json or _default_ask_json
    out = []
    for c in chunks:
        prompt = ("[질문]\n%s\n\n[문서 조각]\n%s\n\n"
                  "이 조각이 질문에 답하는 데 직접 관련되는지 relevant(true/false)를 판정하세요. "
                  "%s"
                  % (question.strip(), (c or "")[:_CHUNK_CLIP], _REL_DIRECTION_NOTE))
        try:
            v = ask(_REL_SYS, prompt, _REL_SCHEMA, model=model).get("relevant")
            out.append(bool(v) if isinstance(v, bool) else None)
        except Exception:  # noqa: BLE001
            out.append(None)
    return out


def judge_sufficiency(question: str, evidence: str, ask_json=None,
                      model: str = None) -> float | None:
    """근거 전체로 질문에 답 가능한가 → 1.0/0.5/0.0. 판독 불능 None."""
    ask = ask_json or _default_ask_json
    # 방향 무관 병기: 원칙 금지 조문만 있어도 허용 경우 판단 기준은 제공됨 —
    # 현행 프롬프트는 no(0.0) 판정(07-27 id355 실측 3/3), 병기 후 partial 3/3.
    prompt = ("[질문]\n%s\n\n[근거]\n%s\n\n이 근거만으로 질문에 완전히 답할 수 있는지 "
              "sufficiency를 판정하세요. 주의: 질문이 허용·거부되는 경우를 물을 때 "
              "그 행위의 원칙적 금지·요건 규정은 판단의 법적 근거입니다 — 근거가 "
              "판단 기준을 제공하면 partial 이상으로 판정하세요."
              % (question.strip(), (evidence or "").strip()[:12000]))
    out = ask(_SUFF_SYS, prompt, _SUFF_SCHEMA, model=model)
    return {"yes": 1.0, "partial": 0.5, "no": 0.0}.get(out.get("sufficiency"))


def aggregate(relevance: list, sufficiency) -> dict:
    """판정 → 집계(순수). precision=관련/판정된 청크(None=판정불능 제외).
    판정된 청크 0개=측정불가(None)."""
    judged = [r for r in relevance if r is not None]
    rel = sum(1 for r in judged if r)
    return {"ctx_chunks": len(relevance), "ctx_relevant": rel,
            "ctx_precision": round(rel / len(judged), 3) if judged else None,
            "ctx_sufficiency": sufficiency}


def score(question: str, chunks: list, ask_json=None, model: str = None) -> dict:
    """질문+검색 청크 → 검색 품질 리포트(집계+청크별 판정)."""
    relevance = judge_chunk_relevance(question, chunks, ask_json=ask_json, model=model)
    evidence = "\n".join((c or "") for c in (chunks or [])[:MAX_CHUNKS])
    suff = judge_sufficiency(question, evidence, ask_json=ask_json, model=model)
    out = aggregate(relevance, suff)
    out["chunks"] = [{"excerpt": (c or "")[:200], "relevant": r}
                     for c, r in zip(chunks, relevance)]
    return out
