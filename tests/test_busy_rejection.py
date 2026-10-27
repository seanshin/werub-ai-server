"""대기열 거절(busy)은 고장이 아니다 — v2.125.68 (2026-09-30 HIS 실측).

`ChatFairQueue`가 사용자당 동시 1·대기 60s를 넘겨 거절하면 종전엔
① `_GatedOllamaSession`이 그냥 RuntimeError → ② model_router가 문자열로 싣고 → ③ medical이 **502**로 냈고
④ 그 거절이 **5개 도메인 공용 `ollama_breaker` 실패로 세졌다**(연속 3회면 전 도메인 30초 503).
처치 = `LLMBusyError` 타입 → `Result.busy` 보존 → 브레이커 제외 → 429 + Retry-After.
음성 통제: 진짜 백엔드 실패는 여전히 브레이커를 연다.
"""
import logging

import pytest
from flask import Flask

from core import llm_resilience as LR
from core import model_router
from core.llm_resilience import LLMBusyError, CircuitBreaker

BUSY_MSG = "LLM server busy (active=1/3, wait_queue=1/20)"


class _Res:
    def __init__(self, ok, text="", tokens=0, status=200, error=None, busy=False, retry_after=None):
        self.ok, self.text, self.tokens, self.status, self.error = ok, text, tokens, status, error
        self.busy, self.retry_after, self.duration_ms, self.raw = busy, retry_after, 0, {}


class _RaisingSess:
    """post가 예외를 던지는 세션 — 게이트가 거절한 상황을 그대로 흉내 낸다."""

    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def post(self, url, **kw):
        self.calls += 1
        raise self.exc


# ── ① 게이트: 타입 있는 예외 + 사용자당 한도가 로그에 보인다 ─────────────────
def test_gate_raises_typed_busy_with_per_user_log(caplog):
    import app as A                                       # noqa: PLC0415 — Flask 앱 임포트 비용
    q = A.ChatFairQueue(max_global=3, max_per_user=1, max_wait_queue=20, wait_timeout=0.2)

    class _Inner:
        def post(self, url, **kw):
            raise AssertionError("슬롯 없이 업스트림이 불리면 안 된다")

    g = A._GatedOllamaSession(_Inner(), q, "http://ol")
    with A.app.test_request_context("/api/medical/predict/complication", environ_base={"REMOTE_ADDR": "10.9.9.9"}):
        key = A._rate_limit_key()
        assert q.try_acquire(key)                         # 같은 키가 슬롯 하나를 쥐고 있다(전역은 1/3)
        with caplog.at_level(logging.WARNING, logger="ollama-admin"):
            with pytest.raises(LLMBusyError) as ei:
                g.post("http://ol/api/generate", json={})
        q.release(key)
    e = ei.value
    assert str(e).startswith("LLM server busy (active=1/3")          # 종전 문자열 유지(로그 grep 호환)
    assert e.per_user == 1 and e.active == 1 and e.retry_after == 5
    assert isinstance(e, RuntimeError)                                # 기존 except RuntimeError는 그대로 잡는다
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "[fair-queue] busy" in log and "per_user=1/1" in log


# ── ② model_router가 종류를 보존한다 ─────────────────────────────────────
@pytest.mark.parametrize("call", ["generate", "chat"])
def test_router_result_keeps_busy_kind(call):
    ad = model_router.OllamaAdapter(base="http://ol", session=_RaisingSess(LLMBusyError(BUSY_MSG)))
    r = (ad.generate(model="m", prompt="p") if call == "generate"
         else ad.chat(model="m", messages=[{"role": "user", "content": "p"}]))
    assert r.ok is False and r.busy is True and r.retry_after == 5 and "LLM server busy" in r.error


def test_router_other_failure_is_not_busy():
    ad = model_router.OllamaAdapter(base="http://ol", session=_RaisingSess(TimeoutError("read timed out")))
    r = ad.generate(model="m", prompt="p")
    assert r.ok is False and r.busy is False and r.retry_after is None


# ── ③ 공용 브레이커: busy 3연속은 열지 않는다 / 진짜 실패 3연속은 연다 ──────
def test_breaker_not_opened_by_busy_but_by_real_failures():
    br = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    for _ in range(3):
        assert LR.record_failure_unless_busy(LLMBusyError(BUSY_MSG), br) is False
    assert br.state == br.CLOSED and br.failure_count == 0
    for _ in range(3):                                    # 음성 통제
        LR.record_failure_unless_busy(RuntimeError("HTTP 500"), br)
    assert br.state == br.OPEN


# ── ④ medical: 429 + Retry-After, 브레이커 미기록 ────────────────────────
class _Breaker:
    def __init__(self):
        self.ok = self.fail = 0

    def can_execute(self):
        return True

    def record_success(self):
        self.ok += 1

    def record_failure(self):
        self.fail += 1


def _patch_medical(monkeypatch, res):
    from routes import medical as M
    monkeypatch.setattr(model_router, "generate", lambda task, prompt, **kw: res)
    br = _Breaker()
    monkeypatch.setattr(M, "ollama_breaker", br)
    return M, br


def test_medical_busy_is_429_and_not_a_breaker_failure(monkeypatch):
    M, br = _patch_medical(monkeypatch, _Res(False, error=BUSY_MSG, busy=True, retry_after=5))
    with pytest.raises(M.MedicalGenerationError) as ei:
        M._medical_generate("x", "고유-busy-%d" % id(monkeypatch), "s")
    assert ei.value.status == 429 and ei.value.retry_after == 5     # 구: 502
    assert br.fail == 0                                               # 구: 1


