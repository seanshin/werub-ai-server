"""Writing AI Blueprint — 글쓰기 AI 도우미 (톤앤매너, SEO, 코칭)"""
import hashlib
import json
import logging
import re
import time

import requests
from flask import Blueprint, request, jsonify, Response
from core.rate_limit import heavy_limit
from core.config import OLLAMA_BASE
from core.korean import force_korean, force_korean_json, force_korean_token
from core.korean_humanize import compute_korean_quality, get_quality_summary, get_s1_prompt_rules
from core.cache import TTLCache
from routes.cache_ctx import RequestAwareTTLCache
from core.validator import validate

logger = logging.getLogger("ollama-admin")

writing_bp = Blueprint("writing", __name__)

_ollama_session = None
_OLLAMA_BASE = None
_writing_cache = RequestAwareTTLCache(maxsize=200, ttl=3600)

WRITING_MODEL = "qwen2.5:14b"
FAST_MODEL = "qwen2.5:7b"

# 글쓰기 전문 모델 (v2.48.0)
WRITING_MODELS = {
    "exaone-deep:7.8b": {
        "id": "exaone-deep", "name": "EXAONE Deep 7.8B",
        "size_gb": 5.0, "lang": "ko+en",
        "strength": "추론 기반 구조적 글쓰기, 논리 구성 강화",
    },
    "dnotitia/dna:latest": {
        "id": "dna-8b", "name": "DNA 8B (한국어 특화)",
        "size_gb": 5.0, "lang": "ko",
        "strength": "한국어 생성 SOTA, 블로그/SEO 최적",
    },
    "gemma4:latest": {
        "id": "gemma4", "name": "Gemma 4 (26B MoE)",
        "size_gb": 16.0, "lang": "multi",
        "strength": "최신 Google MoE, 고품질 글쓰기+비전 겸용",
    },
}

def _get_writing_model(user_model=None, task="draft"):
    """글쓰기 모델 선택 — 사용자 지정 > 태스크별 기본"""
    if user_model:
        return user_model
    if task in ("seo", "evaluate"):
        return FAST_MODEL  # 경량 태스크
    return WRITING_MODEL


def init_writing(*, ollama_session, ollama_base):
    global _ollama_session, _OLLAMA_BASE
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base


# ---------------------------------------------------------------------------
# 톤앤매너 프롬프트
# ---------------------------------------------------------------------------

TONE_PROMPTS = {
    "professional": "전문적이고 신뢰감 있는 톤으로 작성하세요. 객관적이고 정확한 표현을 사용하세요.",
    "friendly": "친근하고 대화하듯 편한 톤으로 작성하세요. 이모지도 적절히 사용하세요.",
    "formal": "격식체(~합니다/~입니다)로 정중하게 작성하세요.",
    "casual": "반말(~해/~야)로 가볍고 편하게 작성하세요.",
    "academic": "학술적이고 논문 형식으로 작성하세요. 인용 형식을 갖추세요.",
    "marketing": "설득력 있고 매력적인 마케팅 톤으로 작성하세요. 행동을 유도하세요.",
}

STRUCTURE_TEMPLATES = {
    "essay": "서론-본론-결론 구조로 작성하세요.",
    "list": "핵심 포인트를 번호 목록으로 작성하세요.",
    "qa": "Q&A 형식으로 작성하세요.",
    "review": "리뷰 형식으로 작성하세요. 장단점을 포함하세요.",
    "diary": "일기/수필 형식으로 개인적인 느낌을 담아 작성하세요.",
    "news": "뉴스 기사 형식으로 작성하세요. 육하원칙을 따르세요.",
}

LENGTH_OPTIONS = {
    "short": {"num_predict": 300, "desc": "짧게 (~200자)"},
    "medium": {"num_predict": 800, "desc": "보통 (~500자)"},
    "long": {"num_predict": 1500, "desc": "길게 (~1000자)"},
    "very_long": {"num_predict": 3000, "desc": "매우 길게 (~2000자)"},
}


def _get_session():
    """Ollama session (lazy init)"""
    global _ollama_session, _OLLAMA_BASE
    if _ollama_session is None:
        _ollama_session = requests.Session()
        _OLLAMA_BASE = OLLAMA_BASE
    return _ollama_session, _OLLAMA_BASE


_HANGUL = re.compile(r"[가-힣]")


def _cacheable(response: str) -> bool:
    """캐시에 담아도 되는 응답인가 — **소비자 기준**으로 판정한다.

    🔴 호출자는 이 값을 그대로 쓰지 않고 `force_korean`을 한 번 더 태운다(5곳).
       그래서 "원문이 비어 있지 않다"는 저장하는 쪽의 조건일 뿐이고,
       **쓰는 쪽의 조건은 '필터를 통과한 뒤에도 쓸 수 있는 한국어'**다.

    2026-08-12 두 단계로 배웠다:
      ① 가드가 `if response:`뿐이라 **비한국어 응답**이 캐시됐다 →
         필터가 통째로 비워 그 뒤로 **영원히 빈 초안**(0.0초·0자).
      ② 그래서 `force_korean(...).strip()`으로 고쳤더니 이번엔 `'[]\n[]'`(5자)가
         통과했다 — **비어 있지 않을 뿐 한글이 하나도 없다**.
    ⇒ **한글이 실제로 남아 있는지**를 본다. 판정을 두 번 약하게 걸어 두 번 뚫렸다.
    """
    if not response:
        return False
    # observe=False — 이 판정은 출력이 아니라 캐시 여부만 정한다. 관측 로그는 호출자의 필터가 남긴다
    # (여기서도 남기면 한국어 경로는 중복, ja·zh 번역은 위양성이었다 — core/korean.force_korean 참고).
    filtered = force_korean(response, observe=False).strip()
    return bool(filtered) and bool(_HANGUL.search(filtered))


# 붕괴와 초안을 가르는 하한.
# 실측(2026-08-14): 붕괴는 2자(`'회사'`)·5자(`'[]\n[]'`), 정상 초안은 464~528자.
# ⚠️ 게이트의 min_len(120)에 맞추지 않는다 — 게이트에 맞춘 값은 게이트만 통과시킨다.
_MIN_DRAFT_CHARS = 40


