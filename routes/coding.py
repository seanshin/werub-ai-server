"""Coding AI Blueprint — 소프트웨어 개발 전용 API"""
import hashlib
import json
import os
import logging
import re
import time
import uuid

import requests as http_requests
from core.cache import TTLCache
from routes.cache_ctx import RequestAwareTTLCache
from core.quality import (force_json, force_json_array, generate_with_validation, generate_with_validation_ex,
                         validate_json_array_response, make_code_validator,
                         extract_code_block, scan_code_security)
from flask import Blueprint, request, jsonify, Response
from core.errors import internal_error
from core.security import require_auth

logger = logging.getLogger("ollama-admin")

coding_bp = Blueprint("coding", __name__)

_ollama_session = None
_OLLAMA_BASE = None

CODING_MODEL = "qwen2.5-coder:14b"
FAST_MODEL = "deepseek-coder-v2:16b"

# 코드 분석 캐시 — 동일 코드 반복 리뷰/설명 방지 (TTL 30분)
_coding_cache = RequestAwareTTLCache(maxsize=200, ttl=1800)
# 임베딩 캐시 — 결정론적, TTL 2시간
_embed_cache = TTLCache(maxsize=2000, ttl=7200)


def _code_ck(code, *extras):
    raw = code + "".join(str(e) for e in extras)
    return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()


_BASE_DIR = None
_code_collection = None
EMBED_MODEL = "qwen3-embedding:8b"
EMBED_MODEL_FALLBACK = "nomic-embed-text"


def _get_code_collection():
    global _code_collection
    if _code_collection is None:
        import chromadb
        client = chromadb.PersistentClient(path=os.path.join(_BASE_DIR, "chromadb_data"))
        collection_name = "code_knowledge_v2" if "qwen3" in EMBED_MODEL else "code_knowledge"
        try:
            _code_collection = client.get_collection(collection_name)
        except Exception:
            _code_collection = client.create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine", "description": "Code knowledge base for RAG"})
    return _code_collection


def _get_code_embedding(text):
    _ck = hashlib.md5((EMBED_MODEL + text[:4000]).encode("utf-8", errors="replace")).hexdigest()
    cached = _embed_cache.get(_ck)
    if cached is not None:
        return cached
    # v2.124.99 task 라우터 경유.
    # 🔴 **이 경로만 `num_ctx`가 없어 §1-2 OOM 결함이 남아 있었다** — 전역 8192를 먹으면
    #    임베딩 모델 KV가 4.5GB로 불어 상주 14b와 겹쳐 터진다(v2.86.12 실측·client/rag.py는
    #    그때 고쳤는데 여기는 빠졌다). 어댑터가 항상 ctx를 실으므로 이관이 곧 봉합이다.
    #    📌 "한 곳에서 고친 결함은 같은 클래스 전 경로를 쓸 것"(v2.124.65) — 또 확인됐다.
    from core import model_router
    spec = model_router.resolve("coding.embed")
    for model in [spec.get("model"), spec.get("fallback")]:
        if not model:
            continue
        try:
            adapter = model_router.OllamaAdapter(base=_OLLAMA_BASE)
            vecs = adapter.embed(model=model, texts=text[:4000],
                                 ctx=spec.get("ctx", 2048), timeout=30)
            if not vecs:
                continue
            vec = vecs[0]
            _embed_cache.set(_ck, vec)
            return vec
        except Exception:
            continue
    raise RuntimeError("All embedding models failed")


def init_coding(*, ollama_session, ollama_base, base_dir=None):
    global _ollama_session, _OLLAMA_BASE, _BASE_DIR
    _ollama_session = ollama_session
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _code_generate(model, prompt, system, options=None, timeout=120):
    opts = options or {"temperature": 0.2, "num_predict": 2000}
    start = time.time()
    try:
        r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model, "prompt": prompt, "system": system,
            "stream": False, "options": opts,
        }, timeout=timeout)
        d = r.json()
        elapsed_ms = int((time.time() - start) * 1000)
        return d.get("response", ""), d.get("eval_count", 0), elapsed_ms
    except Exception as e:
        return f"Error: {e}", 0, int((time.time() - start) * 1000)


# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