def test_medical_real_failure_still_502_and_counted(monkeypatch):
    M, br = _patch_medical(monkeypatch, _Res(False, status=500, error="boom"))
    with pytest.raises(M.MedicalGenerationError) as ei:
        M._medical_generate("x", "고유-real-%d" % id(monkeypatch), "s")
    assert ei.value.status == 502 and br.fail == 1


def test_medical_dedup_waiter_gets_same_429(monkeypatch):
    # 원 요청의 실패 표식 튜플을 대기자도 그대로 받는다 — busy 정보가 4번째 칸으로 함께 간다
    from routes import medical as M
    monkeypatch.setattr(M, "dedup_call", lambda key, fn: (M._GEN_FAILED, BUSY_MSG, 12, 5))
    with pytest.raises(M.MedicalGenerationError) as ei:
        M._medical_generate("x", "고유-dedup-%d" % id(monkeypatch), "s")
    assert ei.value.status == 429 and ei.value.retry_after == 5


def test_medical_errorhandler_429_shape_matches_chat_contract():
    from routes import medical as M
    app = Flask(__name__)
    app.register_blueprint(M.medical_bp)

    @app.route("/_busy")
    def _boom():
        raise M.MedicalGenerationError(BUSY_MSG, status=429, retry_after=5)

    r = app.test_client().get("/_busy")
    body = r.get_json()
    assert r.status_code == 429 and r.headers.get("Retry-After") == "5"
    assert body["error"] == "server busy" and body["code"] == "server_busy" and body["retry_after"] == 5


def test_medical_sse_event_busy():
    from routes import medical as M
    ev = M.medical_generation_error_event(M.MedicalGenerationError(BUSY_MSG, status=429, retry_after=5))
    assert ev["code"] == "server_busy" and ev["status"] == 429 and ev["retry_after"] == 5
    ev2 = M.medical_generation_error_event(M.MedicalGenerationError("boom"))
    assert ev2["code"] == "generation_failed" and ev2["status"] == 502


# ── ⑤ 공용 브레이커를 쓰는 다른 도메인: busy는 실패로 세지 않는다 ─────────
@pytest.fixture
def breaker_counter(monkeypatch):
    calls = {"fail": 0}
    monkeypatch.setattr(LR.ollama_breaker, "record_failure", lambda: calls.__setitem__("fail", calls["fail"] + 1))
    monkeypatch.setattr(LR.ollama_breaker, "can_execute", lambda: True)
    return calls


@pytest.mark.parametrize("mod", ["story", "novel", "writing"])
@pytest.mark.parametrize("busy", [True, False])
def test_shared_breaker_domains(monkeypatch, breaker_counter, mod, busy):
    import importlib
    M = importlib.import_module(f"routes.{mod}")
    exc = LLMBusyError(BUSY_MSG) if busy else TimeoutError("read timed out")
    monkeypatch.setattr(M, "_ollama_session", _RaisingSess(exc))
    monkeypatch.setattr(M, "_OLLAMA_BASE", "http://ol")
    resp, tokens, _ = M._generate("qwen2.5:7b", f"고유-{mod}-{busy}-{id(monkeypatch)}", "SYS", {}, timeout=5)
    assert resp.startswith("Error:") and tokens == 0           # 호출자 계약(문자열) 불변
    assert breaker_counter["fail"] == (0 if busy else 1)


@pytest.mark.parametrize("busy", [True, False])
def test_counseling_chat_breaker(monkeypatch, breaker_counter, busy):
    from routes import counseling as C
    exc = LLMBusyError(BUSY_MSG) if busy else TimeoutError("read timed out")
    monkeypatch.setattr(C, "_get_session", lambda: (_RaisingSess(exc), "http://ol"))
    with pytest.raises(RuntimeError) as ei:
        C._call_ollama_chat("qwen2.5:7b", [{"role": "user", "content": "p"}])
    assert isinstance(ei.value, LLMBusyError) is busy
    assert breaker_counter["fail"] == (0 if busy else 1)


def test_counseling_route_busy_is_429_not_503(monkeypatch):
    from routes import counseling as C
    app = Flask(__name__)
    app.register_blueprint(C.counseling_bp)
    monkeypatch.setattr(C, "_get_or_create_session", lambda sid: "s1")

    def _raise(msg, sid):
        raise LLMBusyError(BUSY_MSG)
    monkeypatch.setattr(C, "_orchestrate", _raise)
    r = app.test_client().post("/api/counseling/chat", json={"message": "안녕"})
    assert r.status_code == 429 and r.headers.get("Retry-After") == "5"   # 구: 503
    assert r.get_json()["code"] == "server_busy"


# ── ⑥ 앱 전역: 핸들러 밖으로 빠져나온 busy는 500이 아니라 429 ────────────────
def test_app_level_handler_429():
    import app as A                                       # noqa: PLC0415
    with A.app.test_request_context("/x"):
        resp = A.app.make_response(A._llm_busy(LLMBusyError(BUSY_MSG)))
    assert resp.status_code == 429 and resp.headers["Retry-After"] == "5"
    assert resp.get_json()["code"] == "server_busy"
    assert A.app.error_handler_spec[None][None].get(LLMBusyError) is A._llm_busy
