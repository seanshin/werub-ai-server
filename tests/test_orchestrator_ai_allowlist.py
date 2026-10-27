"""오케스트레이터 `ai:` allowlist 회귀 (v2.125.17 · 보안).

**왜 이게 심각했나**: `_execute_action`이 `test_request_context(environ_base=
{"REMOTE_ADDR": "127.0.0.1"})`로 localhost를 **위조**해 디스패치한다. localhost는 인증
bypass이자 `_is_admin_request()` 통과 조건이므로, 접두사(`ai:`)만 검사하던 종전에는
워크플로 한 줄이 관리자 API를 실행했다 — `ai:/api/keys`(영구 키 발급)·`ai:/api/config`
(전 시크릿 열람 + `auth_enabled:false` 인증 킬스위치). 유효 API 키 하나면 도달 가능했다.

여기서 고정하는 것:
  ① 관리자·파괴적 엔드포인트는 **막힌다**(키·설정·모델 삭제·서비스 제어)
  ② 실사용 엔드포인트는 **통과한다**(도입으로 기능이 깨지지 않는다)
  ③ 정확 일치 — 접두 일치나 경로 순회로 우회되지 않는다
  ④ 검사가 **두 지점**에 있다(등록·디스패치) — 기존 DB 행은 등록 검증을 안 거친다
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from routes import orchestrator as O


# ── ① 관리자·파괴적 엔드포인트 차단 ────────────────────────────────────────
@pytest.mark.parametrize("ep", [
    "/api/keys",                 # 영구 API 키 발급 → 지속 접근
    "/api/config",               # 전 시크릿 열람 + auth_enabled:false
    "/api/delete",               # 모델 삭제(fleet은 전부 라이브 배선)
    "/api/pull",
    "/api/service/restart",
    "/api/queue/purge",
    "/api/medical/tenants",
    "/admin-console/../../config.json",
])
def test_admin_endpoints_are_blocked(ep):
    assert O._ai_endpoint_allowed(ep) is False, ep


# ── ② 실사용 엔드포인트는 통과 ─────────────────────────────────────────────
@pytest.mark.parametrize("ep", [
    "/api/medical/triage", "/api/medical/summarize", "/api/medical/cli/execute",
    "/api/writing/draft", "/api/rag/search", "/api/vision/describe",
    "/api/korean/spellcheck", "/api/medical-law/ask", "/api/hr-law/ask",
    "/api/notice/search",
])
def test_real_workflow_endpoints_pass(ep):
    assert O._ai_endpoint_allowed(ep) is True, ep


def test_allowlist_covers_every_ai_action_in_repo():
    """레포의 모든 `ai:` 액션이 allowlist 안에 있어야 한다 — 없으면 그 워크플로가 죽는다.

    🔑 이 테스트가 allowlist를 **살아 있게** 만든다: 새 워크플로가 새 엔드포인트를 쓰면
    여기서 빨개지고, 그때 "위조된 localhost 권한으로 실행돼도 되는가"를 묻게 된다.
    """
    import re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = set()
    for sub in ("config", "routes"):
        for dirpath, _d, files in os.walk(os.path.join(root, sub)):
            for fn in files:
                if not fn.endswith((".json", ".py")):
                    continue
                with open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace") as f:
                    found |= {m for m in re.findall(r'"ai:([^"]+)"', f.read())}
    missing = sorted(e for e in found if not O._ai_endpoint_allowed(e))
    assert missing == [], "allowlist에 없는 실사용 ai: 액션(워크플로가 죽는다): %s" % missing


# ── ③ 정확 일치 — 우회 시도 ────────────────────────────────────────────────
@pytest.mark.parametrize("ep", [
    "/api/rag/search/../../keys",     # 경로 순회
    "/api/rag/searchx",               # 접두 일치 악용
    "/api/rag/search/extra",          # 하위 경로
    "//api//keys",                    # 중복 슬래시 정규화 후에도 차단
    "",
])
def test_bypass_attempts_blocked(ep):
    assert O._ai_endpoint_allowed(ep) is False, ep


def test_query_string_and_trailing_slash_normalized():
    assert O._ai_endpoint_allowed("/api/rag/search?x=1") is True
    assert O._ai_endpoint_allowed("/api/rag/search/") is True
    assert O._ai_endpoint_allowed("//api//rag//search") is True   # 정규화 후 일치


# ── ④ 두 지점에 배선돼 있다 ────────────────────────────────────────────────
def test_registration_rejects_disallowed_action():
    errs = O._validate_workflow_def({
        "name": "w", "display_name": "w",
        "steps": [{"id": "s1", "action": "ai:/api/keys", "input": {}}],
    })
    assert any("허용되지 않은" in e for e in errs), errs


def test_registration_accepts_allowed_action():
    errs = O._validate_workflow_def({
        "name": "w", "display_name": "w",
        "steps": [{"id": "s1", "action": "ai:/api/medical/triage", "input": {}}],
    })
    assert not any("허용되지 않은" in e for e in errs), errs


def test_dispatch_blocks_disallowed_without_forging_localhost(monkeypatch):
    """디스패치가 **위조 컨텍스트를 만들기 전에** 막아야 한다."""
    def _boom(*a, **k):
        raise AssertionError("allowlist 밖인데 위조 컨텍스트가 만들어졌다")
    monkeypatch.setattr(O, "_flask_app", type("X", (), {"test_request_context": _boom})())
    result, _elapsed = O._execute_action("ai:/api/keys", {"name": "x"}, None)
    assert "error" in result and "허용되지 않은" in result["error"]
