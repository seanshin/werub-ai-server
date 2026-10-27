"""RAG 답변 충실도(groundedness) 채점 — LLM-as-judge (LLM 평가 Phase 2).

P0 측정에서 HHEM-2.1-Open NO-GO(512토큰 한계로 다중청크 한국어 법령 근거 절단 +
한국어 변별 불안정) → 로컬 LLM judge로 전환. 외부 API 0.

RAGAS-style 2단계:
  ① extract_claims(answer)      — 답변을 원자적 사실 주장으로 분해(면책/권고 제외)
  ② judge_claims(claims, ev)    — 각 주장을 근거 대비 판정 supported/unsupported/contradicted
  → faithfulness = supported/total, hallucination = contradicted 존재 or 지지율 < threshold

judge = 상주 qwen2.5:14b(스왑 0) + Ollama `format` 제약 디코딩(Phase 1 검증 parse_fail=0).
ask_json 주입 가능(테스트·모델 교체). 순수 집계 로직은 judge와 분리(단위테스트 가능).
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import json
import logging
import os
import re

logger = logging.getLogger(__name__)


def _judge_model() -> str:
    """judge 모델 — config `eval_judge_model(s)` → env → 기본값(v2.124.95).
    모듈 로드 시점에 고정하지 않는다: config 무재배포 전환을 살리기 위함.
    실패해도 기본값으로 떨어져 평가가 멈추지 않는다."""
    try:
        from core.judge_config import judge_model
        return judge_model("faithfulness")
    except Exception:  # noqa: BLE001
        return os.environ.get("FAITH_JUDGE_MODEL", "qwen2.5:14b")


# 하위호환 — 기존 참조를 위해 남긴다(값은 로드 시점 스냅샷).
JUDGE_MODEL = _judge_model()
# 교차검증 백엔드 — v2.124.13: bespoke-minicheck(CC BY-NC 4.0 비상업)를 기본에서
# 제거하고 Apache-2.0 qwen-entail로 교체. PoC 12페어(실증 난문 포함) 실측:
# qwen2.5:7b 12/12 · qwen2.5:14b 12/12 · bespoke-minicheck 11/12(패러프레이즈 실패)
# — scripts/poc_verifier_backend.py. 프로토콜(청크 분할·max-over-chunks)·mc_* 지표
# 스키마는 불변, 백엔드만 교체(EVAL_GUIDE 추세 주석 참조). judge=14b와 다른
# 체크포인트(7b)를 기본으로 유지해 교차검증의 모델 이종성 확보.
VERIFIER_BACKEND = os.environ.get("FAITH_VERIFIER", "qwen-entail")  # "minicheck"=레거시(NC) 명시 opt-in
VERIFIER_MODEL = os.environ.get("FAITH_VERIFIER_MODEL", "qwen2.5:7b")
MINICHECK_MODEL = os.environ.get("FAITH_MINICHECK_MODEL", "bespoke-minicheck")
# ⚠️ 이관(v2.124.96) 후 이 모듈은 직접 HTTP를 부르지 않는다 — 전송은 core/model_router.
#    상수는 하위호환(외부 참조·진단)용으로만 남긴다.
OLLAMA_BASE = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
DEFAULT_THRESHOLD = 0.5

VERDICTS = ("supported", "unsupported", "contradicted")

_CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {"type": "string"}}},
    "required": ["claims"],
}

_VERDICTS_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"i": {"type": "integer"},
                       "verdict": {"type": "string", "enum": list(VERDICTS)}},
        "required": ["i", "verdict"],
    }}},
    "required": ["verdicts"],
}

_EXTRACT_SYS = ("당신은 텍스트에서 검증 가능한 사실적 주장(factual claim)을 추출하는 전문가입니다. "
                "면책문구·일반 권고(예: '전문의와 상담하세요')·인사말은 제외하고, "
                "각 주장을 독립적으로 이해 가능한 한 문장으로 만드세요. "
                "**근거 자료 자체를 언급하는 메타 진술**(예: '벌칙은 근거에서 확인되지 "
                "않습니다', '자료에 명시되어 있지 않습니다')은 세상에 대한 사실 주장이 "
                "아니므로 **제외**하세요. 모든 주장은 **반드시 한국어**로 작성하세요.")

# 근거 자체를 가리키는 메타 진술 — 근거로 '지지'될 수 없어 구조적으로 unsupported가 되므로
# 충실도를 왜곡한다(의료법 규칙2가 "벌칙이 근거에 없으면 없다고 말하라"고 요구한 결과가
# 감점되던 클래스). 메타 명사 + 미확인 술어가 **함께** 있을 때만 제외 = 실세계 부정 주장
# ("보존 의무가 없다" 등)은 그대로 판정 대상으로 남긴다.
_META_NOUN = re.compile(r"(근거|자료|문서|출처|제공된 정보|정보|내용)")
_META_NEG = re.compile(r"(확인(할 수 없|되지 않|이 되지 않)|명시(되어 있지 않|되지 않|이 없)"
                       r"|나와 있지 않|나타나 있지 않|찾을 수 없|포함되어 있지 않)")


def _is_meta_claim(claim: str) -> bool:
    """근거 자료에 대한 메타 진술이면 True(주장에서 제외)."""
    return bool(_META_NOUN.search(claim) and _META_NEG.search(claim))


def _is_korean(text: str) -> bool:
    """한글이 한자·가나보다 우세해야 한국어로 인정(언어 드리프트 검출).

    존재 검사(한글 1자라도 있으면 통과)는 한글 몇 자로 시작하는 혼합 드리프트
    ("대리수蛉は回答中的…")를 통과시킨 실측(07-22 id275) → 비율 판정.
    한자 병기("의료법(醫療法)")는 한글 우세라 통과, 한자·가나 없는 텍스트도 통과.
    """
    hangul = len(re.findall(r"[가-힣]", text))
    foreign = len(re.findall(r"[一-鿿ぁ-ヿ]", text))
    return hangul >= foreign


# 주장 분해가 답변에 없는 수치를 만들면(07-30 id399: 답변 "3천만원"→주장 "7천만원")
# 그 주장은 답변을 대변하지 못하고, judge가 근거 대비 contradicted로 판정해
# hallucination 위양성 플래그를 만든다. 표기 차이("3천만"↔"3,000만")는 값으로
# 정규화해 비교 — 답변 수치 집합의 부분집합일 때만 통과.
# '조'는 법조문 "제N조"와 충돌(제5조→5조=10^12 오해석)하고 이 코퍼스에 조 단위
# 금액이 없어 스케일에서 제외. 조문 번호 자체는 2자리 이상이면 맨숫자로 비교된다.
_NUM_TOKEN = re.compile(r"\d[\d,.]*[천만억]*")
_SCALE = {"천": 10 ** 3, "만": 10 ** 4, "억": 10 ** 8}


def _num_values(text: str) -> set:
    """텍스트의 유의 수치 토큰을 값 집합으로 정규화("3천만"과 "3,000만"은 같은 값).

    맨숫자 1자리(목록 번호·"주장1" 등)는 변별력 없는 노이즈라 제외 —
    2자리 이상 또는 천/만/억/조 접미가 붙은 토큰만 비교 대상.
    """
    vals = set()
    for tok in _NUM_TOKEN.findall(text or ""):
        m = re.match(r"([\d,.]+)([천만억]*)", tok)
        num = m.group(1).replace(",", "").rstrip(".")
        if not m.group(2) and len(num.replace(".", "")) < 2:
            continue
        try:
            v = float(num)
        except ValueError:
            continue
        for ch in m.group(2):
            v *= _SCALE[ch]
        vals.add(v)
    return vals

_JUDGE_SYS = ("당신은 주장이 근거 문서에 의해 지지되는지 엄격히 판정하는 검증관입니다. "
              "근거에 명시되었거나 직접 도출되는 내용만 supported로 인정하고, "
              "근거에 없으면 unsupported, 근거와 모순되면 contradicted로 판정하세요.")


def _route(task: str, prompt: str, *, model=None, system=None, schema=None,
           options=None, timeout: int = 180) -> str:
    """task 라우터 경유 생성 → 응답 텍스트. (v2.124.96 이관·외부 API 0)

    🔴 **평가 경로는 강등하지 않는다.** 프로덕션(`medical_law._generate`)은 라우터가
    죽으면 구 경로로 내려가지만, 여기서 같은 짓을 하면 **측정 도구가 조용히 다른
    구성으로 돌아간다**. 그건 이 프로젝트가 v2.124.85~89 다섯 릴리즈에 걸쳐 고친
    바로 그 결함 클래스다(실패 캐시·무성 스킵·근거 무성 절단). 크게 실패시킨다.

    ⚠️ `model`을 안 주면 라우팅 표가 `judge_config`에서 읽는다 — 정본은 한 곳이다.
    """
    return _route_full(task, prompt, model=model, system=system, schema=schema,
                       options=options, timeout=timeout).text or ""


def _route_full(task, prompt, *, model=None, system=None, schema=None, options=None,
                timeout=180, logprobs=False, top_logprobs=None):
    """`_route`와 **같은 호출·같은 실패 규율**인데 `Result`를 통째로 돌려준다.

    P6-1 Phase 0에서 `raw.logprobs`가 필요해 생겼다. `_route`는 이 함수의 얇은
    래퍼이므로 **기존 호출자 4곳의 동작·계약은 변하지 않는다**.

    🔴 여기서도 강등하지 않는다 — 실패는 던진다(측정 도구가 조용히 다른 구성으로
    돌아가는 것이 이 모듈이 금지하는 바로 그 결함 클래스다).
    """
    from core import model_router
    r = model_router.generate(task, prompt, model=model, system=system, schema=schema,
                              options=options, timeout=timeout,
                              logprobs=logprobs, top_logprobs=top_logprobs)
    if not r.ok:
        # 종전 `raise_for_status()`와 같은 층 — HTTP 오류를 빈 응답으로 흘리지 않는다.
        raise RuntimeError(f"[{task}] 생성 실패: {r.error}")
    return r


def _default_ask_json(system: str, prompt: str, schema: dict,
                      model: str = None, timeout: int = 180) -> dict:
    """Ollama format 제약 디코딩 호출 → 파싱된 dict. (외부 API 0)"""
    text = _route("eval.faithfulness_judge", prompt, model=model, system=system,
                  schema=schema, options={"temperature": 0, "num_predict": 1024},
                  timeout=timeout)
    return json.loads(text or "{}")


def extract_claims(answer: str, ask_json=None, model: str = None) -> list:
    """답변 → 원자적 사실 주장 리스트.

    프롬프트 지시만으론 불충분한 실측(v2.124.6 교훈)이라 결정적 후처리를 병행:
    ①근거에 대한 메타 진술 제외 ②한국어 이탈 시 1회 재추출(드리프트 주장은 교차검증
    verifier와 언어가 어긋나 mc_conflicts 위양성을 만든다) ③답변에 없는 수치를 담은
    주장 제외(분해가 수치를 변조하면 답변이 정확해도 contradicted 위양성 — 07-30 id399).
    답변 자체의 오수치는 주장에도 같은 값이 실려 통과하므로 실제 환각 검출은 불변.
    """
    ask = ask_json or _default_ask_json
    prompt = ("다음 답변에서 검증 가능한 사실적 주장을 원자 단위로 추출하세요.\n\n"
              "[답변]\n%s" % (answer or "").strip())

    def _ask_once():
        out = ask(_EXTRACT_SYS, prompt, _CLAIMS_SCHEMA, model=model)
        raw = out.get("claims") or []
        return [c.strip() for c in raw if isinstance(c, str) and c.strip()]

    answer_nums = _num_values(answer or "")

    def _faithful(c):
        return _is_korean(c) and _num_values(c) <= answer_nums

    claims = _ask_once()
    if claims and not all(_faithful(c) for c in claims):
        retried = _ask_once()                       # 드리프트·수치 변조 1회 재시도(개선 시만 채택)
        if retried and sum(_faithful(c) for c in retried) > sum(_faithful(c) for c in claims):
            claims = retried
    # 재시도 후에도 남은 드리프트·수치 변조 주장은 제외 — 답변을 대변하지 못하는
    # 분해 아티팩트라 judge/verifier 위양성만 만든다(07-22 id275·07-30 id399 실측).
    return [c for c in claims if not _is_meta_claim(c) and _faithful(c)]


def judge_claims(claims: list, evidence: str, ask_json=None, model: str = None,
                 verify_flagged: bool = True) -> list:
    """주장 리스트 × 근거 → [{'claim','verdict'}]. 판정 누락/이상값은 unsupported(보수).

    ⚠️ **일괄(번호) 판정은 정답 주장을 떨어뜨린다** — 2026-08-04 실측: 근거 원문과 **문자 그대로
    일치**하는 주장(제21조① "정당한 사유가 없으면 이를 거부하여서는 아니 된다")이 일괄 판정에서
    contradicted로 나왔고, **같은 주장을 단독으로 주면 supported 5/5**였다. A/B(R=3)에서 두
    답변은 **3/3회 결정적으로** 재현됐다(일괄 비지지 1건 ↔ 개별 비지지 0건).
    `v2.124.0`이 검색 평가에서 이미 고친 클래스("10청크 일괄 번호 판정은 정답 청크까지 오판")가
    여기 남아 있었다 — id451의 "재발"은 생성 결함이 아니라 이 하네스 결함이었다.

    처치=**비지지로 나온 주장만 개별 재판정**(전건 개별 판정은 호출 수가 주장 수만큼 늘고,
    실측상 대부분은 supported라 이득이 없다). 통제 검증: 진짜 오류 주장은 개별 판정에서도
    contradicted 5/5라 재판정이 실오류를 지우지 않는다.
    """
    if not claims:
        return []
    ask = ask_json or _default_ask_json
    numbered = "\n".join("%d. %s" % (i, c) for i, c in enumerate(claims, 1))
    prompt = ("[근거 문서]\n%s\n\n[주장 목록]\n%s\n\n"
              "각 주장(i=1~%d)에 대해 verdict를 판정하세요."
              % ((evidence or "").strip(), numbered, len(claims)))
    out = ask(_JUDGE_SYS, prompt, _VERDICTS_SCHEMA, model=model)
    by_idx = {}
    for v in out.get("verdicts") or []:
        try:
            i = int(v.get("i"))
        except (TypeError, ValueError):
            continue
        verdict = v.get("verdict")
        if 1 <= i <= len(claims) and verdict in VERDICTS:
            by_idx[i] = verdict
    judged = [{"claim": c, "verdict": by_idx.get(i, "unsupported")}
              for i, c in enumerate(claims, 1)]

    if verify_flagged and len(claims) > 1:
        for j in judged:
            if j["verdict"] == "supported":
                continue
            try:
                solo = judge_claims([j["claim"]], evidence, ask_json=ask_json,
                                    model=model, verify_flagged=False)
            except Exception:
                continue                      # 재판정 실패 시 일괄 판정 유지(보수)
            if solo and solo[0].get("verdict") in VERDICTS:
                j["verdict"] = solo[0]["verdict"]
    return judged


def aggregate(judged: list, threshold: float = DEFAULT_THRESHOLD) -> dict:
    """판정 리스트 → 충실도 집계 (순수 로직, judge 무관)."""
    n = len(judged)
    supported = sum(1 for j in judged if j.get("verdict") == "supported")
    contradicted = sum(1 for j in judged if j.get("verdict") == "contradicted")
    unsupported = n - supported - contradicted
    faithfulness = round(supported / n, 3) if n else None
    hallucination = bool(contradicted) or (faithfulness is not None and faithfulness < threshold)
    return {"n_claims": n, "n_supported": supported, "n_unsupported": unsupported,
            "n_contradicted": contradicted, "faithfulness": faithfulness,
            "hallucination": hallucination, "threshold": threshold}


def score(answer: str, evidence: str, ask_json=None, model: str = None,
          threshold: float = DEFAULT_THRESHOLD) -> dict:
    """답변+근거 → 충실도 리포트. 주장 0개(순수 면책 등)=측정불가(None)·환각 아님."""
    claims = extract_claims(answer, ask_json=ask_json, model=model)
    judged = judge_claims(claims, evidence, ask_json=ask_json, model=model)
    result = aggregate(judged, threshold=threshold)
    result["claims"] = judged
    # 법령명 오귀속 — **결정적 계산**이다. 충실도 judge는 주장의 *내용*만 근거와
    # 대조하고 *어느 법률의 조문인가*는 판정 축에 없다(2026-08-18 실측: 오귀속 8건 중
    # 7건이 faith 1.000·환각 없음으로 통과). judge=의미 / 메트릭=형식 분업과 같다.
    try:
        from core.law_attribution import misattributed as _misattr
        result["law_misattributed"] = _misattr(answer, evidence)
    except Exception:  # noqa: BLE001 — 부가 축이 충실도 산출을 막아선 안 된다
        result["law_misattributed"] = []
    # 후단·단서 오독(E1 · v2.125.15) — 답변의 "(… 제외)"가 근거에선 허용인 경우.
    # id728②가 faith 1.000으로 통과한 사각. **관측 축**이다(탐지만·게이트 아님) —
    # 처치는 관측이 끝난 뒤 별건(기획서 §3-E1: 탐지기→관측→처치 순서).
    try:
        from core.proviso_misread import misread_exclusions as _misread
        result["proviso_misread"] = _misread(answer, evidence)
    except Exception:  # noqa: BLE001
        result["proviso_misread"] = []
    return result


# ---------------------------------------------------------------------------
# MiniCheck 교차검증 — 검증 특화 소형모델로 주장별 2차 의견(judge와 독립 계열)
# ---------------------------------------------------------------------------

# MiniCheck는 **청크 단위 검증**이 정식 프로토콜(논문·Ollama 가이드 공히 "chunks of
# factual information") — 장문 근거(~4K tok)를 통째로 주면 Yes/No 대신 설명문으로 드리프트
# (done_reason=length, GPU 실측). HHEM을 NO-GO시킨 512tok 절단과 같은 클래스지만,
# MiniCheck는 청크별 판정 후 max(어느 청크든 지지=supported)로 설계상 해결된다.
_MC_CHUNK_CHARS = 1500


def _split_evidence(evidence: str, max_chars: int = _MC_CHUNK_CHARS) -> list:
    """근거를 문단 경계 우선으로 max_chars 이하 청크로 분할(순수)."""
    evidence = (evidence or "").strip()
    if len(evidence) <= max_chars:
        return [evidence] if evidence else []
    parts, cur = [], ""
    for para in evidence.split("\n"):
        if cur and len(cur) + len(para) + 1 > max_chars:
            parts.append(cur)
            cur = para
        else:
            cur = (cur + "\n" + para) if cur else para
        # 단일 문단이 과대하면 하드 컷
        while len(cur) > max_chars:
            parts.append(cur[:max_chars])
            cur = cur[max_chars:]
    if cur:
        parts.append(cur)
    return parts


def _default_minicheck(evidence_chunk: str, claim: str, model: str = None,
                       timeout: int = 120):
    """bespoke-minicheck 1청크 호출 → True(supported)/False. 판독 불능은 예외."""
    ans = _route("eval.verifier_minicheck",
                 f"Document: {evidence_chunk}\nClaim: {claim}",
                 model=model, options={"temperature": 0, "num_predict": 4},
                 timeout=timeout).strip().lower()
    if ans.startswith("yes"):
        return True
    if ans.startswith("no"):
        return False
    raise ValueError(f"minicheck 비정형 응답: {ans[:20]}")


_ENTAIL_SYS = ("당신은 주장이 근거 문서만으로 지지되는지 판정하는 엄격한 검증관입니다. "
               "근거에 명시되었거나 근거의 조항들을 결합해 직접 도출되는 경우에만 supported=true. "
               "근거에 없거나, 조·항 번호가 다르거나, 근거의 요건·한정조건을 빠뜨린 주장은 "
               "supported=false.")
_ENTAIL_SCHEMA = {"type": "object",
                  "properties": {"supported": {"type": "boolean"}},
                  "required": ["supported"]}


def _qwen_entail_check(evidence_chunk: str, claim: str, model: str = None,
                       timeout: int = 120):
    """qwen 계열 format 제약 entailment 1청크 호출 → True/False. 판독 불능은 예외.

    **반환 계약 불변**(bool). 확률까지 필요하면 `_qwen_entail_check_ex`를 쓴다 —
    `checker` 프로토콜(`check(chunk, claim, model=...) -> bool`)을 쓰는 기존
    호출자·회귀를 깨지 않기 위해 갈라 놓았다(P6-1 §4-3a).
    """
    return _qwen_entail_check_ex(evidence_chunk, claim, model=model, timeout=timeout)[0]


def _qwen_entail_check_ex(evidence_chunk: str, claim: str, model: str = None,
                          timeout: int = 120, top_logprobs: int = 5):
    """`_qwen_entail_check` + 판정 확률. → `(bool, P(supported) | None)`

    확률은 `top_logprobs`에서 뽑는다(제약 디코딩에서 선택 토큰 자신의 logprob은
    신뢰할 수 없다 — `core/decision_prob` 참조). **엔진이 logprobs를 안 주거나
    라벨이 안 잡히면 `None`**이고, 판정(bool)은 종전과 똑같이 나온다 ⇒ 확률 축이
    죽어도 충실도 측정은 그대로 돈다(fail-open).
    """
    from core.decision_prob import prob_of
    r = _route_full("eval.verifier",
                    (f"[근거 문서]\n{evidence_chunk}\n\n[주장]\n{claim}\n\n"
                     "이 주장이 근거 문서만으로 지지됩니까?"),
                    model=model, system=_ENTAIL_SYS, schema=_ENTAIL_SCHEMA,
                    options={"temperature": 0, "num_predict": 32}, timeout=timeout,
                    logprobs=True, top_logprobs=top_logprobs)
    text = r.text or ""
    prob = prob_of((r.raw or {}).get("logprobs"), "true")
    out = json.loads(text or "{}")
    if "supported" not in out:
        raise ValueError("entail 비정형 응답")
    return bool(out["supported"]), prob


def _default_checker():
    """백엔드 선택 — 기본 qwen-entail(Apache-2.0), 'minicheck'=레거시 명시 opt-in(NC)."""
    return _default_minicheck if VERIFIER_BACKEND == "minicheck" else _qwen_entail_check


def minicheck_verify(claims: list, evidence: str, checker=None, model: str = None,
                     want_prob: bool = False):
    """주장 리스트 → [True/False/None]. 근거는 청크 분할 후 **어느 청크든 지지=True**
    (MiniCheck 프로토콜의 max-over-chunks). 전 청크 판독 불능 주장은 None(비차단).
    함수명은 스키마 안정성 위해 유지 — 실제 백엔드는 VERIFIER_BACKEND가 결정.

    `want_prob=True`(P6-1 Phase 0)면 `(verdicts, probs)`를 돌려준다. **기본값에서는
    반환 형태·호출 모두 종전과 완전히 동일**하다 — `checker` 프로토콜을 쓰는 기존
    호출자와 회귀 4건이 그대로 통과해야 한다.

    확률의 청크 집계는 max-over-chunks 판정과 정합시킨다:
      - `verdict=True`  → **지지로 판정한 그 청크**의 `P(supported)`(루프가 break한 지점)
      - `verdict=False` → 전 청크 `P(supported)`의 **최댓값**(가장 아슬아슬했던 값)
        🔑 *확신한 부정*과 *간신히 부정*을 가르는 것이 이 축의 실사용 값이다
      - `verdict=None`  → None
    확률만 못 뽑힌 청크는 판정에 영향을 주지 않는다(확률 None으로 건너뛴다).
    """
    if want_prob and checker is None and VERIFIER_BACKEND != "minicheck":
        check_ex = _qwen_entail_check_ex
    else:
        check_ex = None
        if want_prob:
            # 커스텀 checker·minicheck 백엔드는 확률을 못 준다 — **거절이 아니라 축 null**.
            logger.info("[faithfulness] want_prob 요청됐으나 백엔드가 확률 미지원 — 확률 축 null")
    check = checker or _default_checker()
    chunks = _split_evidence(evidence)
    out, probs = [], []
    for c in claims:
        verdict = None  # None=판독 불능, False=전 청크 명시적 no, True=어느 청크든 yes
        best = None     # verdict에 대응하는 P(supported)
        for ch in chunks:
            try:
                if check_ex is not None:
                    ok, p = check_ex(ch, c, model=model)
                else:
                    ok, p = check(ch, c, model=model), None
                if ok:
                    verdict = True
                    best = p          # 지지 판정을 낸 바로 그 청크의 확률
                    break
                verdict = False if verdict is None else verdict
                if p is not None:
                    best = p if best is None else max(best, p)
            except Exception:  # noqa: BLE001 — 해당 청크만 생략
                continue
        out.append(verdict)
        probs.append(None if verdict is None else best)
    return (out, probs) if want_prob else out


def merge_cross_check(judged: list, mc_results: list) -> dict:
    """judge 판정 + minicheck 결과 병합(순수 로직).

    각 주장에 `minicheck`(supported/not_supported/None) 필드 추가, 집계:
      - mc_agree: 판정 가능한 주장 중 양측 일치율(supported↔yes, 나머지↔no)
      - mc_conflicts: 불일치 수 — **리뷰 트리거**(어느 쪽이 옳다는 단정 아님)
      - strict_faithfulness: 양측 모두 supported인 주장 비율(보수 지표, 추세 KPI와 별도)
    faithfulness(주 KPI)는 불변 — 교차검증은 세밀 진단 축."""
    agree = conflicts = strict = judged_n = 0
    for j, m in zip(judged, mc_results):
        j["minicheck"] = None if m is None else ("supported" if m else "not_supported")
        if m is None:
            continue
        judged_n += 1
        js = j.get("verdict") == "supported"
        if js == m:
            agree += 1
        else:
            conflicts += 1
        if js and m:
            strict += 1
    n = len(judged)
    return {"mc_checked": judged_n,
            "mc_agree": round(agree / judged_n, 3) if judged_n else None,
            "mc_conflicts": conflicts,
            "strict_faithfulness": round(strict / n, 3) if n and judged_n else None}


def cross_check(result: dict, evidence: str, checker=None, model: str = None,
                want_prob: bool = True) -> dict:
    """score() 결과에 minicheck 교차검증 병합(제자리 갱신 후 반환).

    `want_prob=True`(P6-1 Phase 0 기본)면 주장별 `entail_prob`을 함께 싣는다.
    확률을 못 뽑으면 **그 주장만 None**이고 교차검증 판정·집계는 종전과 동일하다
    (축이 죽어도 주 KPI는 안 흔들린다).
    """
    judged = result.get("claims") or []
    claims = [j["claim"] for j in judged]
    probs = None
    if want_prob:
        mc, probs = minicheck_verify(claims, evidence, checker=checker, model=model,
                                     want_prob=True)
    else:
        mc = minicheck_verify(claims, evidence, checker=checker, model=model)
    result.update(merge_cross_check(judged, mc))
    if probs is not None:
        for j, pr in zip(judged, probs):
            j["entail_prob"] = None if pr is None else round(pr, 4)
        result.update(summarize_entail_prob(probs))
    return result


def summarize_entail_prob(probs) -> dict:
    """주장별 확률 → 집계. **결측은 분모에서 뺀다**(0으로 세면 축이 깨끗해 보인다).

    반환:
      entail_prob_n       확률이 잡힌 주장 수
      entail_prob_mean    평균(없으면 None)
      entail_prob_low     🔑 **0.35~0.65 구간 주장 수 — 판정자가 망설인 건**.
                          이 축의 실사용 값이고, 원문을 열어볼 대상을 지목한다.
      entail_prob_missing 확률 미검출 수(미측정 — "0건"과 다르다)
    """
    vals = [p for p in (probs or []) if p is not None]
    n = len(vals)
    return {"entail_prob_n": n,
            "entail_prob_mean": round(sum(vals) / n, 4) if n else None,
            "entail_prob_low": sum(1 for p in vals if 0.35 <= p <= 0.65),
            "entail_prob_missing": len([p for p in (probs or []) if p is None])}
