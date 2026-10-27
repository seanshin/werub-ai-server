"""erp-notice-rag Day 2 테스트 — 수명주기 5종 API (멱등/검증/해시/개정/폐지).

전 플랫폼, 인증은 BP 단독 테스트라 미적용 (운영은 app.py _blueprint_auth — 등록부에서 부착).
"""
import pytest
from flask import Flask

from core.notice_indexer import content_hash


@pytest.fixture()
def client(tmp_path):
    import routes.notice as notice_mod
    app = Flask(__name__)
    app.register_blueprint(notice_mod.notice_bp)
    notice_mod.init_notice(str(tmp_path / "notice.db"))
    yield app.test_client()
    notice_mod.init_notice(None)


BODY = "제1조(목적) 이 고시는 요양급여 기준을 정한다.\n제2조(시행) 2026년 7월 1일부터 시행한다."

def _payload(**over):
    p = {
        "erp_ref_id": "ERP-2026-0612", "revision_no": 1,
        "notice_type": "REVIEW_CRITERIA",
        "title": "초음파 삭감 기준 변경", "body": BODY,
        "effective_from": "2026-07-01",
        "client_request_id": "erp-req-1",
    }
    p.update(over)
    return p


# ── 등록 + 멱등 ────────────────────────────────────────────

def test_create_202_with_job(client):
    r = client.post("/api/notice", json=_payload())
    assert r.status_code == 202
    d = r.get_json()
    assert d["id"].startswith("ntc-")
    assert d["index_job"]["status"] == "pending" and d["index_job"]["mode"] == "nightly"


def test_idempotent_same_hash_200(client):
    first = client.post("/api/notice", json=_payload()).get_json()
    r = client.post("/api/notice", json=_payload())
    assert r.status_code == 200
    d = r.get_json()
    assert d["idempotent"] is True and d["id"] == first["id"]


def test_duplicate_different_content_409(client):
    client.post("/api/notice", json=_payload())
    r = client.post("/api/notice", json=_payload(body=BODY + "\n변경된 내용"))
    assert r.status_code == 409


def test_validation_errors(client):
    assert client.post("/api/notice", json=_payload(notice_type="WRONG")).status_code == 400
    assert client.post("/api/notice", json={"title": "x"}).status_code == 400  # 필수 누락
    assert client.post("/api/notice", json=_payload(body="x" * (1024 * 1024 + 10))).status_code == 413
    assert client.post("/api/notice", json=_payload(mode="now")).status_code == 400


def test_hash_verify(client):
    ok = client.post("/api/notice", json=_payload(content_hash=content_hash(BODY)))
    assert ok.status_code == 202
    bad = client.post("/api/notice", json=_payload(erp_ref_id="ERP-2", content_hash="sha256:" + "0" * 64))
    assert bad.status_code == 422


def test_immediate_mode(client):
    r = client.post("/api/notice", json=_payload(mode="immediate"))
    assert r.get_json()["index_job"]["mode"] == "immediate"


# ── 개정 ───────────────────────────────────────────────────

def test_revise_supersedes(client):
    nid = client.post("/api/notice", json=_payload()).get_json()["id"]
    r = client.put(f"/api/notice/{nid}", json=_payload(revision_no=2, body=BODY + "\n제3조(추가) 신설."))
    assert r.status_code == 202
    d = r.get_json()
    assert d["supersedes"] == nid and d["revision_no"] == 2
    assert d["index_job"] and d["remove_job"]
    # 구버전 superseded + 새 revision 조회 가능
    import routes.notice as nm
    conn = nm._db()
    old = conn.execute("SELECT status FROM notices WHERE id=?", (nid,)).fetchone()
    new = conn.execute("SELECT status, revision_no FROM notices WHERE id=?", (d["id"],)).fetchone()
    assert old["status"] == "superseded"
    assert new["status"] == "active" and new["revision_no"] == 2


def test_revise_guards(client):
    nid = client.post("/api/notice", json=_payload()).get_json()["id"]
    # revision 역행 금지
    assert client.put(f"/api/notice/{nid}", json=_payload(revision_no=1)).status_code == 409
    # erp_ref_id 불변
    assert client.put(f"/api/notice/{nid}", json=_payload(revision_no=2, erp_ref_id="OTHER")).status_code == 400
    # 미존재
    assert client.put("/api/notice/ntc-nope", json=_payload(revision_no=2)).status_code == 404


# ── 폐지 / 삭제 ────────────────────────────────────────────

def test_revoke_idempotent(client):
    nid = client.post("/api/notice", json=_payload()).get_json()["id"]
    r1 = client.post(f"/api/notice/{nid}/revoke", json={})
    assert r1.status_code == 200 and r1.get_json()["status"] == "revoked"
    r2 = client.post(f"/api/notice/{nid}/revoke", json={})
    assert r2.status_code == 200 and r2.get_json()["idempotent"] is True


def test_delete(client):
    nid = client.post("/api/notice", json=_payload()).get_json()["id"]
    assert client.delete(f"/api/notice/{nid}").get_json()["deleted"] is True
    assert client.delete(f"/api/notice/{nid}").status_code == 404


# ── 일괄 ───────────────────────────────────────────────────

def test_batch_partial(client):
    items = [_payload(erp_ref_id=f"ERP-B{i}") for i in range(3)]
    items[1]["notice_type"] = "WRONG"  # 1건 고의 오류
    r = client.post("/api/notice/batch", json={"notices": items})
    assert r.status_code == 207
    d = r.get_json()
    assert len(d["accepted"]) == 2 and len(d["rejected"]) == 1
    assert d["rejected"][0]["index"] == 1


def test_batch_limits(client):
    assert client.post("/api/notice/batch", json={"notices": []}).status_code == 400
    big = [_payload(erp_ref_id=f"E{i}") for i in range(101)]
    assert client.post("/api/notice/batch", json={"notices": big}).status_code == 400


def test_batch_all_ok_202(client):
    items = [_payload(erp_ref_id=f"ERP-C{i}") for i in range(2)]
    assert client.post("/api/notice/batch", json={"notices": items}).status_code == 202