def _usable_draft(text: str) -> bool:
    """**필터를 통과한 결과**를 호출자가 초안으로 쓸 수 있는가.

    `_cacheable`은 저장 직전의 *원문*을 보고, 이건 필터를 통과한 *결과*를 본다 —
    같은 조건을 걸어야 할 **두 지점**이고, 2026-08-12에는 캐시 쪽만 막혀 있었다.

    🔑 이 판정은 하루 사이에 **세 번 약하게 걸려 세 번 뚫렸다**:
      ① `if response:`           → 비한국어가 통과했다(필터가 통째로 비웠다)
      ② `force_korean().strip()` → `'[]\\n[]'`(5자)가 통과했다 — 비어 있지 않을 뿐
      ③ 한글 존재만 요구         → `'회사'`(2자)가 통과했다 — 한글일 뿐 초안이 아니다
    필요조건을 하나씩 고르면 계속 뚫린다. 소비자 조건은
    **'초안으로 쓸 수 있는 한국어 분량'**이지 '비어 있지 않음'이 아니다.
    """
    t = (text or "").strip()
    return len(t) >= _MIN_DRAFT_CHARS and bool(_HANGUL.search(t))


def _generate(model, prompt, system, options=None, timeout=None):
    """LLM 호출 (캐시 + 적응형 타임아웃 + 서킷 브레이커)"""
    from core.llm_resilience import get_adaptive_timeout, ollama_breaker
    sess, base = _get_session()
    opts = options or {}

    if timeout is None:
        timeout = get_adaptive_timeout(model, "draft")

    cache_key = hashlib.md5(f"{model}:{system[:100]}:{prompt[:200]}".encode()).hexdigest()
    cached = _writing_cache.get(cache_key)
    if cached:
        return cached[0], cached[1], 0

    if not ollama_breaker.can_execute():
        return "Error: Ollama temporarily unavailable", 0, 0

    start = time.time()
    try:
        # v2.124.98 task 라우터 경유 — 🔴 호출자의 세션·base를 넘긴다
        from core import model_router
        res = model_router.generate(
            "writing.default", prompt, model=model, system=system, options=opts,
            timeout=timeout,
            adapter=model_router.OllamaAdapter(base=base, session=sess))
        if not res.ok:
            # v2.125.16 무성 실패 일괄(2차): HTTP 오류를 전송 예외와 같은 분기로.
            # 🔴 종전엔 ""를 돌려주면서 **브레이커에 success를 기록**했다 — 실패를 성공으로
            #    세면 브레이커가 열리지 않아 장애가 길어진다(medical.py와 같은 결함).
            from core.llm_resilience import result_error
            raise result_error(res, "HTTP %s" % res.status)
        elapsed = res.duration_ms or int((time.time() - start) * 1000)
        response = res.text
        tokens = res.tokens
        ollama_breaker.record_success()
        # 🔴 **필터를 통과 못 할 응답은 캐시하지 않는다.**
        #    호출자는 이 값을 그대로 쓰지 않고 `force_korean`을 한 번 더 태운다(5곳).
        #    비한국어 응답은 필터가 **통째로 비우므로**, 원문이 비어 있지 않다는 이유로
        #    캐시하면 그 뒤로 **영원히 빈 초안**이 나간다.
        #    2026-08-12 실측: `/api/writing/draft`가 0.0초에 0자를 반환했고(=캐시 히트),
        #    프롬프트를 한 글자만 바꾸자 3.8초·493자·한글 74%였다.
        #    📌 오늘 세 번째 같은 클래스 — `/ask` 실패 캐시(v2.124.85)·`_medical_generate`
        #       깨진 JSON 고정. **캐시 가드는 소비자가 필요로 하는 조건으로 걸어야 한다.**
        if _cacheable(response):
            _writing_cache.set(cache_key, (response, tokens))
        return response, tokens, elapsed
    except Exception as e:
        # v2.125.68: 대기열 거절(busy)은 공용 브레이커 실패로 세지 않는다(core/llm_resilience.LLMBusyError)
        from core.llm_resilience import record_failure_unless_busy
        record_failure_unless_busy(e, ollama_breaker)
        return f"Error: {e}", 0, int((time.time() - start) * 1000)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@writing_bp.route("/api/writing/draft", methods=["POST"])
