"""D1 router 이관 (v2.125.13) — routes/hr_law.py · routes/medical.py · routes/medical_cli.py. 이관은 변경이 아니다.

종전 직접 호출 페이로드(2026-08-19 커밋 ccad2e1 기준)를 여기 **그대로 적어** 어댑터가 만든 본문과 비교한다.
어댑터는 `options` 키를 항상 싣는다(호출자가 안 주면 `{}`) — hr_law 스트림 사이트만 종전 본문에 options가 없었고
`"options": {}`는 Ollama에 no-op이라 그 한 키만 허용한다(아래 `_with_empty_options`).

라우팅 표(config/model_routing.json)는 이 슬라이스에서 손대지 않았으므로 여기서 **가짜 표를 꽂는다** —
표에 task가 실리기 전까지 실전은 UnknownTask다(라우팅 표 등록이 배포 전제).

오류 계약(사이트별로 다르다 — 그래서 기계적 치환이 아니다):
  · 연결 오류/비JSON 본문(어댑터가 Result(status=None)로 삼킨 것)은 종전 `except` 분기로 되돌린다.
  · HTTP 오류 JSON(`{"error":…}`)은 종전에 `response` 키가 없어 **빈 결과가 성공으로** 흘렀다(무성 실패 클래스).
    비스트림 사이트는 그 동작을 보존한다(이관은 변경이 아니다). hr_law 스트림만 라우터가 던지므로 error 이벤트로 드러난다.
2026-08-24 D2(v2.125.14): 라우터 페이로드 options에 repeat_penalty 1.1 핀 합류 — 기대값에 반영(core/engine_defaults).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from core import model_router  # noqa: E402

TASKS = ("hr_law.answer", "hr_law.rules_draft", "hr_law.rules_review",
         "medical.generate", "medical.cli_intent", "medical.cli_action")

_FAKE_ROUTING = {
    "defaults": {"policy": "local_only", "provider": "ollama"},
    # 표 값은 광고용 — 모든 사이트가 model=을 넘기므로 여기 값이 페이로드에 실리면 안 된다(아래에서 검증).
    "tasks": {t: {"model": "TABLE-MODEL-MUST-NOT-LEAK"} for t in TASKS},
}


class _Resp:
    def __init__(self, status=200, body=None, lines=None):
        self.status_code, self._b = status, body or {}
        self.text = json.dumps(self._b)
        self._lines = lines or []
        self.closed = False

    def json(self):
        return self._b

    def iter_lines(self):
        for ln in self._lines:
            yield ln

    def close(self):
        self.closed = True


class _Sess:
    """어댑터에 꽂는 세션 스텁 — 보낸 (url, payload, timeout, stream)을 잡는다."""
    def __init__(self, body=None, lines=None, status=200, raise_exc=None):
        self.body, self.lines, self.status, self.raise_exc = body, lines, status, raise_exc
        self.sent = []

    def post(self, url, json=None, timeout=None, stream=False):
        self.sent.append((url, json, timeout, stream))
        if self.raise_exc:
            raise self.raise_exc
        return _Resp(self.status, self.body, self.lines)


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: _FAKE_ROUTING)


def _wire(monkeypatch, mod, sess):
    """호출자가 `OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session)`를 만든다 — 그 세션을 스텁으로."""
    monkeypatch.setattr(mod, "_ollama_session", sess)
    monkeypatch.setattr(mod, "_OLLAMA_BASE", "http://t:11434")


def _with_empty_options(payload):
    return {**payload, "options": {}}


# ── routes/hr_law.py ─────────────────────────────────────────────────────────────
def _hr_stream_events(monkeypatch, sess, body):
    """`/api/hr-law/ask/stream` 본체를 요청 컨텍스트에서 돌려 SSE 이벤트를 모은다(인증 데코레이터는 우회)."""
    from flask import Flask
    from routes import hr_law as H
    _wire(monkeypatch, H, sess)
    monkeypatch.setattr(H, "_resolve_tenant", lambda req: None)          # __common__ → 취업규칙 DB 미조회
    monkeypatch.setattr(H, "_force_korean_token", lambda t: t)
    monkeypatch.setattr(H, "_get_hr_law_db", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(H.requests, "post",                                # RAG 검색은 실패로(경고만 남는다)
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no rag")))
    app = Flask("t")
    with app.test_request_context("/api/hr-law/ask/stream", method="POST", json=body):
        resp = H.hr_law_ask_stream.__wrapped__()
        events = []
        for raw in resp.response:
            for line in raw.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
    return events


def test_hr_law_stream_payload_identical_and_events(monkeypatch, routing):
    from routes import hr_law as H
    lines = [b'{"response":"\xea\xb0\x80","done":false}', b"",
             b'{"response":"\xeb\x82\x98","done":false}',
             b'{"response":"","done":true,"eval_count":7}']
    sess = _Sess(lines=lines)
    events = _hr_stream_events(monkeypatch, sess, {"question": "Q", "model": "qwen2.5:14b"})
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://t:11434/api/generate" and timeout == 120 and stream is True
    system = H.HR_LAW_SYSTEM_PROMPT.replace("{context}", "관련 법률 자료 없음") \
        .replace("반드시 한국어로만 답변하세요.", H.HR_LAW_LANG_PROMPTS["ko"])
    # 종전 본문 그대로(+ 어댑터 options — D2부터 repeat_penalty 1.1 핀만 실린다)
    assert payload == {"model": "qwen2.5:14b", "prompt": "Q", "system": system, "stream": True,
                       "options": {"repeat_penalty": 1.1}}
    assert [e["type"] for e in events] == ["sources", "token", "token", "done"]
    assert [e["token"] for e in events if e["type"] == "token"] == ["가", "나"]
    assert events[-1]["tokens"] == 7


def test_hr_law_stream_http_error_surfaces_as_error_event(monkeypatch, routing):
    """종전엔 HTTP 오류가 토큰도 done도 없이 무언히 끝났다 — 이관 후 error 이벤트(의도된 유일한 차이)."""
    sess = _Sess(status=500, body={"error": "boom"})
    events = _hr_stream_events(monkeypatch, sess, {"question": "Q", "model": "m"})
    assert [e["type"] for e in events] == ["sources", "error"] and "HTTP 500" in events[-1]["error"]


def test_hr_law_stream_connection_error_event_as_before(monkeypatch, routing):
    sess = _Sess(raise_exc=ConnectionError("down"))
    events = _hr_stream_events(monkeypatch, sess, {"question": "Q", "model": "m"})
    assert [e["type"] for e in events] == ["sources", "error"] and "down" in events[-1]["error"]


def _hr_json_route(monkeypatch, sess, fn_name, body):
    from flask import Flask
    from routes import hr_law as H
    _wire(monkeypatch, H, sess)
    monkeypatch.setattr(H, "_resolve_tenant", lambda req: {"id": "t1", "name": "테넌트"})
    monkeypatch.setattr(H, "_load_config", lambda: {"default_model": "cfg-model"})
    monkeypatch.setattr(H, "_get_hr_law_db", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(H.requests, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no rag")))
    app = Flask("t")
    with app.test_request_context("/x", method="POST", json=body):
        out = getattr(H, fn_name).__wrapped__()
    if isinstance(out, tuple):
        resp, code = out
    else:
        resp, code = out, 200
    return json.loads(resp.get_data(as_text=True)), code


def test_hr_law_rules_draft_payload_identical(monkeypatch, routing):
    from routes import hr_law as H
    sec = H.RULE_TEMPLATE["sections"][0]
    sess = _Sess(body={"response": "제1조 …", "eval_count": 3})
    data, code = _hr_json_route(monkeypatch, sess, "hr_law_rules_generate", {"section_id": sec["id"]})
    assert code == 200 and data["draft"] == "제1조 …"
    url, payload, timeout, stream = sess.sent[-1]
    assert timeout == 120 and stream is False
    assert payload["model"] == "cfg-model"                     # config default_model — 표 값이 새지 않는다
    assert payload["stream"] is False
    assert payload["system"] == "당신은 한국 노무사입니다. 반드시 한국어로만 답변하세요. 취업규칙 초안을 조문 형식으로 작성합니다."
    assert payload["options"] == {"temperature": 0.5, "num_predict": 3000, "repeat_penalty": 1.1}
    assert set(payload) == {"model", "prompt", "stream", "system", "options"}
    assert payload["prompt"].startswith("다음 취업규칙 섹션의 초안을 작성해주세요.")


def test_hr_law_rules_draft_error_contracts(monkeypatch, routing):
    """연결 오류·HTTP 오류 모두→500 — v2.125.15 무성 실패 일괄(종전엔 HTTP 오류가 빈 초안 200)."""
    data, code = _hr_json_route(monkeypatch, _Sess(raise_exc=ConnectionError("down")),
                                "hr_law_rules_generate", {"section_id": "work_hours"})
    assert code == 500 and data["error"] == "LLM 처리 중 오류"
    data, code = _hr_json_route(monkeypatch, _Sess(status=500, body={"error": "boom"}),
                                "hr_law_rules_generate", {"section_id": "work_hours"})
    assert code == 500 and data["error"] == "LLM 처리 중 오류"


def test_hr_law_rules_review_payload_identical(monkeypatch, routing):
    sess = _Sess(body={"response": "✅ 적합", "eval_count": 9})
    data, code = _hr_json_route(monkeypatch, sess, "hr_law_rules_review", {"text": "제1조 근로시간"})
    assert code == 200 and data["review"] == "✅ 적합" and data["compliant"] == 2 and data["tokens"] == 9
    url, payload, timeout, stream = sess.sent[-1]
    assert timeout == 180 and stream is False
    assert payload == {"model": "cfg-model", "prompt": payload["prompt"], "stream": False,
                       "options": {"temperature": 0.3, "num_predict": 3000, "repeat_penalty": 1.1}}   # system 없음 — 종전 그대로
    assert "제1조 근로시간" in payload["prompt"]


def test_hr_law_rules_review_error_contracts(monkeypatch, routing):
    """v2.125.15 무성 실패 일괄: HTTP 오류도 500 — 종전엔 "검토 실패"가 200으로 나갔다."""
    data, code = _hr_json_route(monkeypatch, _Sess(raise_exc=ConnectionError("down")),
                                "hr_law_rules_review", {"text": "규칙"})
    assert code == 500
    data, code = _hr_json_route(monkeypatch, _Sess(status=503, body={"error": "busy"}),
                                "hr_law_rules_review", {"text": "규칙"})
    assert code == 500 and data["error"] == "LLM 처리 중 오류"


# ── routes/medical.py `_medical_generate` ───────────────────────────────────────
class _Breaker:
    def __init__(self):
        self.ok, self.fail = 0, 0

    def can_execute(self):
        return True

    def record_success(self):
        self.ok += 1

    def record_failure(self):
        self.fail += 1


def _med_gen(monkeypatch, sess, prompt, **kw):
    from routes import medical as M
    _wire(monkeypatch, M, sess)
    br = _Breaker()
    monkeypatch.setattr(M, "ollama_breaker", br)
    out = M._medical_generate(model="werubmedv2", prompt=prompt, system="SYS", timeout=77, action="t", **kw)
    return out, br


def test_medical_generate_payload_identical(monkeypatch, routing):
    sess = _Sess(body={"response": "소견", "eval_count": 11})
    prompt = "고유-이관-%d" % id(monkeypatch)
    (resp, tokens, ms), br = _med_gen(monkeypatch, sess, prompt, options={"temperature": 0.2, "num_predict": 800})
    assert (resp, tokens) == ("소견", 11) and br.ok == 1 and br.fail == 0
    url, payload, timeout, stream = sess.sent[-1]
    assert timeout == 77 and stream is False
    assert payload == {"model": "werubmedv2", "prompt": prompt, "system": "SYS",
                       "stream": False, "options": {"temperature": 0.2, "num_predict": 800, "repeat_penalty": 1.1}}


def test_medical_generate_no_options_payload_identical(monkeypatch, routing):
    """options 미지정 → 종전 `opts = options or {}`에 D2 핀 합류 = `"options": {"repeat_penalty": 1.1}` (표 options가 끼어들지 않는다)."""
    sess = _Sess(body={"response": "x"})
    prompt = "고유-이관-빈옵션-%d" % id(monkeypatch)
    _med_gen(monkeypatch, sess, prompt)
    assert sess.sent[-1][1] == {"model": "werubmedv2", "prompt": prompt, "system": "SYS",
                                "stream": False, "options": {"repeat_penalty": 1.1}}


def test_medical_generate_error_contracts(monkeypatch, routing):
    """연결 오류·HTTP 오류 모두→**MedicalGenerationError(502)**+브레이커 failure.
    v2.125.15는 브레이커만 정직하게 고치고 `"Error: …"` 문자열 반환을 남겨 핸들러가 200으로
    내보냈다(연동 검토 회신) — 2026-09-27부터 예외로 올린다."""
    import pytest
    from routes.medical import MedicalGenerationError
    prompt = "고유-이관-오류-%d" % id(monkeypatch)
    with pytest.raises(MedicalGenerationError) as ei:
        _med_gen(monkeypatch, _Sess(raise_exc=ConnectionError("down")), prompt)
    assert "down" in str(ei.value) and ei.value.status == 502
    prompt2 = "고유-이관-오류2-%d" % id(monkeypatch)
    from routes import medical as M
    br = _Breaker()
    _wire(monkeypatch, M, _Sess(status=500, body={"error": "boom"}))
    monkeypatch.setattr(M, "ollama_breaker", br)
    with pytest.raises(MedicalGenerationError):
        M._medical_generate(model="werubmedv2", prompt=prompt2, system="SYS", timeout=77, action="t")
    assert (br.ok, br.fail) == (0, 1)


# ── routes/medical_cli.py ────────────────────────────────────────────────────────
def test_medical_cli_intent_payload_identical(monkeypatch, routing):
    from routes import medical_cli as C
    sess = _Sess(body={"response": '{"intent": "vital", "confidence": 0.9}'})
    _wire(monkeypatch, C, sess)
    role = next(iter(C.ROLES))
    assert C._detect_intent_llm(role, "혈압 120/80", {"sbp": 120}) == ("vital", 0.9)
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://t:11434/api/generate" and timeout == 30 and stream is False
    assert payload == {"model": "qwen2.5:14b", "prompt": payload["prompt"],
                       "system": "당신은 의도 분류기입니다. JSON만 출력하세요.",
                       "stream": False, "options": {"temperature": 0.1, "num_predict": 100, "repeat_penalty": 1.1}}
    assert payload["prompt"].startswith('사용자 입력: "혈압 120/80"')


def test_medical_cli_intent_error_contracts(monkeypatch, routing):
    from routes import medical_cli as C
    role = next(iter(C.ROLES))
    _wire(monkeypatch, C, _Sess(raise_exc=ConnectionError("down")))
    assert C._detect_intent_llm(role, "x", {}) == (None, 0.0)
    _wire(monkeypatch, C, _Sess(status=500, body={"error": "boom"}))
    assert C._detect_intent_llm(role, "x", {}) == (None, 0.0)


def test_medical_cli_action_payload_identical(monkeypatch, routing):
    from routes import medical_cli as C
    sess = _Sess(body={"response": "처방 제안"})
    _wire(monkeypatch, C, sess)
    role = next(iter(C.ROLES))
    role_name = C.ROLES[role]["name"]
    out = C._handle_llm_action(role, "prescribe", "두통", {"symptom": "두통"}, {"id": "t"})
    assert out == ({"action": "prescribe", "response": "처방 제안"}, "처방 제안", False)
    url, payload, timeout, stream = sess.sent[-1]
    assert timeout == 60 and stream is False
    assert payload == {"model": "qwen2.5:14b", "prompt": payload["prompt"],
                       "system": f"당신은 병원 {role_name}을 지원하는 AI입니다. 정확하고 간결하게 답변하세요.",
                       "stream": False, "options": {"temperature": 0.3, "num_predict": 1000, "repeat_penalty": 1.1}}
    assert payload["prompt"].startswith(f"역할: {role_name}")


def test_medical_cli_action_error_contracts(monkeypatch, routing):
    from routes import medical_cli as C
    role = next(iter(C.ROLES))
    _wire(monkeypatch, C, _Sess(raise_exc=ConnectionError("down")))
    d, msg, _ = C._handle_llm_action(role, "i", "x", {}, {})
    assert "error" in d and "down" in d["error"] and msg.startswith("오류:")
    # v2.125.15 무성 실패 일괄: HTTP 오류도 error 응답 — 종전엔 빈 response가 정상 결과로 나갔다
    _wire(monkeypatch, C, _Sess(status=500, body={"error": "boom"}))
    d, msg, _ = C._handle_llm_action(role, "i", "x", {}, {})
    assert "error" in d and msg.startswith("오류:") and "response" not in d


# ── 표 등록 여부(라우팅 json은 이 슬라이스에서 손대지 않았다 — 등록되면 통과·전까지는 xfail로 상기) ──
@pytest.mark.xfail(strict=False, reason="v2.125.13 task 6종을 config/model_routing.json에 등록해야 실전에서 UnknownTask가 안 난다")
def test_tasks_registered_in_real_table():
    r = model_router.load_routing(model_router.ROUTING_PATH)
    missing = [t for t in TASKS if t not in r["tasks"]]
    assert not missing, missing
    for t in TASKS:
        assert model_router.resolve(t, r)["policy"] == "local_only"
