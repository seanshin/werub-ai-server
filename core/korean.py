"""한국어 필터 + 품질 검증"""
import re
import hashlib
import time
import logging

logger = logging.getLogger("ollama-admin")

# 중국어/일본어/CJK 구두점 제거 패턴
_CJK_CHARS = re.compile("[\u4E00-\u9FFF\u3400-\u4DBF\uF900-\uFAFF]+")
_CJK_PUNCT = re.compile("[\u3000-\u303F\uFF01-\uFF0F\uFF1A-\uFF20\uFF3B-\uFF40\uFF5B-\uFF65]+")
_JAPANESE = re.compile("[\u3040-\u309F\u30A0-\u30FF]+")
_ENGLISH_ACTION = re.compile(r"\*[a-zA-Z][a-zA-Z ,.:;!?\'\-]{2,}\*")
_KOREAN = re.compile("[\uAC00-\uD7AF]")


def force_korean(text, observe=True):
    """Remove non-Korean text from LLM responses (preserve spacing)

    v2.125.53: 입력의 CJK(한자·가나) 비율이 표류 임계를 넘으면 **호출 위치와 함께 경고 로그** —
    필터가 표류분을 조용히 걷어내 뼈대만 남기는 것(story 실측 30%)을 모든 경로에서 보이게 한다.
    동작(반환값)은 불변.

    observe=False: 관측 로그를 끈다(반환값 동일). **출력을 내보내지 않고 판정에만 필터를 쓰는 곳** 전용 —
    예) `routes/writing._cacheable`. 그 판정은 모든 writing 생성에 돌아서 ①한국어 경로에선 호출자의 필터가
    같은 표류를 **한 번 더** 기록했고(중복) ②ja·zh 번역에선 **가나·한자가 정답인데** 표류로 기록했다
    (2026-09-30 게이트마다 `writing:121 share=0.929 len=14` = 일본어 번역 케이스 — 탐지기 위양성, 클래스 #2).
    ⚠️ 사용자에게 나가는 출력을 거르는 곳에서 끄면 표류 소실이 다시 안 보이게 된다.
    """
    if not text:
        return text
    if observe:
        _log_drift_input(text)
    text = _ENGLISH_ACTION.sub(" ", text)
    text = _JAPANESE.sub("", text)
    text = _CJK_CHARS.sub("", text)
    text = _CJK_PUNCT.sub("", text)
    # Remove lines with no Korean content
    filtered = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            filtered.append(line)
            continue
        korean = len(_KOREAN.findall(stripped))
        if korean > 0 or len(stripped) <= 5:
            filtered.append(line)
    text = "\n".join(filtered)
    # LaTeX to text
    text = re.sub(r"\\\[\\s*", "", text)
    text = re.sub(r"\\s*\\\]", "", text)
    text = re.sub(r"\\text\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\times", "\u00d7", text)
    text = re.sub(r"\\div", "\u00f7", text)
    text = re.sub(r"\\frac\{([^}]+)\}\{([^}]+)\}", r"\1/\2", text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    text = re.sub(r"[$]{1,2}", "", text)
    text = re.sub(r"  +", " ", text)
    return text


def _log_drift_input(text):
    try:
        share = cjk_share(text)
        if share > CJK_DRIFT_THRESHOLD:
            import sys
            f = sys._getframe(2)   # force_korean을 부른 쪽
            logger.warning("[korean-filter] CJK 표류 입력 share=%.3f len=%d caller=%s:%d",
                           share, len(text), f.f_globals.get("__name__", "?"), f.f_lineno)
    except Exception:  # noqa: BLE001 — 관측이 필터를 막지 않는다
        pass


def force_korean_json(data):
    """JSON 내 한국어 텍스트 필드에만 선별적으로 force_korean 적용.

    - dict/list를 재귀 탐색
    - 한국어가 포함된 10자 이상 문자열에만 적용
    - ICD 코드, 약물명, enum 값 등 영문 전용 필드는 보호
    """
    if isinstance(data, dict):
        return {k: force_korean_json(v) for k, v in data.items()}
    if isinstance(data, list):
        return [force_korean_json(item) for item in data]
    if isinstance(data, str) and len(data) >= 10 and _KOREAN.search(data):
        return force_korean(data)
    return data


def force_korean_token(token):
    """Token-level filter for streaming"""
    if not token:
        return token
    return _JAPANESE.sub("", token)


def sanitize_stream_store(text):
    """스트림 저장분(캐시·세션) 정화 — 문자 단위만, 줄 삭제 없음 (2026-08-26).

    force_korean을 저장분에 그대로 쓰면 한글 없는 줄(코드 블록·영어 답변)이 통째로
    사라지고, 전량 삭제 시 full_text=""라 저장 자체가 스킵돼 캐시 미스·세션 이력
    소실이 났다(실측 분기 4/8 — references/chat-stream-cache-divergence-2026-08-26.md).
    여기는 모델이 혼입하는 한자·가나·CJK 구두점만 걷어낸다(#8 정화 효용 보존 —
    가나 제거는 force_korean_token과 같은 강도. 요청된 일본어까지 지워지는 것은
    알려진 트레이드오프로 유지). 정화가 전부 비우면 raw를 돌려준다(저장 스킵 방지).
    """
    if not text:
        return text
    out = _JAPANESE.sub("", text)
    out = _CJK_CHARS.sub("", out)
    out = _CJK_PUNCT.sub("", out)
    return out if out.strip() else text


def korean_quality_check(text):
    """응답 한글 품질 검증 — (passed, ratio, detail)"""
    if not text or not text.strip():
        return False, 0.0, "empty"
    cleaned = text.strip()
    korean = len(_KOREAN.findall(cleaned))
    alpha = len(re.findall("[a-zA-Z]", cleaned))
    digits = len(re.findall("[0-9]", cleaned))
    total = korean + alpha + digits
    if total == 0:
        return False, 0.0, "no_content"
    ratio = korean / total
    if len(cleaned) < 5:
        return False, ratio, "too_short"
    if ratio < 0.3:
        return False, ratio, f"low_korean_{ratio:.0%}"
    return True, ratio, "ok"


KOREAN_RETRY_SUFFIX = (
    "\n\n[중요] 반드시 한국어로만 답변하세요. "
    "중국어, 일본어, 영어를 절대 사용하지 마세요."
)

# alias for backward compat
RETRY_SUFFIX = KOREAN_RETRY_SUFFIX


def postprocess_korean(text, *, genre="chat", validate=True, humanize=False):
    """통합 한국어 후처리 — force_korean + 품질검증 + humanize

    모든 LLM 응답에 적용할 수 있는 통합 함수.
    1) force_korean: CJK/일본어/영문 액션/LaTeX 제거
    2) korean_quality_check: 한국어 비율 30% 이상 검증
    3) compute_korean_quality: AI 패턴 탐지 (선택)

    Returns:
        (filtered_text, korean_ratio, quality_summary_or_None)
    """
    if not text or not text.strip():
        return text or "", 0.0, None

    # 1. 실제 필터링
    text = force_korean(text)

    # 2. 비율 검증
    korean_ratio = 0.0
    if validate:
        _valid, korean_ratio, _status = korean_quality_check(text)

    # 3. AI 패턴 탐지 (선택, 50자 이상)
    quality_summary = None
    if humanize and len(text) >= 50:
        try:
            from core.korean_humanize import compute_korean_quality as _ckq, get_quality_summary as _gqs
            quality_summary = _gqs(_ckq(text, genre=genre))
        except Exception:
            pass

    return text, korean_ratio, quality_summary


def generate_korean(ollama_post_fn, model, prompt, system,
                    options=None, timeout=120, max_retries=2, ollama_base="http://localhost:11434"):
    """LLM 생성 + 한국어 품질 검증 + 자동 재시도

    Args:
        ollama_post_fn: requests.Session().post 또는 OllamaClient.post
    Returns:
        (response_text, metadata)
    """
    _opts = options or {}
    _sys = system or ""
    meta = {"retries": 0, "korean_ratio": 0.0, "quality": "unknown"}

    for attempt in range(max_retries + 1):
        try:
            payload = {"model": model, "prompt": prompt, "system": _sys, "stream": False}
            if _opts:
                payload["options"] = _opts
            r = ollama_post_fn(f"{ollama_base}/api/generate", json=payload, timeout=timeout)
            raw = r.json().get("response", "")
            filtered = force_korean(raw)
            passed, ratio, detail = korean_quality_check(filtered)
            meta["korean_ratio"] = ratio
            meta["quality"] = detail
            meta["eval_count"] = r.json().get("eval_count", 0)

            if passed:
                if attempt > 0:
                    logger.info(f"Korean quality OK after {attempt} retry (ratio={ratio:.0%})")
                return filtered, meta

            meta["retries"] = attempt + 1
            if attempt < max_retries:
                logger.warning(f"Korean quality failed: {detail} (ratio={ratio:.0%}), retry {attempt+1}")
                _sys = (system or "") + KOREAN_RETRY_SUFFIX
            else:
                logger.warning(f"Korean quality still low after {max_retries} retries: {detail}")
                return filtered, meta
        except Exception as e:
            meta["quality"] = f"error: {str(e)[:100]}"
            if attempt == max_retries:
                raise
            logger.warning(f"LLM generate error (retry {attempt+1}): {e}")

    return "", meta


# ── CJK 표류 가드 (v2.125.52) ─────────────────────────────────────────────────
# 실측(2026-09-28 story 동일 입력 N=20): qwen2.5:14b가 **30%**(6/20)에서 중국어로 표류했다 —
# 시스템 프롬프트에 "반드시 한국어·중국어 금지"가 있는데도. force_korean은 한자를 문자 단위로
# 걷어내고 한글 없는 줄을 통째로 버리므로, 표류분은 **뼈대만 남은 채 200**으로 나갔다
# (붕괴 3/20: 526자→31자 · 절반 소실 3/20: 717→330자 — 후자는 게이트도 통과했다. 클래스 #1).
# 처치: 필터 **전** raw에서 표류를 재고, 넘으면 강화 지시로 재생성해 가장 덜 표류한 결과를 쓴다.
CJK_DRIFT_THRESHOLD = 0.02
_DRIFT_REINFORCE = ("\n\n[언어 규칙 — 최우선] 출력 전체를 **한국어(한글)**로만 작성하라. "
                    "중국어 문장·한자 표기·일본어를 한 글자도 쓰지 마라. 인물의 대사도 한국어로 쓴다.")


def cjk_share(text):
    """공백 제외 문자 중 한자·가나 비율(0~1). 빈 문자열은 0."""
    if not text:
        return 0.0
    body = re.sub(r"\s", "", text)
    if not body:
        return 0.0
    n = sum(len(m) for m in _CJK_CHARS.findall(body)) + sum(len(m) for m in _JAPANESE.findall(body))
    return n / len(body)


def generate_with_drift_guard(call, system, *, retries=2, threshold=CJK_DRIFT_THRESHOLD, tag=""):
    """`call(system) -> (text, tokens, ms)`를 CJK 표류 시 강화 지시로 최대 `retries`회 재시도.

    - `"Error:"`로 시작하는 결과(전송·서킷 실패)는 **그대로** 돌려준다(재시도는 표류만 대상 — 장애 증폭 금지).
    - 가장 덜 표류한 시도를 반환하고 토큰·시간은 **누적**한다(비용을 숨기지 않는다).
    - 전 시도가 표류하면 경고 로그를 남긴다(무성 실패 금지) — 최종 판정은 호출자 몫.
    """
    best = None
    tokens_total = ms_total = 0
    sys_prompt = system
    for attempt in range(retries + 1):
        text, tokens, ms = call(sys_prompt)
        tokens_total += tokens or 0
        ms_total += ms or 0
        if isinstance(text, str) and text.startswith("Error:"):
            return (best[0], tokens_total, ms_total) if best else (text, tokens_total, ms_total)
        share = cjk_share(text)
        if best is None or share < best[1]:
            best = (text, share)
        if share <= threshold:
            if attempt:
                logger.info("[korean-drift]%s %d회 재생성 후 해소(share=%.3f)", tag, attempt, share)
            return text, tokens_total, ms_total
        logger.warning("[korean-drift]%s 시도 %d CJK 표류 share=%.3f — 강화 지시로 재생성",
                       tag, attempt + 1, share)
        sys_prompt = (system or "") + _DRIFT_REINFORCE
    logger.warning("[korean-drift]%s 재시도 %d회 후에도 표류(최소 share=%.3f) — 최저 표류본 반환",
                   tag, retries, best[1])
    return best[0], tokens_total, ms_total
