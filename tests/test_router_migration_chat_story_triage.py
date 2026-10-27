"""D1 router 이관 (v2.125.13) — routes/story.py · routes/triage.py. 이관은 변경이 아니다.
(routes/chat.py 구획은 v2.125.15에서 파일 삭제와 함께 제거 — 미등록 레거시·import 불가·라이브 /generate·/chat은 routes/ollama.py 소관.)

종전 직접 호출 페이로드(2026-08-19 커밋 ccad2e1 기준)를 여기 **그대로 적어** 어댑터가 만든 본문과 비교한다.
- 라우팅 표는 `load_routing`을 가짜 표로 바꿔 고정한다(표 파일은 이 테스트가 편집하지 않는다).
- 세션 스텁이 payload를 잡는다. story·triage는 **호출자 세션**을 어댑터에 넘기므로 모듈 세션 자리에
  스텁을 꽂아 그 사실까지 확인한다(ChatFairQueue 게이트 우회 방지). chat.py는 세션 없이 모듈 `requests`를
  썼으므로 `model_router.OllamaAdapter`를 스텁 세션 어댑터로 바꾼다.
- 오류 계약: raise_for_status 층 → RuntimeError(chat._call_ollama·story) / 무성 빈 문자열 유지(triage).
- 스트리밍: 어댑터의 `iter_lines` 소비를 그대로 흉내 낸 NDJSON 줄로 SSE/NDJSON 이벤트를 검사.

⚠️ 어댑터는 `options` 키를 항상 싣는다(`options or {}`). 종전 chat.py 페이로드에는 `options`가 없었으므로
   그 세 곳은 `"options": {}`가 **유일한 차이**다(Ollama에겐 무옵션과 동일). 아래 기대값에 그대로 적어 둔다.
2026-08-24 D2(v2.125.14): 라우터 페이로드 options에 repeat_penalty 1.1 핀 합류 — 기대값에 반영(core/engine_defaults).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from core import model_router  # noqa: E402

_REAL_ADAPTER = model_router.OllamaAdapter

FAKE_ROUTING = {
    "tasks": {
        "chat.checked": {"model": "qwen2.5:14b"},
        "chat.generate": {"model": "qwen2.5:14b"},
        "chat.general": {"model": "qwen2.5:14b"},
        "story.generate": {"model": "qwen2.5:14b"},
        "triage.extract_symptoms": {"model": "qwen2.5:14b"},
        "triage.guidance": {"model": "qwen2.5:14b"},
    },
    "defaults": {"policy": "local_only", "provider": "ollama"},
}


@pytest.fixture(autouse=True)
def _fake_routing(monkeypatch):
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: FAKE_ROUTING)


class _Resp:
    def __init__(self, status=200, body=None, lines=None):
        self.status_code = status
        self._b = body or {}
        self._lines = lines or []
        self.text = json.dumps(self._b)
        self.closed = 0

    def json(self):
        return self._b

    def iter_lines(self):
        # 어댑터는 `for line in r.iter_lines(): if not line: continue; json.loads(line)` — bytes 줄을 준다
        for ln in self._lines:
            yield ln.encode() if isinstance(ln, str) else ln

    def close(self):
        self.closed += 1


class _Sess:
    """세션 스텁 — (url, payload, timeout, stream)을 잡는다."""
    def __init__(self, resp):
        self.resp, self.sent = resp, []

    def post(self, url, json=None, timeout=None, stream=False):
        self.sent.append((url, json, timeout, stream))
        return self.resp


class _AdapterWith(_REAL_ADAPTER):
    """호출자가 세션 없이 `OllamaAdapter(base=…)`를 만드는 경로용 — 세션만 스텁으로 바꾼다."""
    def __init__(self, sess, base=None, session=None):
        super().__init__(base=base, session=sess)


def _patch_adapter(monkeypatch, sess):
    monkeypatch.setattr(model_router, "OllamaAdapter",
                        lambda base=None, session=None: _AdapterWith(sess, base))


def _ndjson(*events):
    return [json.dumps(e, ensure_ascii=False) for e in events]


# ═════════════════════════════════════════════════════════════
# routes/story.py
# ═════════════════════════════════════════════════════════════
def _wire_story(monkeypatch, sess):
    from routes import story as S
    monkeypatch.setattr(S, "_ollama_session", sess)
    monkeypatch.setattr(S, "_OLLAMA_BASE", "http://story-base")
    return S


def test_story_generate_payload_identical(monkeypatch):
    sess = _Sess(_Resp(200, {"response": "이야기", "eval_count": 42}))
    S = _wire_story(monkeypatch, sess)
    resp, tokens, elapsed = S._generate("qwen2.5:14b", "P", "SYS", {"num_predict": 800}, timeout=90)
    assert (resp, tokens) == ("이야기", 42) and isinstance(elapsed, int)
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://story-base/api/generate" and timeout == 90 and stream is False   # 호출자 세션·base 사용
    assert payload == {"model": "qwen2.5:14b", "prompt": "P", "system": "SYS",
                       "stream": False, "options": {"num_predict": 800, "repeat_penalty": 1.1}}


def test_story_generate_options_none_becomes_pin_only(monkeypatch):
    """종전 `opts = options or {}` → D2(v2.125.14) repeat_penalty 핀 합류로 `"options": {"repeat_penalty": 1.1}`."""
    sess = _Sess(_Resp(200, {"response": "x"}))
    S = _wire_story(monkeypatch, sess)
    S._generate("qwen2.5:7b", "P", "SYS", None, timeout=30)
    assert sess.sent[-1][1] == {"model": "qwen2.5:7b", "prompt": "P", "system": "SYS",
                                "stream": False, "options": {"repeat_penalty": 1.1}}


def test_story_generate_http_error_contract(monkeypatch):
    """종전: raise_for_status → except → ("Error: …", 0, elapsed) + breaker.record_failure."""
    from core import llm_resilience as LR
    calls = {"fail": 0}
    monkeypatch.setattr(LR.ollama_breaker, "record_failure", lambda: calls.__setitem__("fail", calls["fail"] + 1))
    S = _wire_story(monkeypatch, _Sess(_Resp(500, {"error": "boom"})))
    resp, tokens, elapsed = S._generate("qwen2.5:14b", "P", "SYS", {"num_predict": 10}, timeout=30)
    assert resp.startswith("Error:") and tokens == 0 and calls["fail"] == 1


def test_story_stream_payload_and_chunks(monkeypatch):
    sess = _Sess(_Resp(200, lines=_ndjson({"response": "가", "done": False},
                                            {"response": "나", "done": False},
                                            {"response": "", "done": True, "eval_count": 5})))
    S = _wire_story(monkeypatch, sess)
    chunks = list(S._generate_stream("qwen2.5:14b", "P", "SYS", {"num_predict": 800}, timeout=200))
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://story-base/api/generate" and timeout == 200 and stream is True
    assert payload == {"model": "qwen2.5:14b", "prompt": "P", "system": "SYS",
                       "stream": True, "options": {"num_predict": 800, "repeat_penalty": 1.1}}
    # 소비자(story_generate_stream)가 보는 것: 토큰 텍스트 → done(tokens=eval_count)
    assert [(c.text, c.done, c.tokens) for c in chunks] == [("가", False, 0), ("나", False, 0), ("", True, 5)]
    assert sess.resp.closed >= 1


def test_story_stream_http_error_raises_before_first_chunk(monkeypatch):
    """종전 `r.raise_for_status()`와 같은 층 — 호출자 SSE의 error 이벤트 분기로 간다."""
    S = _wire_story(monkeypatch, _Sess(_Resp(503, {"error": "busy"})))
    with pytest.raises(RuntimeError):
        list(S._generate_stream("qwen2.5:14b", "P", "SYS", {}, timeout=10))


# ═════════════════════════════════════════════════════════════
# routes/triage.py
# ═════════════════════════════════════════════════════════════
def _wire_triage(monkeypatch, sess):
    from routes import triage as T
    monkeypatch.setattr(T, "_ollama_session", sess)
    monkeypatch.setattr(T, "_OLLAMA_BASE", "http://triage-base")
    return T


def test_triage_extract_symptoms_payload_identical(monkeypatch):
    sess = _Sess(_Resp(200, {"response": '["두통", "발열"]'}))
    T = _wire_triage(monkeypatch, sess)
    assert T._extract_symptoms_llm("머리가 아프고 열이 나요") == ["두통", "발열"]
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://triage-base/api/generate" and timeout == 15 and stream is False
    assert payload == {
        "model": "qwen2.5:14b",
        "prompt": '다음 문장에서 모든 의학적 증상 키워드를 추출하세요.\nJSON 배열만 출력: ["증상1", "증상2", ...]\n\n문장: "머리가 아프고 열이 나요"',
        "system": "증상 키워드 추출 전문가. JSON 배열만 출력. 예: [\"두통\", \"발열\"]",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 100, "repeat_penalty": 1.1},
    }


def test_triage_extract_symptoms_silent_failure_contract(monkeypatch):
    """종전 `.get("response", "")` — HTTP 오류는 빈 문자열 → 폴백 ["기타증상"]. 이관 후 동일(무성 실패 클래스 유지)."""
    T = _wire_triage(monkeypatch, _Sess(_Resp(500, {"error": "boom"})))
    assert T._extract_symptoms_llm("x") == ["기타증상"]


def test_triage_guidance_payload_identical(monkeypatch):
    sess = _Sess(_Resp(200, {"response": " 내과를 먼저 방문하세요. "}))
    T = _wire_triage(monkeypatch, sess)
    out = T._generate_guidance("두통", ["두통"], [{"department": "신경과", "confidence": 0.9}],
                               30, "M", "3일", None)
    assert out == "내과를 먼저 방문하세요."
    url, payload, timeout, stream = sess.sent[-1]
    assert url == "http://triage-base/api/generate" and timeout == 30 and stream is False
    assert payload == {
        "model": "qwen2.5:14b",
        "prompt": "환자 증상: 두통\n추출 증상: 두통\n추천 진료과: 신경과(0.9)\n나이: 30세\n기간: 3일",
        "system": "당신은 환자에게 진료과를 안내하는 AI입니다. 반드시 한국어로만 답변하세요. 2-3문장으로 간결하게 어떤 진료과를 먼저 방문해야 하는지, 이유와 함께 한국어로 안내하세요.",
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 200, "repeat_penalty": 1.1},
    }


def test_triage_guidance_silent_failure_contract(monkeypatch):
    T = _wire_triage(monkeypatch, _Sess(_Resp(500, {"error": "boom"})))
    assert T._generate_guidance("x", ["x"], [], None, None, None, None) == ""


def test_unregistered_task_is_loud_for_story(monkeypatch):
    """🔴 표에 task가 없으면 UnknownTask — story는 `Error:` 문자열로, triage는 폴백으로 **조용히** 강등된다.
    ⇒ 배포 전에 `config/model_routing.json`에 6개 task를 등록해야 한다(이 테스트는 가짜 표로 그 사실을 고정)."""
    monkeypatch.setattr(model_router, "load_routing", lambda path=None: {"tasks": {}, "defaults": {}})
    S = _wire_story(monkeypatch, _Sess(_Resp(200, {"response": "x"})))
    from core import llm_resilience as LR
    monkeypatch.setattr(LR.ollama_breaker, "record_failure", lambda: None)
    resp, _, _ = S._generate("qwen2.5:14b", "P", "SYS", {}, timeout=5)
    assert resp.startswith("Error:") and "story.generate" in resp