def writing_draft():
    """초안 작성 (톤앤매너 + 길이 + 구조 선택)

    톤앤매너·길이·구조를 지정해 초안을 생성한다.
    한국어 품질 점검은 `POST /api/korean/analyze`, 맞춤법은 `POST /api/korean/spellcheck`.
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt: {type: string, description: "글쓰기 주제/지시"}
            tone: {type: string, enum: [professional, friendly, formal, casual, academic, marketing], default: professional}
            structure: {type: string, enum: [essay, list, qa, review, diary, news], default: essay}
            length: {type: string, enum: [short, medium, long, very_long], default: medium}
            context: {type: string, description: "카페/게시판 맥락 정보"}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        schema:
          type: object
          properties:
            draft: {type: string}
            tone: {type: string}
            tokens: {type: integer}
    """
    data = request.json or {}
    errors = validate(data, {
        "prompt": {"required": True, "type": "str", "max_length": 50000},
        "tone": {"enum": list(TONE_PROMPTS.keys()), "default": "professional"},
        "structure": {"enum": list(STRUCTURE_TEMPLATES.keys()), "default": "essay"},
        "length": {"enum": list(LENGTH_OPTIONS.keys()), "default": "medium"},
    })
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    prompt = data["prompt"].strip()
    tone = data.get("tone", "professional")
    structure = data.get("structure", "essay")
    length = data.get("length", "medium")
    context = data.get("context", "")
    model = data.get("model", WRITING_MODEL)

    tone_inst = TONE_PROMPTS.get(tone, TONE_PROMPTS["professional"])
    struct_inst = STRUCTURE_TEMPLATES.get(structure, "")
    length_cfg = LENGTH_OPTIONS.get(length, LENGTH_OPTIONS["medium"])

    system = f"당신은 한국어 글쓰기 AI 어시스턴트입니다.\n{tone_inst}\n{struct_inst}"
    if context:
        system += f"\n\n맥락: {context}"

    response, tokens, duration = _generate(
        model, prompt, system,
        {"temperature": 0.7, "num_predict": length_cfg["num_predict"]})

    response = force_korean(response)

    # 한국어 자연화 품질 체크
    genre = "column" if structure in ("column", "essay") else "report" if structure == "report" else "blog"
    quality_result = compute_korean_quality(response.strip(), genre=genre)
    quality_metrics = get_quality_summary(quality_result)
    quality_metrics["retry_count"] = 0

    # 🔴 재생성 조건에 "쓸 수 있는가"를 합류시킨다.
    #    품질 밴드는 **한국어인 글**을 전제한 지표라 한국어가 아닐 때는 오히려 침묵한다 —
    #    실측 실패 건이 `risk_band=low`였다. 그 구간을 이 조건이 덮는다.
    if quality_result["risk_band"] == "high" or not _usable_draft(response):
        logger.info(
            f"Writing draft regenerating (risk={quality_result['risk_band']}, "
            f"usable={_usable_draft(response)})")
        enhanced_system = system + "\n\n" + get_s1_prompt_rules()
        response2, tokens2, duration2 = _generate(
            model, prompt, enhanced_system,
            {"temperature": 0.7, "num_predict": length_cfg["num_predict"]})
        response2 = force_korean(response2)
        # ⚠️ `Error:`로 시작하지 않는지만 보면 **빈 재생성이 멀쩡한 초안을 덮어쓴다**.
        if not response2.strip().startswith("Error:") and _usable_draft(response2):
            response = response2
            tokens += tokens2
            duration += duration2
            quality_result = compute_korean_quality(response.strip(), genre=genre)
            quality_metrics = get_quality_summary(quality_result)
            quality_metrics["retry_count"] = 1

    # 무성 실패 금지 — 재생성까지 하고도 한국어가 안 남으면 200으로 내보내지 않는다.
    # (v2.124.85 규율: 실패는 502로 알리고 캐시에 남기지 않는다)
    if not _usable_draft(response):
        logger.warning("writing draft: 재생성 후에도 한국어가 남지 않아 502")
        return jsonify({"error": "draft generation produced no usable Korean text"}), 502

    return jsonify({
        "draft": response.strip(),
        "tone": tone,
        "structure": structure,
        "length": length,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
        "quality_metrics": quality_metrics,
    })


@writing_bp.route("/api/writing/draft/stream", methods=["POST"])
def writing_draft_stream():
    """초안 작성 — 스트리밍 (SSE)
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt: {type: string}
            tone: {type: string, default: professional}
            structure: {type: string, default: essay}
            length: {type: string, default: medium}
            context: {type: string}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "prompt required"}}
    """
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400

    tone = data.get("tone", "professional")
    structure = data.get("structure", "essay")
    length = data.get("length", "medium")
    context = data.get("context", "")
    model = data.get("model", WRITING_MODEL)

    tone_inst = TONE_PROMPTS.get(tone, TONE_PROMPTS["professional"])
    struct_inst = STRUCTURE_TEMPLATES.get(structure, "")
    length_cfg = LENGTH_OPTIONS.get(length, LENGTH_OPTIONS["medium"])

    system = f"당신은 한국어 글쓰기 AI 어시스턴트입니다.\n{tone_inst}\n{struct_inst}"
    if context:
        system += f"\n\n맥락: {context}"

    def generate():
        _collected = []
        try:
            sess, base = _get_session()
            from core import model_router
            for chunk in model_router.stream(
                    "writing.default", prompt, model=model, system=system,
                    options={"temperature": 0.7,
                             "num_predict": length_cfg["num_predict"]},
                    timeout=120,
                    adapter=model_router.OllamaAdapter(base=base, session=sess)):
                if True:
                    token = force_korean_token(chunk.text)
                    if token:
                        _collected.append(token)
                        yield f"data: {json.dumps({'type': 'token', 'token': token}, ensure_ascii=False)}\n\n"
                    if chunk.done:
                        # 최종 텍스트 후처리 (v2.62.1)
                        from core.korean import postprocess_korean
                        _final, _ratio, _qs = postprocess_korean("".join(_collected), genre="column")
                        yield f"data: {json.dumps({'type': 'done', 'tokens': chunk.tokens, 'tone': tone, 'korean_ratio': round(_ratio, 3)}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@writing_bp.route("/api/writing/polish", methods=["POST"])
def writing_polish():
    """문장 다듬기 (선택 영역 지원)
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "교정할 텍스트 (선택 영역 또는 전체)"}
            tone: {type: string, default: professional}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "text required"}}
    """
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    tone = data.get("tone", "professional")
    tone_inst = TONE_PROMPTS.get(tone, "")

    system = f"당신은 한국어 교정 전문가입니다. 문법, 맞춤법, 자연스러움을 교정하세요. {tone_inst}\n원문의 의미를 유지하면서 개선된 텍스트만 출력하세요."

    # S1 규칙을 교정 system prompt에 포함
    system += "\n\n" + get_s1_prompt_rules()

    response, tokens, duration = _generate(
        FAST_MODEL, f"다음 텍스트를 교정하세요:\n\n{text}", system,
        {"temperature": 0.3, "num_predict": len(text) * 2})

    response = force_korean(response)

    # 품질 체크
    quality_result = compute_korean_quality(response.strip(), genre="essay")
    quality_metrics = get_quality_summary(quality_result)

    return jsonify({
        "original": text,
        "polished": response.strip(),
        "tone": tone,
        "tokens": tokens,
        "duration_ms": duration,
        "quality_metrics": quality_metrics,
    })


