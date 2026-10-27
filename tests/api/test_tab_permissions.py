"""탭별 권한 — 웹 세션 비관리 역할(operator·viewer). 규칙표 core/tab_permissions.py.

예전엔 역할표(ROLE_DEFAULTS)가 화면 숨김에만 쓰였고 서버는 강제하지 않았다.
공통 읽기(헤더·대시보드)는 막지 않는다 — 막으면 허용된 탭에서도 화면이 깨진다(2026-09-29 UI 호출 지도).
"""
import json

import pytest

from core.tab_permissions import ADMIN_ONLY, denied_reason, required_tabs

EXTERNAL = {"X-Forwarded-For": "203.0.113.7"}
OPERATOR = {"dashboard": True, "queue": False, "models": False, "chat": False, "generate": False,
            "knowledge": False, "image": False, "activity": False, "settings": False, "logs": False, "training": True}
VIEWER = {"dashboard": True, "queue": False, "models": False, "chat": True, "generate": False,
          "knowledge": False, "image": False, "activity": True, "settings": False, "logs": False, "training": False}


# ── 규칙표 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,need", [
    ("GET", "/api/queue/status", None),                 # 헤더·대시보드 공통
    ("GET", "/api/queue/batches", ("queue",)),
    ("POST", "/api/queue/submit", ("queue",)),
    ("GET", "/api/queue/job/abc", ("queue", "image")),
    ("POST", "/api/chat", ("chat",)),
    ("POST", "/api/generate", ("generate",)),
    ("POST", "/api/rag/search", ("knowledge",)),
    ("GET", "/api/image/view/x.png", ("image", "queue")),
    ("GET", "/api/image/sana/status", None),            # 대시보드 카드
    ("GET", "/api/image/gallery", ("image",)),
    ("GET", "/api/logs", ("logs",)),
    ("GET", "/api/training/status", ("training",)),
    ("POST", "/api/service/stop", ADMIN_ONLY),
    ("POST", "/api/models/unload", ADMIN_ONLY),
    ("GET", "/api/status", None),
    ("GET", "/api/models", None),
    ("GET", "/api/eval/status", None),
    ("GET", "/api/medical-law/corpus-status", None),
])
def test_required_tabs(method, path, need):
    assert required_tabs(method, path) == need


def test_denied_reason_roles():
    assert denied_reason("POST", "/api/queue/submit", "admin", {}) is None
    assert denied_reason("POST", "/api/queue/submit", "operator", OPERATOR) == "tab:queue"
    assert denied_reason("GET", "/api/training/status", "operator", OPERATOR) is None
    assert denied_reason("POST", "/api/chat", "viewer", VIEWER) is None
    assert denied_reason("GET", "/api/queue/job/x", "viewer", VIEWER) == "tab:queue|image"
    assert denied_reason("POST", "/api/service/restart", "viewer", VIEWER) == "admin_only"
    assert denied_reason("POST", "/api/queue/submit", "operator", None) == "tab:queue"   # 권한 모름 = 거부


# ── 요청 훅 ─────────────────────────────────────────────────────────────

def _as(client, role, perms):
    with client.session_transaction() as s:
        s["web_logged_in"] = True
        s["web_user"] = "u"
        s["web_role"] = role
        s["web_permissions"] = json.dumps(perms)


def _hook_denied(r):
    return r.status_code == 403 and (r.get_json() or {}).get("error") == "forbidden"


def test_operator_blocked_from_other_tabs(client):
    _as(client, "operator", OPERATOR)
    assert _hook_denied(client.post("/api/queue/submit", json={"model": "m", "prompt": "p"}, headers=EXTERNAL))
    assert _hook_denied(client.post("/api/chat", json={"model": "m", "messages": []}, headers=EXTERNAL))
    assert _hook_denied(client.get("/api/logs", headers=EXTERNAL))
    assert _hook_denied(client.post("/api/service/stop", headers=EXTERNAL))


def test_operator_keeps_allowed_and_shared(client):
    _as(client, "operator", OPERATOR)
    assert not _hook_denied(client.get("/api/training/status", headers=EXTERNAL))   # 허용 탭
    assert not _hook_denied(client.get("/api/queue/status", headers=EXTERNAL))      # 공통 읽기
    assert not _hook_denied(client.get("/api/status", headers=EXTERNAL))


def test_admin_local_and_bearer_not_affected(client):
    _as(client, "admin", {})
    assert not _hook_denied(client.get("/api/logs", headers=EXTERNAL))
    _as(client, "viewer", VIEWER)
    assert not _hook_denied(client.get("/api/logs"))                                  # 로컬 bypass
    assert not _hook_denied(client.get("/api/logs", headers={**EXTERNAL, "Authorization": "Bearer sk-x"}))
