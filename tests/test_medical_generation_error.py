"""의료 생성 실패는 200이 아니다 — 연동 검토 회신 (2026-09-27).

종전 `_medical_generate`는 모든 실패(모델 부재·타임아웃·HTTP 오류·차단기 열림)를
`("Error: …", 0, ms)` **문자열**로 돌려줬고, 호출 35곳/4파일 중 medical 핸들러는
`startswith("Error:")`를 보지 않아 실패가 **200+본문 "Error: …"**로 나갔다
(HIS는 성공 201로 넘김 · 상담 이력에 오류 문구 저장 · 사용량 과금).
부분 실패를 허용하는 호출자(비교·벤치마크)는 `except Exception`으로 받게 짜여 있었는데
예외가 안 와서 실패를 `status: ok`로 기록하고 있었다.

처치 = 예외(`MedicalGenerationError`) + `app_errorhandler` 한 곳(502/503) + SSE 가드.
"""
import json
import re
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]


class _Res:
    def __init__(self, ok, text="", tokens=0, status=200, error=None):
        self.ok, self.text, self.tokens, self.status, self.error = ok, text, tokens, status, error


class _Breaker:
    def __init__(self, can=True):
        self.can, self.ok, self.fail = can, 0, 0

    def can_execute(self):
        return self.can

    def record_success(self):
        self.ok += 1

    def record_failure(self):
        self.fail += 1


def _patch(monkeypatch, res=None, exc=None, can=True):
    from routes import medical as M
    from core import model_router
    calls = []

    def fake_generate(task, prompt, **kw):
        calls.append(task)
        if exc:
            raise exc
        return res

    monkeypatch.setattr(model_router, "generate", fake_generate)
    br = _Breaker(can)
    monkeypatch.setattr(M, "ollama_breaker", br)
    return M, br, calls


def test_http_error_raises_502(monkeypatch):
    M, br, _ = _patch(monkeypatch, res=_Res(False, status=404, error="model 'x' not found"))
    with pytest.raises(M.MedicalGenerationError) as ei:
        M._medical_generate("x", "고유-A11-404-%d" % id(monkeypatch), "s")
    assert ei.value.status == 502 and "not found" in ei.value.reason
    assert (br.ok, br.fail) == (0, 1)


def test_transport_error_raises_502(monkeypatch):
    M, br, _ = _patch(monkeypatch, exc=TimeoutError("read timed out"))
    with pytest.raises(M.MedicalGenerationError) as ei:
        M._medical_generate("x", "고유-A11-timeout-%d" % id(monkeypatch), "s")
    assert ei.value.status == 502 and "timed out" in ei.value.reason
    assert br.fail == 1


def test_circuit_open_raises_503_without_calling_model(monkeypatch):
    M, _, calls = _patch(monkeypatch, res=_Res(True, "x"), can=False)
    with pytest.raises(M.MedicalGenerationError) as ei:
        M._medical_generate("x", "고유-A11-circuit-%d" % id(monkeypatch), "s")
    assert ei.value.status == 503 and calls == []


def test_success_contract_unchanged(monkeypatch):
    M, br, _ = _patch(monkeypatch, res=_Res(True, "소견", tokens=7))
    resp, tokens, _ms = M._medical_generate("x", "고유-A11-ok-%d" % id(monkeypatch), "s")
    assert (resp, tokens) == ("소견", 7) and br.ok == 1


def test_failure_is_not_cached(monkeypatch):
    """실패 뒤 같은 요청은 다시 생성을 시도해야 한다(실패 캐시 금지 — 반복 클래스 #5)."""
    prompt = "고유-A11-nocache-%d" % id(monkeypatch)
    M, _, calls = _patch(monkeypatch, exc=ConnectionError("down"))
    with pytest.raises(M.MedicalGenerationError):
        M._medical_generate("x", prompt, "s")
    M, _, calls = _patch(monkeypatch, res=_Res(True, "회복", tokens=1))
    assert M._medical_generate("x", prompt, "s")[0] == "회복" and calls == ["medical.generate"]


def test_errorhandler_maps_to_status_json():
    """app 전역 핸들러 — medical_advanced·orchestrator·fhir 경로도 같은 변환을 받는다."""
    from routes import medical as M
    app = Flask(__name__)
    app.register_blueprint(M.medical_bp)

    @app.route("/_t/<int:code>")
    def _boom(code):
        raise M.MedicalGenerationError("model 'x' not found", status=code)

    c = app.test_client()
    for code in (502, 503):
        r = c.get("/_t/%d" % code)
        body = r.get_json()
        assert r.status_code == code
        assert body["code"] == "generation_failed" and "not found" in body["detail"]


def test_sse_guard_emits_error_event():
    """스트림은 헤더가 나간 뒤라 errorhandler가 못 닿는다 — error 이벤트로 끝나야 한다."""
    from routes import medical as M
    from routes.medical_advanced import _sse_generation_guard

    def gen():
        yield "data: {\"event\": \"step_start\"}\n\n"
        raise M.MedicalGenerationError("circuit open", status=503)

    out = list(_sse_generation_guard(gen()))
    assert len(out) == 2
    ev = json.loads(out[1][len("data: "):])
    assert ev["event"] == "error" and ev["status"] == 503


# ── 소스 스캔 — 문자열 실패 계약의 재유입 차단 ────────────────────────────────
def _func_src(path, name):
    s = (ROOT / path).read_text(encoding="utf-8")
    i = s.index("def %s(" % name)
    j = s.find("\ndef ", i + 1)
    return s[i:j if j > 0 else None]


def test_medical_generate_returns_no_error_string():
    src = _func_src("routes/medical.py", "_medical_generate")
    assert not re.search(r'return\s+f?["\']Error', src), \
        "_medical_generate가 다시 'Error: …' 문자열로 실패를 반환한다 — 핸들러가 200으로 내보낸다"


def test_all_medical_sse_streams_are_guarded():
    """`_medical_generate`를 부르는 SSE 스트림은 전부 가드를 통과해야 한다."""
    s = (ROOT / "routes/medical_advanced.py").read_text(encoding="utf-8")
    for fn in ("run_reasoning_chain_stream", "run_ensemble_chain_stream"):
        assert "_sse_generation_guard(%s(" % fn in s, fn
    auto = _func_src("routes/medical_advanced.py", "api_auto_record_stream")
    assert "_sse_generation_guard(generate())" in auto


def test_batch_counts_generation_failure_as_failed():
    src = _func_src("routes/medical.py", "medical_batch")
    assert "except MedicalGenerationError" in src and '"status": "failed"' in src
