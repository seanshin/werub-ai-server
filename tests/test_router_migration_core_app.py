"""D1 router 이관 — core/app 슬라이스 (v2.125.13) — 이관은 변경이 아니다: 페이로드가 종전 직접 호출과 **동일**해야 한다.

대상(9곳): core/tool_executor(3·tools 동반 /api/chat) · core/websocket_handler(2·스트림) · core/chat_memory(1)
        · core/meeting_pipeline(1) · core/meeting_vision(1·이미지+think) · app.py `_process_single_llm_job`(1)
종전 페이로드(2026-08-19 커밋 ccad2e1 기준)를 여기 **그대로 적어** 어댑터가 만든 본문과 비교한다.

⚠️ 어댑터는 `options`를 **항상** 싣는다(`options or {}`). 종전에 `options` 키가 없던 지점(chat_memory·
meeting_pipeline·queue 잡 옵션 없음)은 `"options": {}`가 더해진다 — Ollama에 의미 없는 키(빈 옵션=기본값)라
전송 의미는 같다. 그 외 키·값은 전부 동일해야 한다.

라우팅 표는 여기서 **가짜 표로 고정**한다 — 새 task 등록(config/model_routing.json)과 독립적으로 이관 코드의
페이로드·계약을 본다. 등록 여부는 별도 테스트(`test_new_tasks_registered_in_routing`)가 본다.
2026-08-24 D2(v2.125.14): 라우터 페이로드 options에 repeat_penalty 1.1 핀 합류 — 기대값에 반영(core/engine_defaults).
"""
import ast
import copy
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import model_router  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 이 슬라이스가 쓰는 task — 값은 광고용(모델은 전부 호출자 `model=`이 정한다)
NEW_TASKS = ["orchestrator.tool_calling", "ws.chat", "ws.generate", "chat.memory_summary",
             "meeting.summarize", "meeting.vision_frame", "queue.llm_job"]
_FAKE_ROUTING = {
    "defaults": {"policy": "local_only", "provider": "ollama"},
    "tasks": {t: {"model": "fake-model-should-be-overridden"} for t in NEW_TASKS},
}


@pytest.fixture(autouse=True)
def _fake_routing(monkeypatch):
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: dict(_FAKE_ROUTING))


class _Resp:
    def __init__(self, status=200, body=None, lines=None):
        self.status_code, self._b, self._lines = status, body or {}, lines or []
        self.text = json.dumps(self._b)

    def json(self):
        return self._b

    def iter_lines(self):
        for ln in self._lines:
            yield json.dumps(ln, ensure_ascii=False).encode("utf-8")

    def close(self):
        pass


class _Sess:
    """어댑터에 꽂는 세션 스텁 — 보낸 (url, payload, timeout, stream)을 전부 잡는다.

    `bodies`: 호출 순서대로 돌려줄 응답 본문(비스트림) — 마지막 것을 반복.
    `lines`: 스트림 NDJSON 조각들.
    """
    def __init__(self, bodies=None, lines=None, status=200):
        self.bodies, self.lines, self.status = list(bodies or [{}]), lines, status
        self.sent = []

    def post(self, url, json=None, timeout=None, stream=False):
        # messages 리스트는 호출자가 이후 턴에서 이어 붙이므로 **보낸 시점의 스냅샷**을 남긴다
        self.sent.append((url, copy.deepcopy(json), timeout, stream))
        body = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        return _Resp(self.status, body, self.lines)


class _Boom(_Sess):
    """전송 예외(타임아웃·연결 실패) 재현."""
    def post(self, *a, **k):
        raise TimeoutError("read timed out")


# ── core/tool_executor.py (3곳) ────────────────────────────────────────────────
class _Registry:
    defaults = {"model": "qwen3:8b", "max_tool_calls": 5, "tool_timeout": 30,
                "total_timeout": 120, "temperature": 0.3}
    _tools = [{"type": "function", "function": {"name": "web_search",
               "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]

    def to_ollama_tools(self):
        return self._tools

    def get_tool(self, name):
        return {"name": name} if name == "web_search" else None


def _tool_call_msg():
    return {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "web_search", "arguments": {"q": "x"}}}]}