GENERATE_SYSTEM = """You are an expert software engineer. Generate clean, production-ready code.

Rules:
1. Include proper type hints/annotations
2. Add docstrings/JSDoc comments
3. Handle edge cases and errors
4. Follow language best practices and conventions
5. Output ONLY the code unless explanation is requested
6. Use the specified language, framework, and patterns"""

REVIEW_SYSTEM = """You are a senior code reviewer. Analyze the code for:

1. **Bugs**: Logic errors, edge cases, null/undefined handling
2. **Security**: Injection, XSS, authentication, secrets exposure
3. **Performance**: N+1 queries, unnecessary loops, memory leaks
4. **Maintainability**: Naming, structure, DRY violations, complexity
5. **Best Practices**: Language conventions, error handling, typing

For each issue found, provide:
- Severity: 🔴 critical / ⚠️ warning / 💡 suggestion
- Location: file/function/line reference
- Problem: what's wrong
- Fix: corrected code snippet

Output as structured JSON array."""

REFACTOR_SYSTEM = """You are a refactoring expert. Improve the code while preserving exact behavior.

Focus on:
1. Extract functions/methods for repeated logic
2. Simplify complex conditionals
3. Improve naming and readability
4. Apply appropriate design patterns
5. Reduce cognitive complexity
6. Add proper typing

Output the refactored code with brief comments explaining each change."""

DEBUG_SYSTEM = """You are a debugging expert. Analyze the code and error to find the root cause.

Steps:
1. Reproduce: understand what the code does
2. Identify: pinpoint the exact bug location
3. Explain: why the bug occurs
4. Fix: provide the corrected code
5. Prevent: suggest how to prevent similar bugs

Be precise about line numbers and variable states."""

TEST_SYSTEM = """You are a testing expert. Write comprehensive tests for the given code.

Rules:
1. Use the appropriate testing framework (pytest/jest/JUnit/etc.)
2. Cover: happy path, edge cases, error cases, boundary conditions
3. Use descriptive test names
4. Include setup/teardown if needed
5. Mock external dependencies
6. Aim for high code coverage"""

EXPLAIN_SYSTEM = """You are a code educator. Explain the code clearly.

Structure:
1. Overview: what the code does (1-2 sentences)
2. Step-by-step walkthrough
3. Key concepts used
4. Complexity analysis (time/space)
5. Potential improvements

Adjust explanation depth based on the requested level (beginner/intermediate/advanced).
Answer in Korean."""

CONVERT_SYSTEM = """You are a language conversion expert. Convert the code to the target language.

Rules:
1. Preserve exact logic and behavior
2. Use target language idioms and conventions
3. Adapt data structures to target language equivalents
4. Maintain type safety where applicable
5. Add language-specific error handling"""