@writing_bp.route("/api/writing/seo", methods=["POST"])
def writing_seo():
    """SEO 최적화 — 메타 설명 + 키워드 + 가독성 분석
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "분석할 본문"}
            title: {type: string, description: "글 제목 (선택)"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "text required"}}
    """
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    title = data.get("title", "")

    prompt = f"""다음 글을 SEO 관점에서 분석하세요.

제목: {title or '(없음)'}
본문:
{text[:2000]}

반드시 JSON만 출력:
{{
  "meta_description": "160자 이내 메타 설명",
  "keywords": ["키워드1", "키워드2", ...],
  "readability": {{
    "score": 0-100,
    "avg_sentence_length": 0,
    "paragraph_count": 0,
    "has_subheadings": true/false,
    "issues": ["이슈1", ...]
  }},
  "suggestions": ["개선 제안1", "개선 제안2", ...]
}}"""

    system = "SEO 전문가. 한국어 콘텐츠 최적화. JSON만 출력."
    response, tokens, duration = _generate(
        FAST_MODEL, prompt, system, {"temperature": 0.2, "num_predict": 500})

    # 🔴 순서가 뒤집혀 있었다 — JSON 문자열에 `force_korean`을 먼저 태우면 **구조가 지워진다**.
    #    필터는 *줄 단위*로 한글 없는 줄을 버리는데 `"keywords": [`·`"score": 70,`·`}`가
    #    전부 거기 해당한다 ⇒ 파싱이 깨지고 호출자는 **전부 기본값인 빈 결과**를 200과 함께 받는다.
    #    실측(2026-08-18) 이 경로 **8/8 결정적 실패**. LLM 출력 자체는 멀쩡한 한국어 JSON이었다.
    # ⇒ **먼저 파싱하고, 문자열 값에만** 필터를 태운다. 그 용도의 함수가 이미 있고
    #    `medical_advanced.py`는 줄곧 이렇게 쓰고 있었다(v2.124.50 비전 OCR 삭제와 같은 클래스).
    # v2.125.72: 파싱 실패면 502 — 종전엔 `{"raw": 원문}`에 아래 SEO_DEFAULTS가 채워져
    #   **지어낸 `readability.score: 50`·빈 키워드**가 정상 결과처럼 나갔다(아래 `isinstance(str)` 분기는 죽은 코드였다).
    if isinstance(response, str) and response.startswith("Error:"):
        # `_generate`는 실패를 "Error: …" 문자열로 돌려준다 — 구조 미충족과 갈라 정직하게(업스트림 실패).
        return jsonify({"error": response[:200], "code": "GENERATION_FAILED"}), 502
    from core.quality import parse_json_strict
    parsed = parse_json_strict(response)
    if not isinstance(parsed, dict):
        logger.warning("[writing] seo 구조 미충족 → 502: len=%d", len(response or ""))
        return jsonify({"error": "model output is not the promised JSON", "code": "MODEL_OUTPUT_UNPARSED"}), 502
    result = force_korean_json(parsed)

    # 정형화된 폴백 스키마 보장(파싱은 됐는데 일부 키가 빠진 경우만)
    SEO_DEFAULTS = {"meta_description": "", "keywords": [], "readability": {"score": 50, "avg_sentence_length": 0, "paragraph_count": 0, "has_subheadings": False, "issues": []}, "suggestions": []}
    if isinstance(result, str):
        result = {**SEO_DEFAULTS, "raw": result}
    elif isinstance(result, dict):
        for k, v in SEO_DEFAULTS.items():
            if k not in result:
                result[k] = v
        # readability 하위 필드 보장
        if isinstance(result.get("readability"), dict):
            for rk, rv in SEO_DEFAULTS["readability"].items():
                if rk not in result["readability"]:
                    result["readability"][rk] = rv

    result["tokens"] = tokens
    result["duration_ms"] = duration
    return jsonify(result)


@writing_bp.route("/api/writing/evaluate", methods=["POST"])
def writing_evaluate():
    """글쓰기 코칭 — 품질 점수 + 개선 제안
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "평가할 글"}
            purpose: {type: string, description: "글의 목적 (블로그/보고서/SNS 등)"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "text required"}}
    """
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    purpose = data.get("purpose", "블로그")

    prompt = f"""다음 글을 평가하세요. 목적: {purpose}

{text[:3000]}

반드시 JSON만 출력:
{{
  "overall_score": 0-100,
  "categories": {{
    "clarity": {{"score": 0-100, "comment": "..."}},
    "structure": {{"score": 0-100, "comment": "..."}},
    "grammar": {{"score": 0-100, "comment": "..."}},
    "engagement": {{"score": 0-100, "comment": "..."}},
    "completeness": {{"score": 0-100, "comment": "..."}}
  }},
  "strengths": ["강점1", ...],
  "improvements": ["개선점1", ...],
  "rewrite_suggestion": "핵심 개선 방향 1문장"
}}"""

    system = "한국어 글쓰기 코치. 건설적이고 구체적인 피드백. JSON만 출력."
    response, tokens, duration = _generate(
        WRITING_MODEL, prompt, system, {"temperature": 0.3, "num_predict": 800})

    # 🔴 seo와 같은 결함 — 필터를 JSON에 먼저 태워 구조를 지우고 있었다(실측 8/8 실패).
    #    파싱 먼저, 필터는 문자열 값에만. 상세는 `writing_seo`의 주석.
    # v2.125.72: 파싱 실패면 502 — 종전엔 `{"raw": 원문}`이 `overall_score` 없이 200으로 나갔다
    #   (`isinstance(str)` 분기는 force_json이 문자열을 돌려주지 않아 죽은 코드였다).
    if isinstance(response, str) and response.startswith("Error:"):
        # `_generate`는 실패를 "Error: …" 문자열로 돌려준다 — 구조 미충족과 갈라 정직하게(업스트림 실패).
        return jsonify({"error": response[:200], "code": "GENERATION_FAILED"}), 502
    from core.quality import parse_json_strict
    parsed = parse_json_strict(response)
    if not isinstance(parsed, dict):
        logger.warning("[writing] evaluate 구조 미충족 → 502: len=%d", len(response or ""))
        return jsonify({"error": "model output is not the promised JSON", "code": "MODEL_OUTPUT_UNPARSED"}), 502
    result = force_korean_json(parsed)

    result["tokens"] = tokens
    result["duration_ms"] = duration
    result["purpose"] = purpose
    return jsonify(result)


@writing_bp.route("/api/writing/tones")
def writing_tones():
    """사용 가능한 톤앤매너 목록
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            tones: {type: object}
            structures: {type: object}
            lengths: {type: object}
    """
    return jsonify({
        "tones": {k: v for k, v in TONE_PROMPTS.items()},
        "structures": {k: v for k, v in STRUCTURE_TEMPLATES.items()},
        "lengths": {k: v["desc"] for k, v in LENGTH_OPTIONS.items()},
    })