def test_tool_executor_chat_payload_identical_with_tools(monkeypatch):
    from core import tool_executor as TE
    monkeypatch.setattr(TE, "_run_tools_parallel",
                        lambda tc_list, registry, fn, tenant: [("web_search", {"q": "x"}, {"r": 1}, 5, True)])
    sess = _Sess(bodies=[{"message": _tool_call_msg()}, {"message": {"role": "assistant", "content": "최종 답"}}])
    out = TE.run_tool_calling_chat("질문", ollama_session=sess, ollama_base="http://x:11434",
                                   registry=_Registry(), execute_action_fn=lambda *a, **k: None)
    assert out["type"] == "tool_calling" and out["message"] == "최종 답" and out["model"] == "qwen3:8b"
    assert len(out["tool_calls"]) == 1 and out["tool_calls"][0]["name"] == "web_search"
    assert len(sess.sent) == 2
    url, payload, timeout, stream = sess.sent[0]
    assert url == "http://x:11434/api/chat" and timeout == 30 and stream is False
    # 종전 직접 호출 본문 그대로(첫 턴)
    assert payload == {
        "model": "qwen3:8b",
        "messages": [{"role": "system", "content": TE._SYSTEM_PROMPT},
                     {"role": "user", "content": "질문"}],
        "tools": _Registry._tools,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048, "repeat_penalty": 1.1},
    }
    # 둘째 턴: assistant(tool_calls)·tool 메시지가 종전과 같이 누적된다 — Result.raw의 message를 그대로 붙였다
    msgs2 = sess.sent[1][1]["messages"]
    assert msgs2[2] == _tool_call_msg() and msgs2[3]["role"] == "tool"
    assert json.loads(msgs2[3]["content"]) == {"r": 1}


def test_tool_executor_transport_error_returns_none_for_general_fallback():
    """종전: 예외 → None(일반 채팅 폴백). 라우터는 예외를 Result에 싣지만 이 지점은 None을 유지해야 한다."""
    from core import tool_executor as TE
    out = TE.run_tool_calling_chat("q", ollama_session=_Boom(), ollama_base="http://x",
                                   registry=_Registry(), execute_action_fn=lambda *a, **k: None)
    assert out is None


def test_tool_executor_stream_payload_and_error_event(monkeypatch):
    from core import tool_executor as TE
    sess = _Sess(bodies=[{"message": {"role": "assistant", "content": "바로 답"}}])
    events = [json.loads(l) for l in TE.run_tool_calling_chat_stream(
        "q", ollama_session=sess, ollama_base="http://x", registry=_Registry(),
        execute_action_fn=lambda *a, **k: None)]
    assert events[0]["event"] == "tool_calling_start" and events[-1]["event"] == "summary"
    assert events[-1]["message"] == "바로 답"
    assert sess.sent[0][1] == {
        "model": "qwen3:8b",
        "messages": [{"role": "system", "content": TE._SYSTEM_PROMPT}, {"role": "user", "content": "q"}],
        "tools": _Registry._tools, "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048, "repeat_penalty": 1.1},
    }
    # 전송 예외 → error 이벤트로 종료(종전 계약)
    events = [json.loads(l) for l in TE.run_tool_calling_chat_stream(
        "q", ollama_session=_Boom(), ollama_base="http://x", registry=_Registry(),
        execute_action_fn=lambda *a, **k: None)]
    assert events[-1]["event"] == "error" and events[-1]["message"].startswith("LLM error: ")


def test_tool_executor_finalize_payload_and_fallback_text():
    from core import tool_executor as TE
    sess = _Sess(bodies=[{"message": {"content": "요약"}}])
    msgs = [{"role": "user", "content": "q"}]
    out = TE._finalize(sess, "http://x", list(msgs), "qwen3:8b", 0.3, 30, [], 0)
    assert out["message"] == "요약"
    assert sess.sent[-1][1] == {
        "model": "qwen3:8b",
        "messages": msgs + [{"role": "user",
                             "content": "도구 호출을 마무리하고, 지금까지의 결과를 종합하여 최종 답변해주세요."}],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1024, "repeat_penalty": 1.1},
    }
    assert sess.sent[-1][2] == 30
    # 종전: 예외 → 고정 안내문
    out = TE._finalize(_Boom(), "http://x", list(msgs), "qwen3:8b", 0.3, 30, [], 0)
    assert out["message"] == "도구 분석이 완료되었습니다. 결과를 확인해주세요."
    # v2.125.15 무성 실패 일괄: HTTP 오류도 같은 안내문 분기 — 종전엔 빈 content가 그대로 나갔다
    sess500 = _Sess(bodies=[{"error": "boom"}], status=500)
    out = TE._finalize(sess500, "http://x", list(msgs), "qwen3:8b", 0.3, 30, [], 0)
    assert out["message"] == "도구 분석이 완료되었습니다. 결과를 확인해주세요."


