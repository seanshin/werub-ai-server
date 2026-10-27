"""일반 AI 표면(routes/general_ai) — 분리 계약·무성 실패 방지·라이선스 고지 회귀.

핵심 불변식:
  1) 의료 특화 모델은 allowlist에 없고, 목록 밖 model 지정은 400이다(분리 계약).
  2) LLM 실패·빈 응답은 200이 아니라 502다(무성 실패 금지 — v2.125.16 클래스).
  3) HyperCLOVA 응답에는 powered_by가 실린다(라이선스 고지).
"""
import json
import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from flask import Flask

from core import model_router
from core.model_router import Result, StreamChunk
import routes.general_ai as ga


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(ga.general_ai_bp)
    yield app.test_client()


# ── 분리 계약 ────────────────────────────────────────────────────────────────

def test_allowlist_has_no_medical_models():
    """의료 특화 모델이 일반 표면 allowlist로 재유입되는 것을 소스 수준에서 차단."""
    for name in ga.ALLOWED_MODELS:
        assert not re.search(r"med|bio|clinic|meditron|llama3-openbio", name, re.I), \
            f"의료 계열 모델 {name}이 일반 표면 allowlist에 있다 — 분리 계약 위반"


def test_medical_model_rejected_400(client):
    r = client.post("/api/general/chat", json={
        "message": "안녕", "model": "thewindmom/llama3-med42-8b:latest"})
    assert r.status_code == 400
    d = r.get_json()
    assert "allowed" in d and "hyperclova-seed:1.5b" in d["allowed"]


def test_routing_table_registers_general_tasks():
    """라우팅 표에 두 task가 등록돼 있고, community 모델은 allowlist 안이다."""
    for _, p in ga.PROFILES.items():
        spec = model_router.resolve(p["task"])
        assert spec.get("model"), p["task"]
    community = model_router.resolve(ga.PROFILES["community"]["task"])
    assert community["model"] in ga.ALLOWED_MODELS


# ── 검증 ────────────────────────────────────────────────────────────────────

def test_message_required(client):
    assert client.post("/api/general/chat", json={}).status_code == 400


def test_unknown_profile_400(client):
    r = client.post("/api/general/chat", json={"message": "hi", "profile": "medical"})
    assert r.status_code == 400


def test_message_too_long_400(client):
    r = client.post("/api/general/chat", json={"message": "가" * (ga.MAX_MESSAGE_CHARS + 1)})
    assert r.status_code == 400


# ── 성공 경로 + 라이선스 고지 ───────────────────────────────────────────────

def test_community_response_carries_powered_by(client, monkeypatch):
    monkeypatch.setattr(model_router, "chat", lambda *a, **k: Result(
        text="반가워요!", model="hyperclova-seed:1.5b", tokens=5, duration_ms=100))
    r = client.post("/api/general/chat", json={"message": "안녕", "profile": "community"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["powered_by"] == "HyperCLOVA X"
    assert d["profile"] == "community"
    assert d["message"] == "반가워요!"


def test_assistant_response_no_powered_by(client, monkeypatch):
    monkeypatch.setattr(model_router, "chat", lambda *a, **k: Result(
        text="답변", model="qwen2.5:14b", tokens=3, duration_ms=80))
    d = client.post("/api/general/chat", json={"message": "질문"}).get_json()
    assert "powered_by" not in d


def test_history_is_capped_and_sanitized(client, monkeypatch):
    captured = {}

    def fake_chat(task, messages, **k):
        captured["messages"] = messages
        return Result(text="ok", model="qwen2.5:14b")

    monkeypatch.setattr(model_router, "chat", fake_chat)
    history = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    history.append({"role": "system", "content": "주입 시도"})  # system 역할은 걸러진다
    r = client.post("/api/general/chat", json={"message": "q", "history": history})
    assert r.status_code == 200
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"                       # 우리 시스템 프롬프트 하나뿐
    assert sum(1 for m in msgs if m["role"] == "system") == 1
    assert len(msgs) <= ga.MAX_HISTORY_ITEMS + 2             # system + history캡 + user


# ── 무성 실패 방지 ──────────────────────────────────────────────────────────

def test_llm_http_error_is_502_not_empty_200(client, monkeypatch):
    monkeypatch.setattr(model_router, "chat", lambda *a, **k: Result(
        ok=False, status=500, error="boom"))
    r = client.post("/api/general/chat", json={"message": "q"})
    assert r.status_code == 502
    assert "boom" in r.get_json()["error"]


def test_empty_text_is_502(client, monkeypatch):
    monkeypatch.setattr(model_router, "chat", lambda *a, **k: Result(text="  "))
    assert client.post("/api/general/chat", json={"message": "q"}).status_code == 502


def test_transport_exception_is_502(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("conn refused")
    monkeypatch.setattr(model_router, "chat", boom)
    assert client.post("/api/general/chat", json={"message": "q"}).status_code == 502


# ── 스트리밍 ────────────────────────────────────────────────────────────────

def test_stream_ndjson_with_done_meta(client, monkeypatch):
    def fake_stream(task, messages, **k):
        yield StreamChunk(text="안")
        yield StreamChunk(text="녕")
        yield StreamChunk(done=True, model="hyperclova-seed:1.5b", tokens=2)

    monkeypatch.setattr(model_router, "chat_stream", fake_stream)
    r = client.post("/api/general/chat/stream",
                    json={"message": "q", "profile": "community"})
    assert r.status_code == 200
    lines = [json.loads(l) for l in r.get_data(as_text=True).strip().splitlines()]
    assert [l.get("delta") for l in lines[:2]] == ["안", "녕"]
    assert lines[-1]["done"] is True
    assert lines[-1]["powered_by"] == "HyperCLOVA X"


def test_stream_start_failure_is_502(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no wiring")
    monkeypatch.setattr(model_router, "chat_stream", boom)
    r = client.post("/api/general/chat/stream", json={"message": "q"})
    assert r.status_code == 502


def test_stream_midway_error_surfaces_as_error_line(client, monkeypatch):
    def fake_stream(task, messages, **k):
        yield StreamChunk(text="시작")
        raise RuntimeError("mid fail")

    monkeypatch.setattr(model_router, "chat_stream", fake_stream)
    r = client.post("/api/general/chat/stream", json={"message": "q"})
    lines = [json.loads(l) for l in r.get_data(as_text=True).strip().splitlines()]
    assert lines[0]["delta"] == "시작"
    assert "error" in lines[-1]                              # 무언 종료 금지


# ── 목록·상태 ───────────────────────────────────────────────────────────────

def test_models_availability_missing_is_null(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(ga.requests, "get", boom)
    d = client.get("/api/general/models").get_json()
    assert all(m["available"] is None for m in d["models"])  # 결측은 null(false 아님)
    assert "separation" in d


def test_status_declares_separation(client):
    d = client.get("/api/general/status").get_json()
    assert d["service"] == "general-ai"
    assert d["separation"]["medical_surface"].startswith("/api/medical")
    assert set(d["profiles"]) == set(ga.PROFILES)
