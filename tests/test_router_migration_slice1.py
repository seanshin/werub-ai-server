"""D1 router 이관 슬라이스 1 (v2.125.12) — 이관은 변경이 아니다: 페이로드가 종전 직접 호출과 **동일**해야 한다.

대상: core/retrieval_eval._default_ask_json · core/selfcheck.sample_answers
종전 페이로드(2026-08-19 커밋 02c969c 기준)를 여기 **그대로 적어** 어댑터가 만든 본문과 비교한다.
실패 시 어댑터 오류가 종전 raise_for_status와 같은 층에서 예외로 올라온다(빈 응답으로 흐르지 않는다).
2026-08-24 D2(v2.125.14): 라우터 페이로드 options에 repeat_penalty 1.1 핀 합류 — 기대값에 반영(core/engine_defaults).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import model_router  # noqa: E402


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body or {}
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


class _Sess:
    """어댑터에 꽂는 세션 스텁 — 마지막 payload를 잡는다."""
    def __init__(self, body):
        self.body, self.sent = body, []

    def post(self, url, json=None, timeout=None, stream=False):
        self.sent.append((url, json, timeout))
        return _Resp(200, self.body)


def _patch_adapter(monkeypatch, sess):
    monkeypatch.setattr(model_router, "get_adapter",
                        lambda provider, **kw: model_router.OllamaAdapter(session=sess))


def test_retrieval_eval_payload_identical(monkeypatch):
    from core import retrieval_eval as RE
    sess = _Sess({"response": json.dumps({"relevant": True})})
    _patch_adapter(monkeypatch, sess)
    out = RE._default_ask_json("SYS", "PROMPT", RE._REL_SCHEMA, model=None, timeout=180)
    assert out == {"relevant": True}
    url, payload, timeout = sess.sent[-1]
    assert url.endswith("/api/generate") and timeout == 180
    # 종전 직접 호출 본문 그대로 (+options의 repeat_penalty만 D2 핀(engine_defaults))
    assert payload == {"model": RE.JUDGE_MODEL, "system": "SYS", "prompt": "PROMPT",
                       "stream": False, "format": RE._REL_SCHEMA,
                       "options": {"temperature": 0, "num_predict": 512, "repeat_penalty": 1.1}}


def test_retrieval_eval_http_error_raises(monkeypatch):
    from core import retrieval_eval as RE

    class _Bad(_Sess):
        def post(self, *a, **k):
            return _Resp(500, {"error": "boom"})
    _patch_adapter(monkeypatch, _Bad({}))
    try:
        RE._default_ask_json("S", "P", RE._REL_SCHEMA)
        assert False, "HTTP 오류가 예외로 올라와야 한다"
    except RuntimeError:
        pass


def test_selfcheck_payload_identical(monkeypatch):
    from core import selfcheck as SC
    sess = _Sess({"response": " 답변 "})
    _patch_adapter(monkeypatch, sess)
    outs = SC.sample_answers("Q", n=2, model=None, system="", temperature=0.8, timeout=180)
    assert outs == ["답변", "답변"] and len(sess.sent) == 2
    url, payload, timeout = sess.sent[-1]
    assert url.endswith("/api/generate") and timeout == 180
    assert payload == {"model": SC.SAMPLE_MODEL, "system": "", "prompt": "Q", "stream": False,
                       "options": {"temperature": 0.8, "num_predict": 768, "repeat_penalty": 1.1}}  # D2 핀(engine_defaults)


def test_tasks_registered():
    r = model_router.load_routing()
    assert "eval.retrieval_judge" in r["tasks"] and "eval.selfcheck_sample" in r["tasks"]
    assert model_router.resolve("eval.selfcheck_sample")["policy"] == "local_only"


class _AdapterWith(model_router.OllamaAdapter):
    """호출자가 `OllamaAdapter(base=…, session=…)`를 직접 만드는 경로용 — 세션만 스텁으로 바꾼다."""
    def __init__(self, sess, base=None, session=None):
        super().__init__(base=base, session=sess)



# ── 슬라이스 2: routes/rag.py · routes/notice.py · routes/medical_advanced.py ──────────────
def test_notice_llm_json_payload_identical(monkeypatch):
    from routes import notice as N
    sess = _Sess({"response": json.dumps({"risk": "LOW"})})
    monkeypatch.setattr(model_router, "OllamaAdapter", lambda base=None, session=None: _AdapterWith(sess, base))
    out = N._llm_json("P", "S", "qwen2.5:14b", timeout=120)
    assert out == {"risk": "LOW"}
    url, payload, timeout = sess.sent[-1]
    assert payload == {"model": "qwen2.5:14b", "prompt": "P", "system": "S", "stream": False,
                       "format": "json", "options": {"temperature": 0.2, "num_predict": 900, "repeat_penalty": 1.1}}  # D2 핀(engine_defaults)
    assert timeout == 120


def test_rag_answer_payload_and_error_contract(monkeypatch):
    from routes import rag as R
    sess = _Sess({"response": "답"})
    monkeypatch.setattr(model_router, "OllamaAdapter", lambda base=None, session=None: _AdapterWith(sess, base))
    # 함수 본문 일부만 재현: 라우터 호출 인자 계약(모델·옵션·타임아웃)
    res = model_router.generate("rag.answer", "PROMPT", model="qwen2.5:7b", system="SYS",
                                options={"temperature": 0.3, "num_predict": 300}, timeout=120,
                                adapter=model_router.OllamaAdapter(base="http://x", session=None))
    assert res.text == "답"
    assert sess.sent[-1][1] == {"model": "qwen2.5:7b", "prompt": "PROMPT", "system": "SYS", "stream": False,
                               "options": {"temperature": 0.3, "num_predict": 300, "repeat_penalty": 1.1}}  # D2 핀(engine_defaults)
    assert model_router.resolve("rag.answer")["model"] == "qwen2.5:7b"     # 종전 기본값 그대로


def test_medical_structured_report_payload_and_status_contract(monkeypatch):
    sess = _Sess({"response": "{}"})
    res = model_router.generate("medical.structured_report", "P", model="medgemma:4b", system="S",
                                images=["b64"], options={"num_predict": 2000, "temperature": 0.2},
                                timeout=180, adapter=model_router.OllamaAdapter(base="http://x", session=sess))
    assert res.ok and sess.sent[-1][1] == {"model": "medgemma:4b", "prompt": "P", "system": "S", "images": ["b64"],
                                           "stream": False, "options": {"num_predict": 2000, "temperature": 0.2, "repeat_penalty": 1.1}}  # D2 핀(engine_defaults)

    class _Bad(_Sess):
        def post(self, *a, **k):
            return _Resp(503, {"error": "busy"})
    res = model_router.generate("medical.structured_report", "P", model="medgemma:4b",
                                adapter=model_router.OllamaAdapter(base="http://x", session=_Bad({})))
    assert not res.ok and res.status == 503      # 호출자는 이걸로 502+status를 만든다(종전 계약)