# ── core/meeting_vision.py ────────────────────────────────────────────────────
def test_meeting_vision_payload_identical(tmp_path):
    from core import meeting_vision as MV
    img = tmp_path / "f.png"
    img.write_bytes(b"\x89PNG fake")
    import base64
    b64 = base64.b64encode(b"\x89PNG fake").decode("utf-8")
    sess = _Sess(bodies=[{"message": {"content": "슬라이드 텍스트"}, "done_reason": "stop"}])
    out = MV.analyze_frames([{"path": str(img), "timestamp": 0}], ollama_session=sess, ollama_base="http://x")
    assert out and out[0]["description"] == "슬라이드 텍스트"
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://x/api/chat" and timeout == 180 and stream is False
    assert payload == {
        "model": MV.VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": "이 화면을 분석하세요. 슬라이드나 문서가 보이면 텍스트를 추출하고, "
                       "그렇지 않으면 화면 내용을 간단히 설명하세요. "
                       "반드시 한국어로 답변하세요. 3줄 이내로 간결하게.",
            "images": [b64],
        }],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 2048, "repeat_penalty": 1.1},
    }


def test_meeting_vision_http_error_skips_frame(tmp_path):
    """종전 raise_for_status → except → 프레임 건너뜀. 라우터 경로도 같은 결과(예외 전파 없음·빈 결과)."""
    from core import meeting_vision as MV
    img = tmp_path / "f.png"
    img.write_bytes(b"x")
    sess = _Sess(bodies=[{"error": "boom"}], status=500)
    out = MV.analyze_frames([{"path": str(img), "timestamp": 1}], ollama_session=sess, ollama_base="http://x")
    assert out == [] and len(sess.sent) == 1


# ── core/meeting_pipeline.py ──────────────────────────────────────────────────
def test_meeting_pipeline_payload_identical():
    from core import meeting_pipeline as MP
    sess = _Sess(bodies=[{"message": {"content": "SUMMARY: 요약"}}])
    fake = types.SimpleNamespace(_session=sess, _ollama_base="http://x:11434",
                                 _parse_llm_response=lambda raw: raw)
    raw = MP.MeetingPipeline._llm_analyze(fake, "녹취록 본문", [], {"participants": [{"name": "김팀장"}]})
    assert raw == "SUMMARY: 요약"
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://x:11434/api/chat" and stream is False and timeout >= 1
    # 종전 본문(model·messages·stream) + 어댑터가 항상 싣는 빈 options
    assert payload == {
        "model": MP.LLM_MODEL,
        "messages": [{"role": "system", "content": MP.SUMMARY_SYSTEM_PROMPT},
                     {"role": "user", "content": "참석자: 김팀장\n\n## 녹취록:\n녹취록 본문"}],
        "stream": False,
        "options": {"repeat_penalty": 1.1},
    }


def test_meeting_pipeline_http_error_raises():
    """종전 raise_for_status 층 — HTTP 오류는 예외로 올라가 파이프라인이 failed가 된다(빈 요약으로 흐르지 않는다)."""
    from core import meeting_pipeline as MP
    fake = types.SimpleNamespace(_session=_Sess(bodies=[{"error": "busy"}], status=503),
                                 _ollama_base="http://x", _parse_llm_response=lambda raw: raw)
    with pytest.raises(RuntimeError):
        MP.MeetingPipeline._llm_analyze(fake, "t", [], {})


