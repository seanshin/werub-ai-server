"""SelfCheckGPT-style 무참조 환각 검출 — 비-RAG 개방형 답변용 (LLM 평가 후속).

원리(Manakul et al., SelfCheckGPT LLM-prompt 변형): 같은 질의를 온도>0으로 N회 재샘플
→ 원답변의 각 주장이 샘플들과 얼마나 일관되는지 측정. 모델이 아는 사실은 샘플 간
일관·모르는(지어낸) 내용은 발산 → 낮은 일관성 = 환각 신호. **참조문서 불요**라
RAG 근거가 없는 경로(상담·일반챗)를 커버(RAG 경로는 core.faithfulness 전담).

주장 분해·판정은 core.faithfulness 재사용(extract_claims/judge_claims — 샘플을
'근거'로 보고 supported 여부 판정). judge=상주 qwen2.5:14b·외부 API 0.
집계는 순수 로직(단위테스트 가능). 샘플 생성만 Ollama 호출(배치 측정 시점).
"""
from __future__ import annotations

import os

from core import faithfulness as _F

SAMPLE_MODEL = os.environ.get("SELFCHECK_SAMPLE_MODEL", "qwen2.5:14b")
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_N = 3
DEFAULT_THRESHOLD = 0.5   # 주장별: 샘플 과반이 지지 못 하면 환각 의심


def sample_answers(prompt: str, n: int = DEFAULT_N, model: str = None,
                   system: str = "", temperature: float = 0.8, timeout: int = 180) -> list:
    """같은 질의 N회 확률적 재샘플(온도>0). 측정 시점에만 호출(Ollama N회)."""
    from core import model_router   # v2.125.12 D1 슬라이스 1 — 페이로드 동일(회귀 test_router_migration_slice1)
    outs = []
    for _ in range(max(1, n)):
        r = model_router.generate("eval.selfcheck_sample", prompt, model=model or SAMPLE_MODEL,
                                  system=system,
                                  options={"temperature": temperature, "num_predict": 768},
                                  timeout=timeout)
        if not r.ok:
            raise RuntimeError(f"[eval.selfcheck_sample] 생성 실패: {r.error}")  # 종전 raise_for_status와 같은 층
        txt = (r.text or "").strip()
        if txt:
            outs.append(txt)
    return outs


def aggregate(per_sample_verdicts: list, n_claims: int,
              threshold: float = DEFAULT_THRESHOLD) -> dict:
    """샘플별 판정 리스트([[{claim,verdict}]×n_samples]) → 주장별 일관성 집계 (순수).

    claim_consistency[i] = 주장 i를 supported로 판정한 샘플 비율.
    consistency = 주장별 평균. suspect_claims = 일관성<threshold 주장 인덱스.
    """
    n_samples = len(per_sample_verdicts)
    if not n_claims or not n_samples:
        return {"n_claims": n_claims, "n_samples": n_samples, "consistency": None,
                "claim_consistency": [], "suspect_claims": [], "hallucination_risk": False,
                "threshold": threshold}
    support = [0] * n_claims
    for judged in per_sample_verdicts:
        for i, j in enumerate(judged[:n_claims]):
            if j.get("verdict") == "supported":
                support[i] += 1
    claim_cons = [round(s / n_samples, 3) for s in support]
    consistency = round(sum(claim_cons) / n_claims, 3)
    suspects = [i for i, c in enumerate(claim_cons) if c < threshold]
    return {"n_claims": n_claims, "n_samples": n_samples, "consistency": consistency,
            "claim_consistency": claim_cons, "suspect_claims": suspects,
            "hallucination_risk": bool(suspects), "threshold": threshold}


def score(answer: str, samples: list, ask_json=None, model: str = None,
          threshold: float = DEFAULT_THRESHOLD) -> dict:
    """원답변 × 재샘플들 → 일관성 리포트. faithfulness의 분해/판정 재사용."""
    claims = _F.extract_claims(answer, ask_json=ask_json, model=model)
    # 2026-08-04 측정 후 활성화 — SelfCheck에도 일괄 판정 오염이 **있다**(R=3 결정적 재현):
    #   일괄 비지지 8 → 개별 검증 7 · 일관성 0.467 → **0.533**(위험 판정은 불변)
    # RAG 맥락(플래그 47% 소멸)보다 약한데, 여기서 "근거"는 다른 샘플 답변(~600자)이라
    # 6,000자 다중 청크보다 번호 혼동이 적기 때문으로 보인다.
    # 방향이 중요하다 — 일관성을 낮게 재면 **환각 위험을 과다 경보**한다(안전 신호 역방향).
    # 대가는 호출 증가(샘플 수 × 플래그 주장)이나, 본 모듈은 옵트인 진단 경로다.
    per_sample = [_F.judge_claims(claims, s, ask_json=ask_json, model=model, verify_flagged=True)
                  for s in samples]
    result = aggregate(per_sample, len(claims), threshold=threshold)
    result["claims"] = [
        {"claim": c, "consistency": result["claim_consistency"][i] if i < len(result["claim_consistency"]) else None}
        for i, c in enumerate(claims)]
    return result


def check(prompt: str, answer: str = None, n: int = DEFAULT_N, model: str = None,
          system: str = "", ask_json=None, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """전체 파이프라인: (answer 미제공 시 1회 생성) + N 재샘플 + 일관성 채점."""
    if answer is None:
        first = sample_answers(prompt, n=1, model=model, system=system, temperature=0.0)
        answer = first[0] if first else ""
    samples = sample_answers(prompt, n=n, model=model, system=system)
    result = score(answer, samples, ask_json=ask_json, model=model, threshold=threshold)
    result["answer"] = answer
    return result
