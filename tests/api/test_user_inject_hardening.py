"""관리 화면 사용자 정보 주입·사용자 수정 API 하드닝(v2.125.67).

🔴 예전: index()가 `window.__USER__`를 문자열로 이어 붙였다 — 이름은 `"`만 빼고, permissions는 DB 원문 그대로.
   PUT /api/admin/users가 permissions 문자열·임의 역할을 검증 없이 저장했으므로
   ①JSON이 아닌 permissions → 그 사용자 화면의 인라인 스크립트 문법 오류(화면 전체 불능)
   ②`</script>`가 든 이름·권한 → 스크립트 탈출. 설정 권한은 admin뿐이라 위험은 낮지만 둘 다 막는다.
"""
import json
import re
import time


def _page_for(client, monkeypatch, tmp_path, display_name, permissions, role="operator"):
    import app as A
    monkeypatch.setattr(A, "USERS_DB_PATH", str(tmp_path / "users.db"))
    c = A._get_users_db()
    c.execute("INSERT INTO users (username, password_hash, display_name, role, permissions) VALUES (?,?,?,?,?)",
              ("u1", "x", display_name, role, permissions))
    c.commit(); c.close()
    with client.session_transaction() as s:
        s["web_logged_in"] = True; s["web_user"] = "u1"; s["web_last_active"] = time.time()
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    m = re.search(r"<script>window\.__USER__=(.*?);</script>", html, re.S)
    assert m, "사용자 주입 스크립트 없음"
    return html, m.group(1)


def test_script_breakout_in_display_name_is_escaped(client, monkeypatch, tmp_path):
    html, blob = _page_for(client, monkeypatch, tmp_path, 'x</script><script>alert(1)</script>', '{"dashboard": true}')
    assert "</script><script>alert(1)" not in html
    assert json.loads(blob.replace("<\\/", "</"))["displayName"] == 'x</script><script>alert(1)</script>'


def test_non_json_permissions_do_not_break_page(client, monkeypatch, tmp_path):
    _html, blob = _page_for(client, monkeypatch, tmp_path, "홍길동", "not-json")
    d = json.loads(blob)                       # 유효한 JSON(예전엔 `"permissions":not-json` → 문법 오류)
    assert d["permissions"] == {} and d["displayName"] == "홍길동"


def _admin_put(client, monkeypatch, tmp_path, body):
    from routes import training
    monkeypatch.setattr(training, "USERS_DB", str(tmp_path / "users2.db"))
    c = training._get_users_db()
    c.execute("INSERT OR IGNORE INTO users (username, password_hash, role, permissions) VALUES ('boss','x','admin','{}')")
    c.execute("INSERT OR IGNORE INTO users (username, password_hash, role, permissions) VALUES ('op','x','operator','{}')")
    c.commit()
    uid = c.execute("SELECT id FROM users WHERE username='op'").fetchone()["id"]
    c.close()
    with client.session_transaction() as s:
        s["web_logged_in"] = True; s["web_user"] = "boss"; s["web_role"] = "admin"; s["web_last_active"] = time.time()
    return client.put(f"/api/admin/users/{uid}", json=body), training, uid


def test_update_rejects_unknown_role(client, monkeypatch, tmp_path):
    r, training, uid = _admin_put(client, monkeypatch, tmp_path, {"role": "Admin"})
    assert r.status_code == 400
    c = training._get_users_db()
    assert c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()["role"] == "operator"


def test_update_rejects_non_object_permissions(client, monkeypatch, tmp_path):
    r, _t, _u = _admin_put(client, monkeypatch, tmp_path, {"permissions": "not-json"})
    assert r.status_code == 400
    r2, _t, _u = _admin_put(client, monkeypatch, tmp_path, {"permissions": "[1,2]"})
    assert r2.status_code == 400


def test_update_accepts_object_permissions(client, monkeypatch, tmp_path):
    r, training, uid = _admin_put(client, monkeypatch, tmp_path, {"permissions": {"dashboard": True}})
    assert r.status_code == 200
    c = training._get_users_db()
    assert json.loads(c.execute("SELECT permissions FROM users WHERE id=?", (uid,)).fetchone()["permissions"]) == {"dashboard": True}


def test_user_table_escapes_values():
    """사용자 표(관리자 화면)도 같은 클래스 — username·display_name·role은 escHtml, onclick 인자는 _jsArg."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "static/js/main.js").read_text()
    i = js.index("async function loadUsers(")
    body = js[i:js.index("\nasync function ", i + 1)]
    for v in ("escHtml(u.username)", "escHtml(u.display_name)", "escHtml(u.role)", "_jsArg(u.username)"):
        assert v in body, v
    assert "+u.username+'" not in body and "\\''+u.username" not in body
