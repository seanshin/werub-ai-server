"""Heavy tier(하이브리드 대형 모델) 회귀 테스트 — route 계층.

게이트·입력검증·single-flight·상주복원·status/settings·num_gpu 오프로드 주입.
실행: venv/bin/python -m pytest tests/test_heavy.py -q
"""
import json
import os
import sys
import threading

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _FakeResp:
    def __init__(self, status=200, json_data=None, lines=None):
        self.status_code = status
        self._json = json_data or {}
        self._lines = lines or []

    def json(self):
        return self._json

    def iter_lines(self):
        for ln in self._lines:
            yield ln

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeRequests:
    def __init__(self):
        self.calls = []
        self.reachable = True

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if not self.reachable:
            raise ConnectionError("down")
        if kw.get("stream"):
            return _FakeResp(200, lines=[
                json.dumps({"message": {"content": "안녕"}, "done": False}).encode(),
                json.dumps({"message": {"content": "하세요"}, "done": True}).encode()])
        return _FakeResp(200, json_data={"message": {"content": "안녕하세요"}, "done": True})

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if not self.reachable:
            raise ConnectionError("down")
        return _FakeResp(200, json_data={"models": [
            {"name": "qwen3:30b-a3b", "size_vram": 13_600_000_000, "size": 19_700_000_000}]})


class _FakeGPU:
    def __init__(self):
        self.reloaded = threading.Event()

    def _reload_llm(self):
        self.reloaded.set()


@pytest.fixture()
def ctx(monkeypatch):
    import routes.heavy as H

    cfg = {"heavy_enabled": False, "heavy_model": "qwen3:30b-a3b",
           "heavy_keep_alive": "0", "heavy_num_gpu": None, "default_model": "qwen2.5:14b"}
    gpu = _FakeGPU()
    H.init_heavy(load_config=lambda: cfg, save_config=lambda c: cfg.update(c),
                 ollama_base="http://127.0.0.1:11434", gpu_manager=gpu,
                 is_admin_request=lambda: True)
    fake = _FakeRequests()
    monkeypatch.setattr(H, "http_requests", fake)
    # 락 초기화(테스트 간 격리)
    if H._heavy_lock.locked():
        H._heavy_lock.release()

    app = Flask(__name__)
    app.register_blueprint(H.heavy_bp)
    return type("Ctx", (), {"client": app.test_client(), "cfg": cfg, "fake": fake,
                            "gpu": gpu, "H": H})()


# ── 게이트 ────────────────────────────────────────────────────────────────

def test_heavy_disabled_400(ctx):
    r = ctx.client.post("/api/chat/heavy", json={"prompt": "hi"})
    assert r.status_code == 400
    assert "disabled" in r.get_json()["error"]


def test_heavy_missing_input_400(ctx):
    ctx.cfg["heavy_enabled"] = True
    r = ctx.client.post("/api/chat/heavy", json={})
    assert r.status_code == 400
    assert "required" in r.get_json()["error"]


# ── 비스트림 + 상주 복원 ──────────────────────────────────────────────────

def test_heavy_nonstream_ok_and_restores(ctx):
    ctx.cfg["heavy_enabled"] = True
    r = ctx.client.post("/api/chat/heavy", json={"prompt": "안녕", "stream": False})
    assert r.status_code == 200
    assert r.get_json()["message"]["content"] == "안녕하세요"
    # heavy 모델로 호출됐는지
    post = [c for c in ctx.fake.calls if c[0] == "POST"][0]
    assert post[2]["json"]["model"] == "qwen3:30b-a3b"
    assert post[2]["json"]["keep_alive"] == "0"
    # 상주 복원(백그라운드) 발생
    assert ctx.gpu.reloaded.wait(timeout=2), "resident not restored"
    # 락 해제됨
    assert not ctx.H._heavy_lock.locked()


def test_heavy_prompt_becomes_messages(ctx):
    ctx.cfg["heavy_enabled"] = True
    ctx.client.post("/api/chat/heavy", json={"prompt": "질문", "system": "너는 전문가", "stream": False})
    msgs = ctx.fake.calls[0][2]["json"]["messages"]
    assert msgs[0] == {"role": "system", "content": "너는 전문가"}
    assert msgs[-1] == {"role": "user", "content": "질문"}


def test_heavy_model_override(ctx):
    ctx.cfg["heavy_enabled"] = True
    ctx.client.post("/api/chat/heavy", json={"prompt": "hi", "model": "medgemma:27b", "stream": False})
    assert ctx.fake.calls[0][2]["json"]["model"] == "medgemma:27b"


# ── 스트리밍 ──────────────────────────────────────────────────────────────

def test_heavy_stream_passthrough(ctx):
    ctx.cfg["heavy_enabled"] = True
    r = ctx.client.post("/api/chat/heavy", json={"prompt": "안녕"})
    assert r.status_code == 200
    # NDJSON 패스스루 — 라인 파싱해 content 결합
    lines = [json.loads(l) for l in r.get_data(as_text=True).splitlines() if l.strip()]
    content = "".join(o.get("message", {}).get("content", "") for o in lines)
    assert content == "안녕하세요" and lines[-1]["done"] is True
    assert ctx.gpu.reloaded.wait(timeout=2)  # 스트림 종료 후 복원


# ── single-flight ─────────────────────────────────────────────────────────

def test_heavy_single_flight_409(ctx):
    ctx.cfg["heavy_enabled"] = True
    ctx.H._heavy_lock.acquire()  # 다른 요청이 처리중인 상황 모사
    try:
        r = ctx.client.post("/api/chat/heavy", json={"prompt": "hi", "stream": False})
        assert r.status_code == 409
    finally:
        ctx.H._heavy_lock.release()


# ── num_gpu 하이브리드 오프로드 주입 ─────────────────────────────────────────

def test_heavy_num_gpu_injected(ctx):
    ctx.cfg["heavy_enabled"] = True
    ctx.cfg["heavy_num_gpu"] = 20
    ctx.client.post("/api/chat/heavy", json={"prompt": "hi", "stream": False})
    assert ctx.fake.calls[0][2]["json"]["options"]["num_gpu"] == 20


# ── status / settings ─────────────────────────────────────────────────────

def test_heavy_status(ctx):
    ctx.cfg["heavy_enabled"] = True
    d = ctx.client.get("/api/heavy/status").get_json()
    assert d["enabled"] is True and d["heavy_model"] == "qwen3:30b-a3b"
    assert d["resident_model"] == "qwen2.5:14b"
    assert d["loaded"][0]["name"] == "qwen3:30b-a3b"
    assert d["loaded"][0]["cpu_offload_pct"] == 31  # 13.6/19.7 → 31% CPU


def test_heavy_settings_toggle(ctx):
    r = ctx.client.post("/api/heavy/settings",
                        json={"heavy_enabled": True, "heavy_model": "gemma4:26b", "heavy_num_gpu": 24})
    s = r.get_json()["settings"]
    assert s["heavy_enabled"] is True and s["heavy_model"] == "gemma4:26b"
    assert s["heavy_num_gpu"] == 24
    assert ctx.cfg["heavy_enabled"] is True
