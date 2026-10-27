"""경로 순회 봉합 회귀 (v2.125.17) — 2026-08-25 **라이브 재현**된 두 결함.

재현된 것(처치 전):
    GET /admin-console/../../config.json        → 200 · config.json 1,783B (**무인증**)
    GET /admin-console/%2e%2e/%2e%2e/config.json → 200 (인코딩 변종·nginx 정규화 우회)
    GET /api/image/view/../.secret_key?subfolder=sana → 200 · SECRET_KEY 32B
                                                       (→ 세션 위조 → 관리자 판정 통과)

🔑 **새 결함이 아니라 미적용 지점이었다.** 형제 4곳(`routes/image.py:753`·
`routes/training.py:1902/1920/1938`)은 이미 `os.path.basename`을 쓰고 있었다 —
"한 곳에서 고친 결함은 같은 클래스 전 경로를 쓸어야 한다"(CLAUDE.md 결함 클래스 7)의 반례.
그래서 이 파일은 **개별 라우트가 아니라 패턴 자체**를 고정한다(④).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── ① admin-console: base 밖은 어떤 표기로도 안 나간다 ──────────────────────
def test_admin_console_rejects_traversal(tmp_path, monkeypatch):
    import app as A

    base = os.path.join(A.BASE_DIR, "static", "admin-console")
    os.makedirs(base, exist_ok=True)
    client = A.app.test_client()
    secret_names = ("config.json", ".secret_key")
    for name in secret_names:
        if not os.path.exists(os.path.join(A.BASE_DIR, name)):
            continue
        for path in ("/admin-console/../../%s" % name,
                     "/admin-console/%2e%2e/%2e%2e/" + name,
                     "/admin-console/..%2f..%2f" + name):
            r = client.get(path)
            assert r.status_code != 200 or b'"s3_secret_key"' not in r.data, \
                "경로 순회로 %s 유출: %s" % (name, path)


def test_admin_console_still_serves_legit_asset(tmp_path):
    """봉쇄가 정상 자산까지 막으면 안 된다(하위 디렉터리 구조 유지)."""
    import app as A
    base = os.path.join(A.BASE_DIR, "static", "admin-console")
    sub = os.path.join(base, "_next", "static")
    os.makedirs(sub, exist_ok=True)
    probe = os.path.join(sub, "_pytest_probe.txt")
    with open(probe, "w", encoding="utf-8") as f:
        f.write("ok")
    try:
        r = A.app.test_client().get("/admin-console/_next/static/_pytest_probe.txt")
        assert r.status_code == 200 and r.data == b"ok"
    finally:
        os.remove(probe)


# ── ② image view: filename·subfolder 둘 다 봉쇄 ────────────────────────────
def test_image_view_basenames_filename(monkeypatch):
    from routes import image as I
    monkeypatch.setattr(I, "is_enabled", lambda: False, raising=False)
    captured = {}

    def _fake_exists(p):
        captured["path"] = p
        return False
    monkeypatch.setattr(I.os.path, "exists", _fake_exists)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(I.image_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.get("/api/image/view/../.secret_key?subfolder=sana")
    assert captured.get("path", "").endswith(os.path.join("sana_out", ".secret_key")), captured
    assert ".." not in captured.get("path", "")


def test_image_view_rejects_traversal_subfolder(monkeypatch):
    from routes import image as I
    monkeypatch.setattr(I, "is_enabled", lambda: False, raising=False)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(I.image_bp)
    app.config["TESTING"] = True
    with app.test_client() as c:
        r = c.get("/api/image/view/x.png?subfolder=../../")
    assert r.status_code == 400


# ── ③ 패턴 고정: `<path:…>`를 그대로 join하는 곳이 다시 생기면 잡는다 ───────
def test_no_unguarded_path_converter_join():
    """`<path:X>`를 받아 `os.path.join`에 **그대로** 넣는 라우트가 없어야 한다.

    허용: 조립 전에 `basename`을 취하거나, 조립 후 realpath로 base 봉쇄를 하는 경우.
    이 검사는 형태만 본다 — 새 라우트가 같은 실수를 하면 여기서 먼저 빨개진다.
    """
    import re
    offenders = []
    targets = [os.path.join(_ROOT, "app.py")]
    for dirpath, _d, files in os.walk(os.path.join(_ROOT, "routes")):
        targets += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]

    route_re = re.compile(r'@[\w.]*route\([^)]*<path:(\w+)>')
    for path in targets:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
        for m in route_re.finditer(src):
            var = m.group(1)
            # 라우트 선언 이후 60줄 안에서 그 변수의 사용을 본다
            tail = src[m.end():]
            window = "\n".join(tail.splitlines()[:60])
            joins = re.findall(r'os\.path\.join\([^)]*\b%s\b' % var, window) + \
                    re.findall(r'_os\.path\.join\([^)]*\b%s\b' % var, window)
            if not joins:
                continue
            guarded = ("basename(%s)" % var in window or "realpath" in window
                       or "safe_join" in window)
            if not guarded:
                offenders.append("%s:<path:%s>" % (os.path.relpath(path, _ROOT), var))
    assert offenders == [], "가드 없는 경로 조립: %s" % offenders