@writing_bp.route("/api/writing/models")
def writing_models():
    """글쓰기 모델 목록 (v2.48.0)
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            models: {}
            default: {}
    """
    models = [
        {"id": WRITING_MODEL, "name": "Qwen2.5 14B", "size_gb": 9.0,
         "lang": "ko+en", "strength": "범용 메인, 한국어 99%", "default": True},
        {"id": FAST_MODEL, "name": "Qwen2.5 7B", "size_gb": 4.7,
         "lang": "ko+en", "strength": "경량 빠른 응답", "default": False},
    ]
    for ckpt, meta in WRITING_MODELS.items():
        models.append({**meta, "checkpoint": ckpt, "default": False})
    return jsonify({"models": models, "default": WRITING_MODEL})


# ---------------------------------------------------------------------------
# Orchestrated Writing (v2.49.0) — 3-model pipeline
# ---------------------------------------------------------------------------

CRAFT_STAGE1_SYSTEM = """당신은 한국어 글쓰기 AI입니다. 주어진 주제로 풍부한 초안을 작성하세요.
{tone_inst}
{struct_inst}
구체적인 예시와 근거를 포함하여 내용이 풍부한 글을 작성하세요."""

CRAFT_STAGE2_SYSTEM = """당신은 글 구조 전문 편집자입니다. 아래 초안의 논리 구조를 강화하세요.

[규칙]
1. 도입→본론→결론 흐름이 자연스러운지 검토
2. 논증이 약한 부분에 근거 보강
3. 불필요한 반복 제거, 핵심 메시지 명확화
4. 문단 간 전환이 매끄럽도록 개선
5. 원문의 내용과 톤은 유지하되 구조만 개선

개선된 전체 글을 출력하세요. 코멘트 없이 글 본문만 출력."""

CRAFT_STAGE3_SYSTEM = """당신은 한국어 문체 전문가입니다. 아래 글의 한국어 자연스러움을 다듬으세요.

[규칙]
1. 번역투 제거 ("~하는 것이 중요합니다" → "~해야 합니다")
2. 문장 길이 균형 (너무 긴 문장 분리)
3. 어색한 조사/어미 수정
4. 반복 어휘 동의어로 교체
5. 전체 문체 통일 (경어/평어 혼용 금지)

다듬어진 전체 글을 출력하세요. 코멘트 없이 글 본문만 출력."""


@writing_bp.route("/api/writing/craft", methods=["POST"])
def writing_craft():
    """Orchestrated high-quality writing — 3-model pipeline
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt: {type: string, description: "글쓰기 주제/지시"}
            tone: {type: string, enum: [professional, friendly, formal, casual, academic, marketing], default: professional}
            structure: {type: string, enum: [essay, list, qa, review, diary, news], default: essay}
            length: {type: string, enum: [short, medium, long, very_long], default: long}
            context: {type: string, description: "맥락 정보"}
    responses:
      200:
        schema:
          type: object
          properties:
            result: {type: string}
            stages: {type: array}
            total_duration_ms: {type: integer}
    """
    data = request.json or {}
    errors = validate(data, {
        "prompt": {"required": True, "type": "str", "max_length": 50000},
        "tone": {"enum": list(TONE_PROMPTS.keys()), "default": "professional"},
        "structure": {"enum": list(STRUCTURE_TEMPLATES.keys()), "default": "essay"},
        "length": {"enum": list(LENGTH_OPTIONS.keys()), "default": "long"},
    })
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    prompt = data["prompt"].strip()
    tone = data.get("tone", "professional")
    structure = data.get("structure", "essay")
    length = data.get("length", "long")
    context = data.get("context", "")

    tone_inst = TONE_PROMPTS.get(tone, TONE_PROMPTS["professional"])
    struct_inst = STRUCTURE_TEMPLATES.get(structure, "")
    length_cfg = LENGTH_OPTIONS.get(length, LENGTH_OPTIONS["long"])

    stages = []
    total_tokens = 0
    total_ms = 0

    # Stage 1: 초안 (qwen2.5:14b — 상주, 스왑 없음)
    s1_system = CRAFT_STAGE1_SYSTEM.format(tone_inst=tone_inst, struct_inst=struct_inst)
    if context:
        s1_system += f"\n\n맥락: {context}"

    s1_text, s1_tok, s1_ms = _generate(
        WRITING_MODEL, prompt, s1_system,
        {"temperature": 0.7, "num_predict": length_cfg["num_predict"]})
    s1_text = force_korean(s1_text)
    stages.append({"stage": 1, "role": "초안 생성", "model": WRITING_MODEL,
                    "tokens": s1_tok, "duration_ms": s1_ms})
    total_tokens += s1_tok; total_ms += s1_ms

    # Stage 2: 구조/논증 강화 (exaone-deep:7.8b)
    s2_model = "exaone-deep:7.8b"
    s2_prompt = f"[초안]\n{s1_text}\n\n위 초안의 논리 구조를 강화하세요."
    s2_text, s2_tok, s2_ms = _generate(
        s2_model, s2_prompt, CRAFT_STAGE2_SYSTEM,
        {"temperature": 0.4, "num_predict": length_cfg["num_predict"] + 200})
    s2_text = force_korean(s2_text)
    stages.append({"stage": 2, "role": "구조 강화", "model": s2_model,
                    "tokens": s2_tok, "duration_ms": s2_ms})
    total_tokens += s2_tok; total_ms += s2_ms

    # Stage 3: 한국어 다듬기 (dnotitia/dna:8b)
    s3_model = "dnotitia/dna:latest"
    s3_prompt = f"[글]\n{s2_text}\n\n위 글의 한국어 자연스러움을 다듬어주세요."
    s3_text, s3_tok, s3_ms = _generate(
        s3_model, s3_prompt, CRAFT_STAGE3_SYSTEM,
        {"temperature": 0.3, "num_predict": length_cfg["num_predict"] + 200})
    s3_text = force_korean(s3_text)
    stages.append({"stage": 3, "role": "한국어 다듬기", "model": s3_model,
                    "tokens": s3_tok, "duration_ms": s3_ms})
    total_tokens += s3_tok; total_ms += s3_ms

    return jsonify({
        "result": s3_text,
        "tone": tone,
        "structure": structure,
        "stages": stages,
        "total_tokens": total_tokens,
        "total_duration_ms": total_ms,
        "orchestrated": True,
    })