# ── core/chat_memory.py ───────────────────────────────────────────────────────
def test_chat_memory_summary_payload_identical(monkeypatch):
    from core import chat_memory as CM
    sess = _Sess(bodies=[{"response": " 요약문 "}])
    monkeypatch.setattr(model_router, "OllamaAdapter",
                        lambda base=None, session=None: _AdapterWith(sess, base))
    monkeypatch.setattr(CM, "_summary_model", lambda: "qwen2.5:14b")

    # DB를 흉내 낸다: 12건 중 recent 10 → 2건 압축
    class _Cur:
        def __init__(self, rows=None, one=None):
            self._rows, self._one, self.lastrowid = rows or [], one, 7

        def fetchone(self):
            return self._one

        def fetchall(self):
            return self._rows

    class _Conn:
        def execute(self, sql, params=()):
            if "COUNT(*)" in sql:
                return _Cur(one=(12,))
            if "SELECT id, role, content" in sql:
                return _Cur(rows=[(1, "user", "안녕", "t1"), (2, "assistant", "네", "t2")])
            return _Cur()

        def commit(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(CM, "get_db", lambda: _Conn())

    out = CM.compress_chat_history("a" * 36, recent_turns=10)
    assert out["compressed"] is True and out["summary"] == "요약문"
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://localhost:11434/api/generate" and timeout == 60
    assert payload == {"model": "qwen2.5:14b",
                       "prompt": "다음 대화 히스토리를 간결하게 요약해주세요 (3문장 이내):\n\nuser: 안녕\nassistant: 네",
                       "stream": False, "options": {"repeat_penalty": 1.1}}


class _AdapterWith(model_router.OllamaAdapter):
    """호출자가 `OllamaAdapter(base=…)`를 세션 없이 만드는 경로용 — 세션만 스텁으로 바꾼다."""
    def __init__(self, sess, base=None, session=None):
        super().__init__(base=base, session=sess)


# ── core/websocket_handler.py (스트림 2곳) ─────────────────────────────────────
def _ws_call(fn_name, body, sess):
    """require_auth를 벗겨(`__wrapped__`) 요청 컨텍스트 안에서 핸들러를 실행, SSE 이벤트를 파싱한다."""
    flask = pytest.importorskip("flask")
    from core import websocket_handler as WS
    WS.init_websocket(ollama_session=sess, ollama_base="http://x:11434")
    app = flask.Flask("t")
    fn = getattr(WS, fn_name).__wrapped__
    with app.test_request_context("/", method="POST", json=body):
        resp = fn()
        chunks = list(resp.response)
    events = []
    for c in chunks:
        c = c.decode() if isinstance(c, bytes) else c
        for line in c.split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_ws_chat_stream_payload_identical():
    sess = _Sess(lines=[{"message": {"content": "안"}}, {"message": {"content": "녕"}},
                        {"message": {"content": ""}, "done": True, "eval_count": 2}])
    msgs = [{"role": "user", "content": "hi"}]
    ev = _ws_call("ws_chat_stream", {"model": "qwen2.5:7b", "messages": msgs, "options": {"temperature": 0.1}}, sess)
    kinds = [e["event"] for e in ev]
    assert kinds == ["start", "first_token", "token", "token", "done"]
    assert ev[-1]["total_tokens"] == 2
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://x:11434/api/chat" and timeout == 120 and stream is True
    assert payload == {"model": "qwen2.5:7b", "messages": msgs, "stream": True,
                       "options": {"temperature": 0.1, "repeat_penalty": 1.1}}


def test_ws_generate_stream_payload_identical_and_error_event():
    sess = _Sess(lines=[{"response": "A"}, {"response": "B"}, {"response": "", "done": True}])
    ev = _ws_call("ws_generate_stream", {"model": "qwen2.5:7b", "prompt": "P", "system": "S"}, sess)
    assert [e["event"] for e in ev] == ["first_token", "token", "token", "done"]
    assert ev[-1]["response"] == "AB" and ev[-1]["total_tokens"] == 2
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://x:11434/api/generate" and timeout == 120 and stream is True
    # 종전 본문 그대로: system은 기본 ""라도 항상 실린다 · options 기본 {}
    assert payload == {"model": "qwen2.5:7b", "prompt": "P", "system": "S", "stream": True,
                       "options": {"repeat_penalty": 1.1}}
    # 전송 예외 → error 이벤트 뒤 done(종전 계약)
    ev = _ws_call("ws_generate_stream", {"model": "m", "prompt": "P"}, _Boom())
    assert [e["event"] for e in ev] == ["error", "done"]


# ── app.py `_process_single_llm_job` ─────────────────────────────────────────
def _load_process_single_llm_job(sess, cfg=None):
    """app.py 전체를 import하지 않고(부팅 부작용) 함수 본문만 떼어 스텁 이름공간에서 실행한다."""
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_process_single_llm_job")
    code = ast.get_source_segment(src, fn)
    ns = {
        "time": __import__("time"), "json": json,
        "load_config": lambda: cfg or {"default_system_prompt_enabled": False},
        "build_init_system_prompt": lambda: "DEFAULT-SYS",
        "OLLAMA_BASE": "http://gpu:11434", "_ollama_session": sess,
        "logger": types.SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None),
        "_llm_response_cache": types.SimpleNamespace(set=lambda k, v: None, __len__=lambda s: 0),
    }
    exec(compile(code, "app.py:_process_single_llm_job", "exec"), ns)
    return ns["_process_single_llm_job"]