ARCHITECT_SYSTEM = """You are a software architect. Design the system architecture.

Provide:
1. Component diagram (text-based)
2. API design (endpoints, schemas)
3. Data model (tables/collections, relationships)
4. Technology stack recommendation
5. Scalability considerations
6. Security architecture

Answer in Korean with English technical terms."""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@coding_bp.route("/api/code/generate", methods=["POST"])
@require_auth
def code_generate():
    """Generate code from description
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt: {type: string, description: "코드 생성 요청"}
            language: {type: string, description: "프로그래밍 언어 (python/typescript/java/go/rust 등)"}
            framework: {type: string, description: "프레임워크 (react/fastapi/spring 등)"}
            style: {type: string, description: "스타일 (concise/detailed/production)"}
            model: {type: string, default: "qwen2.5-coder:14b"}
    responses:
      200:
        description: "생성된 코드 + syntax_valid + security_scan (위험 패턴 시)"
    """
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    language = data.get("language", "")
    framework = data.get("framework", "")
    style = data.get("style", "production")
    model = data.get("model", CODING_MODEL)

    full_prompt = prompt
    if language:
        full_prompt = f"Language: {language}\n{full_prompt}"
    if framework:
        full_prompt = f"Framework: {framework}\n{full_prompt}"
    if style:
        full_prompt = f"Style: {style}\n{full_prompt}"

    # Python: syntax validation + retry. Others: generate only.
    validators = []
    if language.lower() in ("python", "py", ""):
        validators = [make_code_validator("python")]

    if validators:
        response, tokens, duration, retries, passed = generate_with_validation_ex(
            generate_fn=lambda p, s, o: _code_generate(model, p, s, o),
            prompt=full_prompt, system=GENERATE_SYSTEM, options=None,
            validators=validators, max_retries=2,
        )
    else:
        response, tokens, duration = _code_generate(model, full_prompt, GENERATE_SYSTEM)
        retries = 0
        passed = True   # validator 없음 — 종전 validated=True(retries 0 < 2) 유지

    # 🔴 **문서 계약은 `code`가 순수 코드다** — `docs/CODING_API_GUIDE.md`의 응답 예시가
    #    `"code": "from fastapi import Depends, HTTPException..."`이다. 그런데 구현은
    #    LLM 원문(```펜스 + 뒤따르는 설명문)을 그대로 실었다. 더 나쁜 건 **`syntax_valid`와
    #    `security_scan`은 펜스를 벗긴 `code_block`을 검사한 값**이라, 호출자가 받은
    #    문자열을 그대로 파싱하면 실패하는데 플래그는 true였다(2026-08-12 실측).
    #    ⇒ 검사 대상과 반환 값을 **같은 것**으로 맞춘다. 원문은 `raw`로 보존한다
    #      (설명이 필요한 호출자를 위해 — 정보를 버리지는 않는다).
    #    `extract_code_block`은 블록이 없으면 전체를 돌려주므로 폴백이 내장돼 있다.
    code_block = extract_code_block(response, language or "python")
    result = {
        "code": code_block or response,
        "raw": response,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
        "language": language,
    }

    # Syntax check + security scan
    if code_block:
        # Syntax (Python only)
        if language.lower() in ("python", "py", ""):
            import ast
            try:
                ast.parse(code_block)
                result["syntax_valid"] = True
            except SyntaxError as e:
                result["syntax_valid"] = False
                result["syntax_error"] = f"line {e.lineno}: {e.msg}"

        # Security scan (all languages)
        security = scan_code_security(code_block, language or "python")
        if security["issue_count"] > 0:
            result["security_scan"] = security

    result["quality"] = {"retries": retries, "validated": passed}

    return jsonify(result)


@coding_bp.route("/api/code/generate/stream", methods=["POST"])
@require_auth
def code_generate_stream():
    """Generate code with streaming
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    responses:
      200:
        description: "스트리밍 응답(NDJSON/SSE) — 토큰 단위 전송"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    model = data.get("model", CODING_MODEL)

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    def generate():
        try:
            r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
                "model": model, "prompt": prompt, "system": GENERATE_SYSTEM,
                "stream": True, "options": {"temperature": 0.2, "num_predict": 3000},
            }, stream=True, timeout=180)
            for line in r.iter_lines():
                if line:
                    yield line.decode() + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    return Response(generate(), mimetype="application/x-ndjson")


@coding_bp.route("/api/code/review", methods=["POST"])
@require_auth
def code_review():
    """Review code for bugs, security, performance
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [code]
          properties:
            code: {type: string, description: "리뷰할 코드"}
            language: {type: string}
            focus: {type: string, description: "집중 영역 (security/performance/all)", default: "all"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    language = data.get("language", "auto-detect")
    focus = data.get("focus", "all")
    model = data.get("model", CODING_MODEL)

    key = _code_ck(code, language, focus, model)
    cached = _coding_cache.get(key)
    if cached is not None:
        return jsonify(cached)

    prompt = f"Language: {language}\nFocus: {focus}\n\nCode to review:\n```\n{code}\n```"

    opts = {"temperature": 0.1, "num_predict": 2000}

    # Validated generation: JSON 배열 검증 + 자동 재시도
    response, tokens, duration, retries, passed = generate_with_validation_ex(
        generate_fn=lambda p, s, o: _code_generate(model, p, s, o or opts),
        prompt=prompt, system=REVIEW_SYSTEM, options=opts,
        validators=[validate_json_array_response],
        max_retries=2,
    )

    # Parse issues — v2.125.72: 파싱 실패면 502이고 **캐시하지 않는다**. 종전 `force_json_array`는 실패 시 `[]`를 줘
    #   `issues: []`·`summary.total: 0` = **'버그 없음'**으로 읽히는 200이 나갔고, 그 결과를 아래에서 캐시해
    #   같은 코드를 다시 보내도 같은 거짓 '이상 없음'이 나왔다(반복 클래스 #1 + #5 실패 캐시). 정상 `[]`는 성공.
    if isinstance(response, str) and response.startswith("Error:"):
        # `_code_generate`는 실패를 "Error: …" 문자열로 돌려준다 — 구조 미충족과 갈라 정직하게(비캐시).
        return jsonify({"error": response[:200], "code": "GENERATION_FAILED"}), 502
    from core.quality import parse_json_array_strict
    issues = parse_json_array_strict(response)
    if issues is None:
        logger.warning("[coding] review 구조 미충족 → 502(비캐시): model=%s len=%d", model, len(response or ""))
        return jsonify({"error": "model output is not the promised JSON", "code": "MODEL_OUTPUT_UNPARSED"}), 502

    summary = {
        "critical": sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "critical"),
        "warning": sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "warning"),
        "suggestion": sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "suggestion"),
        "total": len(issues),
    }

    # Security scan on input code (v2.38.0)
    security = scan_code_security(code, language if language != "auto-detect" else "python")

    result = {
        "review": response,
        "issues": issues,
        "summary": summary,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
        "quality": {"retries": retries, "validated": passed},
    }
    if security["issue_count"] > 0:
        result["security_scan"] = security

    _coding_cache.set(key, result)
    return jsonify(result)