# ---------------------------------------------------------------------------
# Orchestrated Writing Stream (v2.55.0) — 3-model SSE pipeline
# ---------------------------------------------------------------------------

@writing_bp.route("/api/writing/craft/stream", methods=["POST"])
def writing_craft_stream():
    """Orchestrated high-quality writing — 3-model SSE streaming
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt: {type: string, description: "글쓰기 주제/지시"}
            tone: {type: string, default: professional}
            structure: {type: string, default: essay}
            length: {type: string, default: long}
            context: {type: string}
    responses:
      200:
        description: SSE stream (stage_start, token, stage_done, done events)
    """
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400

    tone = data.get("tone", "professional")
    structure = data.get("structure", "essay")
    length = data.get("length", "long")
    context = data.get("context", "")

    tone_inst = TONE_PROMPTS.get(tone, TONE_PROMPTS["professional"])
    struct_inst = STRUCTURE_TEMPLATES.get(structure, "")
    length_cfg = LENGTH_OPTIONS.get(length, LENGTH_OPTIONS["long"])

    STAGES = [
        {"stage": 1, "role": "초안 생성", "model": WRITING_MODEL,
         "system": CRAFT_STAGE1_SYSTEM.format(tone_inst=tone_inst, struct_inst=struct_inst) + (f"\n\n맥락: {context}" if context else ""),
         "prompt_fn": lambda _input: prompt,
         "opts": {"temperature": 0.7, "num_predict": length_cfg["num_predict"]}},
        {"stage": 2, "role": "구조 강화", "model": "exaone-deep:7.8b",
         "system": CRAFT_STAGE2_SYSTEM,
         "prompt_fn": lambda prev: f"[초안]\n{prev}\n\n위 초안의 논리 구조를 강화하세요.",
         "opts": {"temperature": 0.4, "num_predict": length_cfg["num_predict"] + 200}},
        {"stage": 3, "role": "한국어 다듬기", "model": "dnotitia/dna:latest",
         "system": CRAFT_STAGE3_SYSTEM,
         "prompt_fn": lambda prev: f"[글]\n{prev}\n\n위 글의 한국어 자연스러움을 다듬어주세요.",
         "opts": {"temperature": 0.3, "num_predict": length_cfg["num_predict"] + 200}},
    ]

    def _sse(event_type, payload):
        return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"

    def generate():
        prev_output = ""
        total_tokens = 0
        total_ms = 0
        try:
            for cfg in STAGES:
                yield _sse("stage_start", {"stage": cfg["stage"], "role": cfg["role"], "model": cfg["model"]})

                stage_prompt = cfg["prompt_fn"](prev_output)
                start = time.time()
                sess, base = _get_session()

                if cfg["stage"] < 3:
                    # Stage 1-2: 비스트리밍 — 메모리 효율 + 속도 향상
                    from core import model_router
                    _res = model_router.generate(
                        "writing.default", stage_prompt, model=cfg["model"],
                        system=cfg["system"], options=cfg["opts"], timeout=180,
                        adapter=model_router.OllamaAdapter(base=base, session=sess))
                    if not _res.ok:      # v2.125.16: HTTP 오류 → 아래 except의 error 이벤트
                        raise RuntimeError(_res.error or ("HTTP %s" % _res.status))
                    prev_output = force_korean(_res.text).strip()
                    stage_tok = _res.tokens
                else:
                    # Stage 3: SSE 스트리밍 — 클라이언트에 토큰 전달
                    from core import model_router
                    tokens = []
                    stage_tok = 0
                    for chunk in model_router.stream(
                            "writing.default", stage_prompt, model=cfg["model"],
                            system=cfg["system"], options=cfg["opts"], timeout=180,
                            adapter=model_router.OllamaAdapter(base=base, session=sess)):
                        if True:
                            token = force_korean_token(chunk.text)
                            if token:
                                tokens.append(token)
                                yield _sse("token", {"token": token})
                            if chunk.done:
                                stage_tok = chunk.tokens
                    prev_output = force_korean("".join(tokens)).strip()

                elapsed = int((time.time() - start) * 1000)
                total_tokens += stage_tok
                total_ms += elapsed
                yield _sse("stage_done", {"stage": cfg["stage"], "role": cfg["role"],
                                          "tokens": stage_tok, "duration_ms": elapsed,
                                          "length": len(prev_output)})

            yield _sse("done", {"result": prev_output, "total_tokens": total_tokens,
                                "total_duration_ms": total_ms, "orchestrated": True,
                                "tone": tone, "structure": structure})
        except Exception as e:
            logger.error("craft/stream error: %s", e)
            yield _sse("error", {"error": str(e)})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Translation (v2.55.0)
# ---------------------------------------------------------------------------

TRANSLATE_LANGUAGES = {
    "en": "English", "ja": "日本語", "zh": "中文", "es": "Español",
    "fr": "Français", "de": "Deutsch", "ko": "한국어",
}

# /api/translate(호스트 STT 파이프라인 연동 계약)용 도메인/스타일 가이드.
TRANSLATE_DOMAIN_GUIDE = {
    "medical": "의학 전문 번역입니다. 의학 용어의 정확성을 최우선으로 하고, 임상적 의미를 왜곡하지 마세요. "
               "약물명·검사명·수치·단위는 보존하세요.",
    "legal":   "법률 전문 번역입니다. 법률 용어와 효력의 정확성을 우선하세요.",
    "general": "",
}
TRANSLATE_STYLE_GUIDE = {
    "literal": "직역 — 원문의 의미와 구조를 충실히 보존하세요. 의역·생략·요약 금지(정확성 우선).",
    "natural": "자연스러운 의역 — 대상 언어 화자에게 매끄럽게 읽히도록 하세요.",
    "formal":  "격식체/공식 문서 수준으로 번역하세요.",
    "casual":  "일상적이고 자연스러운 구어체로 번역하세요.",
}


# 괄호로 시작하는 영문 메타노트만 절단(예: "(Note: ...)", "[Corrected ...]").
# 병기 괄호 '고혈압(hypertension)'·한국어 '참고/주의'가 든 정상 번역문을 오절단하지 않도록
# 여는 괄호+영문 마커로 한정. 1차 방어(이중 개행 분리)가 관측된 코멘트는 이미 제거.
_TRANSLATE_NOTE_RE = re.compile(
    r"\s*[\(\[]\s*(?:Note|Corrected|Correction)\b.*$",
    re.IGNORECASE | re.DOTALL)