def test_app_queue_job_payload_identical():
    sess = _Sess(bodies=[{"response": "답", "eval_count": 3, "total_duration": 2_000_000}])
    fn = _load_process_single_llm_job(sess)
    job = {"id": "j1", "model": "qwen2.5:7b", "prompt": "P", "system_prompt": "S",
           "options": json.dumps({"temperature": 0.2})}
    out = fn(job)
    assert out == {"response": "답", "tokens": 3, "duration_ms": 2}
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://gpu:11434/api/generate" and timeout == 300 and stream is False
    assert payload == {"model": "qwen2.5:7b", "prompt": "P", "system": "S", "stream": False,
                       "options": {"temperature": 0.2, "repeat_penalty": 1.1}}
    # iMac 라우팅 base가 어댑터 base로 그대로 간다 · system 없음+기본 시스템 프롬프트 켜짐 · options 없음→{}
    sess2 = _Sess(bodies=[{"response": "r"}])
    fn2 = _load_process_single_llm_job(sess2, cfg={"default_system_prompt_enabled": True})
    fn2({"id": "j2", "model": "gemma2:2b", "prompt": "P", "system_prompt": None, "options": None},
        ollama_base="http://imac:11434")
    url, payload, _, _ = sess2.sent[-1]
    assert url == "http://imac:11434/api/generate"
    assert payload == {"model": "gemma2:2b", "prompt": "P", "system": "DEFAULT-SYS", "stream": False,
                       "options": {"repeat_penalty": 1.1}}


def test_app_queue_job_error_contract_raises():
    """종전: `error` 응답·전송 예외 모두 예외 → _handle_llm_error(재시도). HTTP 오류·전송 실패 둘 다 던져야 한다."""
    fn = _load_process_single_llm_job(_Sess(bodies=[{"error": "model 'x' not found"}], status=404))
    with pytest.raises(Exception) as ei:
        fn({"id": "j", "model": "x", "prompt": "P", "system_prompt": "S", "options": None})
    assert "not found" in str(ei.value)
    fn = _load_process_single_llm_job(_Boom())
    with pytest.raises(Exception):
        fn({"id": "j", "model": "x", "prompt": "P", "system_prompt": "S", "options": None})


# ── 등록·배선 ─────────────────────────────────────────────────────────────────
def test_new_tasks_registered_in_routing(monkeypatch):
    """가짜 표를 걷고 실제 표를 본다 — 미등록이면 런타임에 UnknownTask로 즉시 실패하는 지점들이다."""
    monkeypatch.undo()
    real = model_router.load_routing()
    missing = [t for t in NEW_TASKS if t not in real["tasks"]]
    assert not missing, f"config/model_routing.json에 미등록 task: {missing}"


def test_sites_wired_to_tasks():
    """각 지점이 의도한 task 이름을 쓴다(오타=런타임 UnknownTask)."""
    def _has(path, needle):
        with open(os.path.join(ROOT, path), encoding="utf-8") as f:
            return needle in f.read()
    assert _has("core/tool_executor.py", '"orchestrator.tool_calling"')
    assert _has("core/websocket_handler.py", '"ws.chat"') and _has("core/websocket_handler.py", '"ws.generate"')
    assert _has("core/chat_memory.py", '"chat.memory_summary"')
    assert _has("core/meeting_pipeline.py", '"meeting.summarize"')
    assert _has("core/meeting_vision.py", '"meeting.vision_frame"')
    assert _has("app.py", '"queue.llm_job"')