@coding_bp.route("/api/code/refactor", methods=["POST"])
@require_auth
def code_refactor():
    """Refactor code for better quality
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [code]
          properties:
            code: {type: string}
            language: {type: string}
            goals: {type: array, items: {type: string}, description: "리팩토링 목표 (readability/performance/dry/typing)"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    goals = data.get("goals", ["readability", "typing"])
    model = data.get("model", CODING_MODEL)

    prompt = f"Refactoring goals: {', '.join(goals)}\n\nCode to refactor:\n```\n{code}\n```"

    response, tokens, duration = _code_generate(model, prompt, REFACTOR_SYSTEM,
                                                 {"temperature": 0.2, "num_predict": 3000})

    # generate와 같은 이유로 코드 블록만 싣는다(원문은 `raw`).
    return jsonify({
        "refactored": extract_code_block(response) or response,
        "raw": response,
        "goals": goals,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
    })


@coding_bp.route("/api/code/debug", methods=["POST"])
@require_auth
def code_debug():
    """Debug code with error message
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [code]
          properties:
            code: {type: string}
            error: {type: string, description: "에러 메시지/스택 트레이스"}
            language: {type: string}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    code = data.get("code", "").strip()
    error_msg = data.get("error", "")
    if not code:
        return jsonify({"error": "code is required"}), 400

    model = data.get("model", CODING_MODEL)

    prompt = f"Code:\n```\n{code}\n```"
    if error_msg:
        prompt += f"\n\nError:\n```\n{error_msg}\n```"

    response, tokens, duration = _code_generate(model, prompt, DEBUG_SYSTEM,
                                                 {"temperature": 0.1, "num_predict": 2000})

    return jsonify({
        "analysis": response,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
    })