def _clean_translation(s: str) -> str:
    """번역문만 남기고 모델이 덧붙인 메타 코멘트 제거(단일 발화 가정).
    - 코멘트는 관측상 이중 개행으로 분리됨 → 첫 단락만 사용(도메인 안전).
    - 보조: 괄호로 시작하는 영문 메타노트('(Note: ...)') 절단."""
    s = (s or "").strip().strip('"').strip()
    s = re.split(r"\n\s*\n", s, 1)[0].strip()   # 첫 단락만(코멘트 분리)
    s = _TRANSLATE_NOTE_RE.sub("", s).strip()   # 괄호 영문 메타노트 절단
    return s.strip().strip('"').strip()


# 대상 언어별 강한 출력 지시 — qwen2.5:14b의 CJK 혼동(한→일에 중국어 출력) 억제.
_LANG_HINT = {
    "ko": "반드시 한국어로만 출력하세요.",
    "en": "Output only in English.",
    "ja": "반드시 일본어로만 출력하세요(히라가나·가타카나 포함). 중국어 한자만으로 쓰지 마세요.",
    "zh": "반드시 중국어(간체)로만 출력하세요. 일본어 가나(かな)를 쓰지 마세요.",
    "es": "Output only in Spanish.",
    "fr": "Output only in French.",
    "de": "Output only in German.",
}


# 중국어 전각 문장부호 — 비중국어 대상에 섞이면 중국어 오염 신호.
_ZH_PUNCT = "，。、？！；：（）「」『』《》"


