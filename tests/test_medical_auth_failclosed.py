"""의료 Blueprint fail-closed 가드 회귀 (보안 P2 M4).

이 계열은 Bearer가 아니라 **X-Medical-Key**(테넌트 키)로 인증한다. `_blueprint_auth()`를
단순 강제하면 HIS가 차단되므로, "셋(로컬·Bearer/세션·X-Medical-Key) 중 아무것도 없을 때만
401"이 올바른 형태다. 여기서는 그 판정 규칙을 소스 계약으로 고정한다.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _guard_source():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    m = re.search(r"def _medical_auth\(\):.*?\n    app\.register_blueprint\(medical_bp\)", src, re.S)
    assert m, "_medical_auth 가드를 찾지 못했다"
    return m.group(0)


def test_guard_is_not_scoped_to_tenant_paths_only():
    """종전 결함: /api/medical/tenant* 변경 요청만 인증 → 임상 라우트 무인증 노출."""
    g = _guard_source()
    assert 'request.path.startswith("/api/medical/tenant")' not in g


def test_guard_accepts_tenant_key_header():
    """X-Medical-Key가 있으면 통과시켜야 한다(없으면 HIS 전면 차단)."""
    g = _guard_source()
    assert "X-Medical-Key" in g and "medical_key" in g


def test_guard_falls_back_to_blueprint_auth():
    g = _guard_source()
    assert "_blueprint_auth()" in g


def test_options_is_exempt():
    """CORS preflight는 인증 대상이 아니다."""
    g = _guard_source()
    assert 'request.method == "OPTIONS"' in g


def test_kill_switch_present():
    """운영 중 즉시 되돌릴 수 있어야 한다(재배포 없이)."""
    g = _guard_source()
    assert "MEDICAL_AUTH_ENFORCE" in g


def test_blocked_requests_are_logged():
    """차단이 조용하면 정상 호출자가 걸린 걸 알 수 없다(무성 실패 금지)."""
    g = _guard_source()
    assert "logger.warning" in g and "차단" in g