@coding_bp.route("/api/code/test", methods=["POST"])
@require_auth
def code_test():
    """Generate tests for code
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [code]
          properties:
            code: {type: string}
            language: {type: string}
            framework: {type: string, description: "테스트 프레임워크 (pytest/jest/junit)"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    framework = data.get("framework", "auto")
    model = data.get("model", CODING_MODEL)

    prompt = f"Test framework: {framework}\n\nCode to test:\n```\n{code}\n```"

    response, tokens, duration = _code_generate(model, prompt, TEST_SYSTEM,
                                                 {"temperature": 0.2, "num_predict": 3000})

    return jsonify({
        "tests": response,
        "framework": framework,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
    })


@coding_bp.route("/api/code/explain", methods=["POST"])
@require_auth
def code_explain():
    """Explain code in Korean
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [code]
          properties:
            code: {type: string}
            level: {type: string, enum: [beginner, intermediate, advanced], default: "intermediate"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    code = data.get("code", "").strip()
    if not code:
        return jsonify({"error": "code is required"}), 400

    level = data.get("level", "intermediate")
    model = data.get("model", CODING_MODEL)

    key = _code_ck(code, level, model)
    cached = _coding_cache.get(key)
    if cached is not None:
        return jsonify(cached)

    prompt = f"Explanation level: {level}\n\nCode to explain:\n```\n{code}\n```"

    response, tokens, duration = _code_generate(model, prompt, EXPLAIN_SYSTEM,
                                                 {"temperature": 0.3, "num_predict": 2000})

    result = {
        "explanation": response,
        "level": level,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
    }
    _coding_cache.set(key, result)
    return jsonify(result)


@coding_bp.route("/api/code/convert", methods=["POST"])
@require_auth
def code_convert():
    """Convert code between languages
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [code, target]
          properties:
            code: {type: string}
            source: {type: string, description: "원본 언어 (auto-detect)"}
            target: {type: string, description: "변환 대상 언어"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    code = data.get("code", "").strip()
    target = data.get("target", "").strip()
    if not code or not target:
        return jsonify({"error": "code and target language required"}), 400

    source = data.get("source", "auto-detect")
    model = data.get("model", CODING_MODEL)

    prompt = f"Convert from {source} to {target}:\n```\n{code}\n```"

    response, tokens, duration = _code_generate(model, prompt, CONVERT_SYSTEM,
                                                 {"temperature": 0.2, "num_predict": 3000})

    # generate와 같은 이유. 변환 **대상** 언어를 힌트로 준다.
    return jsonify({
        "converted": extract_code_block(response, target) or response,
        "raw": response,
        "source": source,
        "target": target,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
    })


@coding_bp.route("/api/code/architect", methods=["POST"])
@require_auth
def code_architect():
    """Design system architecture
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [requirements]
          properties:
            requirements: {type: string, description: "시스템 요구사항"}
            stack: {type: string, description: "기술 스택 제한 (선택)"}
            scale: {type: string, description: "규모 (small/medium/large)", default: "medium"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    requirements = data.get("requirements", "").strip()
    if not requirements:
        return jsonify({"error": "requirements is required"}), 400

    stack = data.get("stack", "")
    scale = data.get("scale", "medium")
    model = data.get("model", CODING_MODEL)

    prompt = f"Requirements: {requirements}\nScale: {scale}"
    if stack:
        prompt += f"\nTech stack: {stack}"

    response, tokens, duration = _code_generate(model, prompt, ARCHITECT_SYSTEM,
                                                 {"temperature": 0.3, "num_predict": 3000})

    return jsonify({
        "architecture": response,
        "scale": scale,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
    })


@coding_bp.route("/api/code/chat", methods=["POST"])
@require_auth
def code_chat():
    """Coding assistant chat (multi-turn)
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [messages]
          properties:
            messages: {type: array, items: {type: object}}
            model: {type: string, default: "qwen2.5-coder:14b"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    messages = data.get("messages", [])
    if not messages:
        return jsonify({"error": "messages required"}), 400

    model = data.get("model", CODING_MODEL)

    # Prepend system message
    full_messages = [{"role": "system", "content": GENERATE_SYSTEM}] + messages

    start = time.time()
    try:
        r = _ollama_session.post(f"{_OLLAMA_BASE}/api/chat", json={
            "model": model, "messages": full_messages,
            "stream": False, "options": {"temperature": 0.3, "num_predict": 2000},
        }, timeout=120)
        d = r.json()
        elapsed = int((time.time() - start) * 1000)
        return jsonify({
            "message": d.get("message", {}).get("content", ""),
            "model": model,
            "tokens": d.get("eval_count", 0),
            "duration_ms": elapsed,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 503




# ---------------------------------------------------------------------------
# Code RAG — 코드베이스 지식 색인 + 증강 생성
# ---------------------------------------------------------------------------

@coding_bp.route("/api/code/rag/index", methods=["POST"])
@require_auth
def code_rag_index():
    """Index code snippet/documentation for RAG
    ---
    tags: [Coding AI - RAG]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [content]
          properties:
            id: {type: string, description: "문서 ID (자동 생성)"}
            content: {type: string, description: "코드 또는 문서 내용"}
            title: {type: string, description: "제목/파일명"}
            language: {type: string, description: "프로그래밍 언어"}
            category: {type: string, description: "분류 (code/doc/api/pattern/snippet/config)", default: "code"}
            tags: {type: array, items: {type: string}}
            project: {type: string, description: "프로젝트명"}
    responses:
      200:
        description: 색인 완료
    """
    data = request.json or {}
    doc_content = data.get("content", "").strip()
    if not doc_content:
        return jsonify({"error": "content is required"}), 400

    doc_id = data.get("id", str(uuid.uuid4()))
    title = data.get("title", "")
    language = data.get("language", "")
    category = data.get("category", "code")
    tags = data.get("tags", [])
    project = data.get("project", "")

    # Build searchable text
    search_text = f"{title}\n{doc_content}" if title else doc_content

    try:
        emb = _get_code_embedding(search_text)
        collection = _get_code_collection()
        collection.upsert(
            ids=[doc_id],
            documents=[search_text],
            embeddings=[emb],
            metadatas=[{
                "title": title or doc_id,
                "language": language,
                "category": category,
                "tags": json.dumps(tags) if tags else "[]",
                "project": project,
            }])
        return jsonify({"ok": True, "doc_id": doc_id})
    except Exception as e:
        return internal_error(e)


@coding_bp.route("/api/code/rag/index/batch", methods=["POST"])
@require_auth
def code_rag_index_batch():
    """Batch index multiple code files
    ---
    tags: [Coding AI - RAG]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [documents]
          properties:
            documents:
              type: array
              items:
                type: object
                properties:
                  id: {type: string}
                  content: {type: string}
                  title: {type: string}
                  language: {type: string}
                  category: {type: string}
            project: {type: string}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    data = request.json or {}
    docs = data.get("documents", [])
    if not docs:
        return jsonify({"error": "documents required"}), 400

    project = data.get("project", "")
    collection = _get_code_collection()
    indexed = 0
    errors = 0

    for doc in docs:
        doc_content = doc.get("content", "").strip()
        if not doc_content:
            errors += 1
            continue
        doc_id = doc.get("id", str(uuid.uuid4()))
        title = doc.get("title", "")
        search_text = f"{title}\n{doc_content}" if title else doc_content

        try:
            emb = _get_code_embedding(search_text)
            collection.upsert(
                ids=[doc_id],
                documents=[search_text],
                embeddings=[emb],
                metadatas=[{
                    "title": title or doc_id,
                    "language": doc.get("language", ""),
                    "category": doc.get("category", "code"),
                    "tags": json.dumps(doc.get("tags", [])),
                    "project": project or doc.get("project", ""),
                }])
            indexed += 1
        except Exception:
            errors += 1

    return jsonify({"indexed": indexed, "errors": errors, "total": len(docs)})


@coding_bp.route("/api/code/rag/search", methods=["POST"])
@require_auth
def code_rag_search():
    """Search code knowledge base (vector similarity)
    ---
    tags: [Coding AI - RAG]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: "검색 쿼리"}
            n_results: {type: integer, default: 5}
            language: {type: string, description: "언어 필터"}
            category: {type: string, description: "카테고리 필터"}
            project: {type: string, description: "프로젝트 필터"}
    responses:
      200:
        description: 유사 코드/문서 목록
    """
    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400

    n_results = min(data.get("n_results", 5), 20)

    where_filter = None
    filters = {}
    if data.get("language"):
        filters["language"] = data["language"]
    if data.get("category"):
        filters["category"] = data["category"]
    if data.get("project"):
        filters["project"] = data["project"]
    if filters:
        if len(filters) == 1:
            key, val = list(filters.items())[0]
            where_filter = {key: val}
        else:
            where_filter = {"$and": [{k: v} for k, v in filters.items()]}

    try:
        emb = _get_code_embedding(query)
        collection = _get_code_collection()
        results = collection.query(
            query_embeddings=[emb],
            n_results=n_results,
            where=where_filter,
        )

        items = []
        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                distance = results["distances"][0][i] if results.get("distances") else 0
                doc_text = results["documents"][0][i] if results.get("documents") else ""
                items.append({
                    "id": doc_id,
                    "title": meta.get("title", ""),
                    "language": meta.get("language", ""),
                    "category": meta.get("category", ""),
                    "project": meta.get("project", ""),
                    "relevance": round(1 - distance, 3),
                    "content": doc_text[:500],
                })

        return jsonify({"results": items, "count": len(items)})
    except Exception as e:
        return internal_error(e)


@coding_bp.route("/api/code/rag/ask", methods=["POST"])
@require_auth
def code_rag_ask():
    """RAG-augmented code generation — search context + LLM answer
    ---
    tags: [Coding AI - RAG]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [question]
          properties:
            question: {type: string, description: "코드 관련 질문"}
            n_context: {type: integer, default: 5}
            language: {type: string}
            project: {type: string}
            model: {type: string, default: "qwen2.5-coder:14b"}
    responses:
      200:
        description: RAG 기반 답변
        schema:
          type: object
          properties:
            answer: {}
            sources: {}
            model: {}
            tokens: {}
            duration_ms: {}
    """
    data = request.json or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400

    model = data.get("model", CODING_MODEL)
    n_context = min(data.get("n_context", 5), 10)

    # 1. Vector search
    context_text = ""
    sources = []
    try:
        emb = _get_code_embedding(question)
        collection = _get_code_collection()

        where_filter = None
        if data.get("language"):
            where_filter = {"language": data["language"]}
        elif data.get("project"):
            where_filter = {"project": data["project"]}

        results = collection.query(
            query_embeddings=[emb],
            n_results=n_context,
            where=where_filter,
        )

        if results and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                doc_text = results["documents"][0][i] if results.get("documents") else ""
                distance = results["distances"][0][i] if results.get("distances") else 0
                context_text += f"\n--- [{meta.get('title', doc_id)}] ---\n{doc_text[:1000]}\n"
                sources.append({
                    "id": doc_id,
                    "title": meta.get("title", ""),
                    "relevance": round(1 - distance, 3),
                })
    except Exception as e:
        logger.debug("code RAG search failed: %s", e)

    # 2. LLM with context
    system = """You are an expert software engineer. Answer based on the provided code context.
If the context contains relevant code, reference it in your answer.
If the context is not relevant, answer from your general knowledge.
Include code examples when appropriate."""

    prompt = question
    if context_text:
        prompt = f"[Reference Code Context]\n{context_text}\n\n[Question]\n{question}"

    response, tokens, duration = _code_generate(model, prompt, system,
                                                 {"temperature": 0.3, "num_predict": 2000})

    return jsonify({
        "answer": response,
        "sources": sources,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration,
    })


@coding_bp.route("/api/code/rag/delete/<doc_id>", methods=["DELETE"])
@require_auth
def code_rag_delete(doc_id):
    """Delete indexed code document
    ---
    tags: [Coding AI - RAG]
    security: [{Bearer: []}]
    parameters:
      - {name: doc_id, in: path, type: string, required: true}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    try:
        collection = _get_code_collection()
        collection.delete(ids=[doc_id])
        return jsonify({"ok": True, "deleted": doc_id})
    except Exception as e:
        return internal_error(e)


@coding_bp.route("/api/code/rag/stats")
@require_auth
def code_rag_stats():
    """Code knowledge base statistics
    ---
    tags: [Coding AI - RAG]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    try:
        collection = _get_code_collection()
        count = collection.count()

        # Get category breakdown
        if count > 0:
            all_meta = collection.get(limit=count, include=["metadatas"])
            categories = {}
            languages = {}
            projects = {}
            for meta in all_meta.get("metadatas", []):
                cat = meta.get("category", "unknown")
                categories[cat] = categories.get(cat, 0) + 1
                lang = meta.get("language", "")
                if lang:
                    languages[lang] = languages.get(lang, 0) + 1
                proj = meta.get("project", "")
                if proj:
                    projects[proj] = projects.get(proj, 0) + 1
        else:
            categories = {}
            languages = {}
            projects = {}

        return jsonify({
            "total": count,
            "by_category": categories,
            "by_language": languages,
            "by_project": projects,
        })
    except Exception as e:
        return internal_error(e)


@coding_bp.route("/api/code/models")
@require_auth
def code_models():
    """List available coding models
    ---
    tags: [Coding AI]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      400:
        description: "필수 필드 누락(예: code)"
        schema:
          type: object
          properties: {error: {type: string, example: "code is required"}}
      401:
        description: "인증 실패 — 외부 호출에 유효한 API 키가 없다(localhost는 bypass)"
        schema:
          type: object
          properties: {error: {type: string, example: "unauthorized"}}
    """
    models = [
        {"name": "qwen2.5-coder:14b", "description": "코드 특화 14B, 128K 컨텍스트, HumanEval 88%", "default": True},
        {"name": "deepseek-coder-v2:16b", "description": "MoE 16B, 가장 빠름 122tok/s, HumanEval 90%", "default": False},
        {"name": "qwen2.5:14b", "description": "범용 14B, 코드+한국어 겸용", "default": False},
    ]
    return jsonify({"models": models})