def _script_ok(text: str, target: str) -> bool:
    """번역 결과가 대상 언어의 표기 체계와 부합하는가(언어 충실도 가드).
    오탐 최소화 위해 '명백한 불일치'만 False(가나 없는 순중국어를 ja로, 한글에 중국어 혼입 등)."""
    if not text:
        return True
    has_kana = any(0x3040 <= ord(c) <= 0x30FF for c in text)
    has_hangul = any(0xAC00 <= ord(c) <= 0xD7A3 for c in text)
    has_cjk = any(0x4E00 <= ord(c) <= 0x9FFF for c in text)
    has_latin = any(c.isalpha() and c.isascii() for c in text)
    has_zh_punct = any(c in _ZH_PUNCT for c in text)
    if target == "ja":
        return has_kana or not has_cjk      # 가나 없는 순CJK = 중국어 의심
    if target == "zh":
        return not has_kana and not has_hangul
    if target == "ko":
        # 한글이 있어도 중국어 문장부호나 과도한 한자(한글의 1/5 초과)면 오염으로 판정.
        n_hangul = sum(1 for c in text if 0xAC00 <= ord(c) <= 0xD7A3)
        n_cjk = sum(1 for c in text if 0x4E00 <= ord(c) <= 0x9FFF)
        return n_hangul > 0 and not has_zh_punct and n_cjk <= max(2, n_hangul // 5)
    if target in ("en", "es", "fr", "de"):
        return has_latin and not has_kana and not has_hangul and not has_cjk and not has_zh_punct
    return True


@writing_bp.route("/api/translate", methods=["POST"])
@heavy_limit("60/minute")
def api_translate():
    """실시간 다국어 번역 — 호스트 STT 파이프라인 연동 계약(/api/translate)

    호스트 STT 파이프라인이 실시간으로 부르는 경로. `text` 하나를 `targets` 배열의 여러 언어로
    동시에 번역한다(`source: auto`면 자동 감지). `style`은 literal/natural/formal/casual.
    메타코멘트 제거·언어 충실도 가드가 걸려 있어 군더더기 서두가 섞이지 않는다.
    문서 톤을 다듬는 용도라면 `POST /api/writing/translate`를 쓴다.
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text, targets]
          properties:
            text:    {type: string, description: "번역할 원문"}
            source:  {type: string, default: "auto", description: "원문 언어 코드(또는 auto)"}
            targets: {type: array, items: {type: string}, description: "대상 언어 코드 배열 (en/ja/zh/es/fr/de/ko)"}
            domain:  {type: string, enum: [general, medical, legal], default: general}
            style:   {type: string, enum: [literal, natural, formal, casual], default: literal}
    responses:
      200:
        description: "{ translation: {lang: text}, ms: int }"
    """
    data = request.json or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    # targets(배열) — 단일 'target' 문자열도 호환
    targets = data.get("targets")
    if not targets:
        single = data.get("target")
        targets = [single] if single else []
    if isinstance(targets, str):
        targets = [targets]
    targets = [t.strip().lower() for t in targets if isinstance(t, str) and t.strip()]
    if not targets:
        return jsonify({"error": "targets is required (non-empty array)"}), 400
    unknown = [t for t in targets if t not in TRANSLATE_LANGUAGES]
    if unknown:
        return jsonify({"error": "unsupported target(s)", "unsupported": unknown,
                        "supported": list(TRANSLATE_LANGUAGES.keys())}), 400

    result = translate_core(
        text, targets,
        source=(data.get("source") or "auto"),
        domain=(data.get("domain") or "general"),
        style=(data.get("style") or "literal"))
    return jsonify(result)


def translate_core(text, targets, source="auto", domain="general", style="literal") -> dict:
    """번역 핵심 로직 — /api/translate 및 /api/stt/chunk(final 통합 A+C) 공용.
    text·targets는 호출부에서 정규화/검증 완료라고 가정(targets=소문자 코드 리스트).
    반환: {translation:{lang:text}, ms, source, targets, domain, style, model, per_target_ms}."""
    text = (text or "").strip()
    source = (source or "auto").strip().lower()
    domain = (domain or "general").strip().lower()
    style = (style or "literal").strip().lower()
    domain_guide = TRANSLATE_DOMAIN_GUIDE.get(domain, "")
    style_guide = TRANSLATE_STYLE_GUIDE.get(style, TRANSLATE_STYLE_GUIDE["literal"])
    src_label = f"{TRANSLATE_LANGUAGES[source]}({source})" if source in TRANSLATE_LANGUAGES else ""

    t0 = time.time()
    translation, per_ms = {}, {}
    n_predict = min(2000, max(64, len(text) * 3))
    for tgt in targets:
        tgt_name = TRANSLATE_LANGUAGES.get(tgt, tgt)
        lang_hint = _LANG_HINT.get(tgt, f"반드시 {tgt_name}로만 출력하세요.")
        system = (
            f"전문 번역가입니다. 입력 텍스트를 {tgt_name}(으)로 번역합니다. {lang_hint} "
            "출력은 번역 결과 텍스트만 포함합니다. 설명·주석·교정 메모·대체 번역·"
            "'Note'/'참고' 같은 부가 코멘트를 절대 추가하지 마세요. "
            "원문이 어색하거나 모호해도 임의로 고치지 말고 그대로 번역만 합니다."
        )
        if domain_guide:
            system += " " + domain_guide
        # [원문]을 프롬프트 맨 앞에 둔다 — _generate 캐시키가 prompt[:200]라
        # 보일러플레이트가 길면 짧은 입력이 잘려 캐시 충돌(다른 발화→같은 번역). 텍스트 선두 = 키 고유.
        prompt = (
            f"[원문]\n{text}\n\n"
            f"위 [원문]을 {tgt_name}({tgt})로 번역하세요. {lang_hint}\n"
            f"[스타일] {style_guide}\n"
            "[규칙]\n- 번역 결과 텍스트만 출력(설명·코멘트·메모·따옴표 래핑 없이).\n"
            "- 고유명사·약물명·코드·수치·단위는 보존(용어 병기 괄호는 허용).\n"
            + (f"[원문 언어] {src_label}" if src_label else "")
        )
        resp, _tokens, dur = _generate(
            WRITING_MODEL, prompt, system,
            {"temperature": 0.2, "num_predict": n_predict}, timeout=60)
        resp = _clean_translation(resp)
        # 언어 충실도 가드 — 대상 언어 표기 불일치(예: ja에 순중국어) 시 1회 재시도(저온·강한 지시).
        guard_ok = _script_ok(resp, tgt)
        if not guard_ok:
            retry_sys = system + f" 경고: 반드시 {tgt_name}로만 작성하고 다른 언어(특히 중국어)를 절대 쓰지 마세요."
            resp2, _t2, dur2 = _generate(
                WRITING_MODEL, prompt, retry_sys,
                {"temperature": 0.1, "num_predict": n_predict}, timeout=60)
            resp2 = _clean_translation(resp2)
            dur += dur2
            if _script_ok(resp2, tgt) or not resp:
                resp = resp2
            guard_ok = _script_ok(resp, tgt)
        # 한국어: 일반 도메인 정규화 OR 가드 실패(중국어 혼입) 잔존 시 force_korean으로 정리.
        # (의료/법률 정상 출력은 영어 용어 병기 보존 위해 미적용 — 오염 잔존 시에만 최후 정리)
        if tgt == "ko" and (domain == "general" or not guard_ok):
            resp = force_korean(resp)
        translation[tgt] = resp
        per_ms[tgt] = dur

    return {
        "translation": translation,
        "ms": int((time.time() - t0) * 1000),
        "source": source,
        "targets": targets,
        "domain": domain,
        "style": style,
        "model": WRITING_MODEL,
        "per_target_ms": per_ms,
    }


@writing_bp.route("/api/writing/translate", methods=["POST"])
def writing_translate():
    """텍스트 번역 — 지정 언어로 번역
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text, target]
          properties:
            text: {type: string, description: "번역할 원문"}
            target: {type: string, description: "대상 언어 코드 (en, ja, zh, es, fr, de, ko)"}
            tone: {type: string, enum: [formal, casual, literary], default: formal, description: "번역 톤"}
    responses:
      200:
        schema:
          type: object
          properties:
            translation: {type: string}
            source_lang: {type: string}
            target_lang: {type: string}
    """
    data = request.json or {}
    errors = validate(data, {
        "text": {"required": True, "type": "str", "max_length": 100000},
        "target": {"required": True, "enum": list(TRANSLATE_LANGUAGES.keys())},
        "tone": {"enum": ["formal", "casual", "literary"], "default": "formal"},
    })
    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    text = data["text"].strip()
    target = data["target"].strip().lower()
    tone = data.get("tone", "formal")
    target_name = TRANSLATE_LANGUAGES[target]

    tone_guide = {
        "formal": "격식체/존댓말 사용, 공식 문서 수준",
        "casual": "일상적이고 자연스러운 구어체",
        "literary": "문학적이고 우아한 표현, 비유와 묘사 풍부",
    }.get(tone, "")

    prompt = f"""다음 텍스트를 {target_name}({target})로 번역하세요.

[번역 톤] {tone_guide}
[규칙]
- 의미를 정확히 전달하되 대상 언어에 자연스럽게 번역
- 고유명사는 원문 유지 또는 현지 표기 병기
- 번역문만 출력 (설명/코멘트 없음)

[원문]
{text}"""

    system = f"전문 번역가입니다. {target_name}로 정확하고 자연스럽게 번역하세요. 번역문만 출력."

    # 언어별 번역 길이 계수 (CJK→EN은 짧고, EN→FR/ES는 김)
    _lang_mult = {"en": 1.0, "ja": 1.1, "zh": 1.0, "ko": 1.15, "es": 1.2, "fr": 1.25, "de": 1.1}
    response, tokens, duration = _generate(
        WRITING_MODEL, prompt, system,
        {"temperature": 0.3, "num_predict": int(len(text) * _lang_mult.get(target, 1.2))},
        timeout=120)

    response = response.strip()
    # 한국어 대상일 때만 force_korean 적용
    if target == "ko":
        response = force_korean(response)

    return jsonify({
        "translation": response,
        "source_lang": "auto",
        "target_lang": target,
        "target_name": target_name,
        "tone": tone,
        "tokens": tokens,
        "duration_ms": duration,
    })


@writing_bp.route("/api/writing/languages", methods=["GET"])
def writing_languages():
    """지원 번역 언어 목록
    ---
    tags: [Writing AI]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            languages: {type: array}
    """
    return jsonify({"languages": [{"code": k, "name": v} for k, v in TRANSLATE_LANGUAGES.items()]})
