"""Tool Calling 실행 엔진 — LLM <-> 도구 반복 루프"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.korean import force_korean, force_korean_json, korean_quality_check
from core import model_router
from core.model_router import OllamaAdapter

logger = logging.getLogger("ollama-admin")


def _safe_force_korean(text):
    """force_korean 적용 후 콘텐츠 손실이 크면 원본 유지"""
    if not text:
        return text
    filtered = force_korean(text)
    # force_korean이 80% 이상 제거했으면 원본 사용 (의학 용어 혼합 응답 보호)
    if len(filtered.strip()) < len(text.strip()) * 0.2:
        return text
    return filtered


def _run_single_tool(tc, registry, execute_action_fn, tenant):
    """단일 도구 호출 실행 — 병렬 처리용 작업 단위

    Returns:
        (fn_name, fn_args, result, duration_ms, known)
        known=False 이면 unknown tool 에러
    """
    fn_name = tc["function"]["name"]
    fn_args = tc["function"].get("arguments", {})
    action = registry.get_action(fn_name)

    if not action:
        return fn_name, fn_args, {"error": f"unknown tool: {fn_name}"}, 0, False

    tc_start = time.time()
    try:
        result, duration_ms = execute_action_fn(action, fn_args, tenant)
        result = force_korean_json(result) if result else {}
    except Exception as e:
        logger.error("Tool %s error: %s", fn_name, e)
        result = {"error": str(e)[:200]}
        duration_ms = int((time.time() - tc_start) * 1000)

    return fn_name, fn_args, result, duration_ms, True


def _run_tools_parallel(tool_calls, registry, execute_action_fn, tenant):
    """tool_calls 목록을 병렬로 실행하고 원래 순서대로 결과 반환.

    단일 도구일 때는 ThreadPoolExecutor 오버헤드 없이 직접 실행.

    Returns:
        list of (fn_name, fn_args, result, duration_ms, known)
    """
    if len(tool_calls) == 1:
        return [_run_single_tool(tool_calls[0], registry, execute_action_fn, tenant)]

    ordered = [None] * len(tool_calls)
    with ThreadPoolExecutor(max_workers=len(tool_calls)) as ex:
        futures = {
            ex.submit(_run_single_tool, tc, registry, execute_action_fn, tenant): i
            for i, tc in enumerate(tool_calls)
        }
        for f in as_completed(futures):
            ordered[futures[f]] = f.result()
    return ordered


_SYSTEM_PROMPT = """당신은 병원 AI 어시스턴트입니다. 한국어로 답변하세요.
사용 가능한 도구가 있으면 적극적으로 활용하여 정확한 정보를 제공하세요.
도구가 필요 없는 일반 질문은 직접 답변하세요."""


def run_tool_calling_chat(
    message,
    *,
    ollama_session,
    ollama_base,
    registry,
    execute_action_fn,
    tenant=None,
    session_history=None,
    log_fn=None,
):
    """Tool Calling 채팅 실행 (동기)

    Returns:
        dict or None (None이면 호출부에서 general chat 폴백)
    """
    defaults = registry.defaults
    model = defaults.get("model", "qwen3:8b")
    max_calls = defaults.get("max_tool_calls", 5)
    tool_timeout = defaults.get("tool_timeout", 30)
    total_timeout = defaults.get("total_timeout", 120)
    temperature = defaults.get("temperature", 0.3)

    tools = registry.to_ollama_tools()
    if not tools:
        return None

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if session_history:
        messages.extend(session_history)
    messages.append({"role": "user", "content": message})

    tool_call_log = []
    total_start = time.time()
    call_count = 0

    while call_count < max_calls:
        if time.time() - total_start > total_timeout:
            logger.warning("Tool calling total timeout (%ds)", total_timeout)
            break

        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
        # 모델은 tools.json(registry.defaults)이 정본 → orchestrator.tool_calling + model= 오버라이드.
        # 종전 계약: 전송 예외(타임아웃 등)=None(일반 채팅 폴백) · HTTP 오류 JSON은 message 없음→빈 최종 응답.
        #   Result.raw가 /api/chat JSON 전체 — message.tool_calls를 종전과 같이 읽는다.
        res = model_router.chat(
            "orchestrator.tool_calling", messages, model=model, tools=tools,
            options={"temperature": temperature, "num_predict": 2048}, timeout=tool_timeout,
            adapter=OllamaAdapter(base=ollama_base, session=ollama_session))
        if not res.ok and res.status is None:
            logger.error("Tool calling LLM error: %s", res.error)
            return None
        resp = res.raw

        msg = resp.get("message", {})

        # tool_calls가 없으면 최종 응답
        if not msg.get("tool_calls"):
            final_text = _safe_force_korean(msg.get("content", ""))
            return {
                "type": "tool_calling",
                "message": final_text,
                "tool_calls": tool_call_log,
                "model": model,
                "total_duration_ms": int((time.time() - total_start) * 1000),
            }

        # assistant message 추가
        messages.append(msg)

        # 한 LLM 응답에 포함된 tool_calls를 병렬 실행 후 순서 보존
        tc_list = msg["tool_calls"]
        call_count += len(tc_list)
        tool_results = _run_tools_parallel(tc_list, registry, execute_action_fn, tenant)

        for fn_name, fn_args, result, duration_ms, known in tool_results:
            if not known:
                messages.append({"role": "tool", "content": json.dumps(
                    result, ensure_ascii=False)})
                continue

            tool_call_log.append({
                "name": fn_name,
                "args": fn_args,
                "result_summary": _truncate_result(result),
                "duration_ms": duration_ms,
            })

            if log_fn:
                try:
                    log_fn(fn_name, fn_args, result, duration_ms)
                except Exception:
                    pass

            messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
            })

    # max_calls 도달 — 최종 요약 요청
    return _finalize(ollama_session, ollama_base, messages, model, temperature,
                     tool_timeout, tool_call_log, total_start)


def run_tool_calling_chat_stream(
    message,
    *,
    ollama_session,
    ollama_base,
    registry,
    execute_action_fn,
    tenant=None,
    session_history=None,
    log_fn=None,
):
    """Tool Calling 채팅 SSE 스트리밍

    Yields:
        NDJSON 문자열
    """
    defaults = registry.defaults
    model = defaults.get("model", "qwen3:8b")
    max_calls = defaults.get("max_tool_calls", 5)
    tool_timeout = defaults.get("tool_timeout", 30)
    total_timeout = defaults.get("total_timeout", 120)
    temperature = defaults.get("temperature", 0.3)

    tools = registry.to_ollama_tools()
    if not tools:
        return

    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if session_history:
        messages.extend(session_history)
    messages.append({"role": "user", "content": message})

    tool_call_log = []
    total_start = time.time()
    call_count = 0

    yield json.dumps({
        "event": "tool_calling_start",
        "model": model,
        "tools_available": len(tools),
    }) + "\n"

    while call_count < max_calls:
        if time.time() - total_start > total_timeout:
            yield json.dumps({
                "event": "timeout",
                "elapsed_ms": int((time.time() - total_start) * 1000),
            }) + "\n"
            break

        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
        # 종전 계약: 전송 예외=error 이벤트 후 종료 · HTTP 오류 JSON은 message 없음→빈 summary(위와 동일).
        res = model_router.chat(
            "orchestrator.tool_calling", messages, model=model, tools=tools,
            options={"temperature": temperature, "num_predict": 2048}, timeout=tool_timeout,
            adapter=OllamaAdapter(base=ollama_base, session=ollama_session))
        if not res.ok and res.status is None:
            yield json.dumps({
                "event": "error",
                "message": f"LLM error: {res.error[:200]}",
            }) + "\n"
            return
        resp = res.raw

        msg = resp.get("message", {})

        if not msg.get("tool_calls"):
            final_text = _safe_force_korean(msg.get("content", ""))
            yield json.dumps({
                "event": "summary",
                "type": "tool_calling",
                "message": final_text,
                "tool_calls": tool_call_log,
                "total_duration_ms": int((time.time() - total_start) * 1000),
            }) + "\n"
            return

        messages.append(msg)

        tc_list = msg["tool_calls"]
        call_count += len(tc_list)

        # 실행 시작 이벤트를 먼저 전송 (병렬 실행 전)
        for tc in tc_list:
            yield json.dumps({
                "event": "tool_call",
                "name": tc["function"]["name"],
                "args": tc["function"].get("arguments", {}),
                "status": "running",
            }) + "\n"

        # 병렬 실행 후 원래 순서로 결과 처리
        tool_results = _run_tools_parallel(tc_list, registry, execute_action_fn, tenant)

        for fn_name, fn_args, result, duration_ms, known in tool_results:
            if not known:
                messages.append({"role": "tool", "content": json.dumps(result)})
                yield json.dumps({
                    "event": "tool_call",
                    "name": fn_name,
                    "status": "error",
                    "error": "unknown tool",
                }) + "\n"
                continue

            tool_call_log.append({
                "name": fn_name,
                "args": fn_args,
                "result_summary": _truncate_result(result),
                "duration_ms": duration_ms,
            })

            if log_fn:
                try:
                    log_fn(fn_name, fn_args, result, duration_ms)
                except Exception:
                    pass

            messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False, default=str)[:4000],
            })

            yield json.dumps({
                "event": "tool_call",
                "name": fn_name,
                "status": "completed",
                "duration_ms": duration_ms,
                "preview": _truncate_result(result),
            }) + "\n"

    # max_calls 도달 — 최종 요약
    final = _finalize(ollama_session, ollama_base, messages, model, temperature,
                      tool_timeout, tool_call_log, total_start)
    if final:
        yield json.dumps({
            "event": "summary",
            **final,
        }) + "\n"


def _finalize(ollama_session, ollama_base, messages, model, temperature,
              timeout, tool_call_log, total_start):
    """max_calls 도달 시 최종 응답 생성"""
    messages.append({
        "role": "user",
        "content": "도구 호출을 마무리하고, 지금까지의 결과를 종합하여 최종 답변해주세요.",
    })
    # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
    # v2.125.15 무성 실패 일괄: HTTP 오류도 전송 예외와 같은 분기(고정 안내문) — 종전엔 빈 content로 흘렀다.
    try:
        res = model_router.chat(
            "orchestrator.tool_calling", messages, model=model,
            options={"temperature": temperature, "num_predict": 1024}, timeout=timeout,
            adapter=OllamaAdapter(base=ollama_base, session=ollama_session))
        if not res.ok:
            raise RuntimeError(res.error or ("HTTP %s" % res.status))
        final_text = _safe_force_korean(res.raw.get("message", {}).get("content", ""))
    except Exception:
        final_text = "도구 분석이 완료되었습니다. 결과를 확인해주세요."

    return {
        "type": "tool_calling",
        "message": final_text,
        "tool_calls": tool_call_log,
        "model": model,
        "total_duration_ms": int((time.time() - total_start) * 1000),
    }


def _truncate_result(result, max_len=300):
    """로그/프리뷰용 결과 요약"""
    if not result or not isinstance(result, dict):
        return result or {}
    truncated = {}
    for k, v in result.items():
        if isinstance(v, str) and len(v) > max_len:
            truncated[k] = v[:max_len] + "..."
        else:
            truncated[k] = v
    return truncated
