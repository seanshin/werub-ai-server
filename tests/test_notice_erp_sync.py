"""X1 — ERP 공문 확약 API 2종 (2026-08-25).

GET /api/notice/changes?since= · POST /api/notice/sync/verify.
계약 정본: docs/01-plan/references/notice-integration-reply-to-erp.md ①·⑥ +
docs/02-design/features/erp-notice-rag.design.md §API(sync/verify 스키마·
hash_diff/missing/extra 3종 검출 = 설계 테스트 계획 #6).
백그라운드 색인은 no-op 패치로 격리(test_notice_v287 컨벤션).
실행(GPU): venv/bin/python -m pytest tests/test_notice_erp_sync.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("flask")
pytest.importorskip("requests")

from flask import Flask  # noqa: E402


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    import routes.notice as nm
    monkeypatch.setattr(nm, "process_immediate_async", lambda: None)  # 백그라운드 차단(격리)
    app = Flask(__name__)
    app.register_blueprint(nm.notice_bp)
    nm.init_notice(str(tmp_path / "notice.db"))
    yield app.test_client()
    nm.init_notice(None)


def _create(client, **over):
    p = {"erp_ref_id": "ERP-1", "notice_type": "REVIEW_CRITERIA", "title": "t",
         "body": "제1조(목적) 본문.\n제2조 시행.", "effective_from": "2026-07-01"}
    p.update(over)
    r = client.post("/api/notice", json=p)
    assert r.status_code in (200, 202), r.get_json()
    return r.get_json()


def _hash_of(body_text):
    from core.notice_indexer import content_hash
    return content_hash(body_text)


# ── GET /api/notice/changes ────────────────────────────────────────────────

def test_changes_since_required(setup):
    assert setup.get("/api/notice/changes").status_code == 400
    assert setup.get("/api/notice/changes?since=not-a-ts").status_code == 400


def test_changes_returns_rows_after_since(setup):
    _create(setup, erp_ref_id="ERP-A")
    _create(setup, erp_ref_id="ERP-B")
    r = setup.get("/api/notice/changes?since=2000-01-01T00:00:00Z")
    assert r.status_code == 200
    d = r.get_json()
    assert d["count"] == 2 and d["truncated"] is False
    refs = {c["erp_ref_id"] for c in d["changes"]}
    assert refs == {"ERP-A", "ERP-B"}
    # 행 스키마: ERP 대사에 필요한 3컬럼 + 상태·타임스탬프 포함, body 미포함(대량 응답 방지)
    row = d["changes"][0]
    for k in ("id", "erp_ref_id", "revision_no", "content_hash", "status", "updated_at"):
        assert k in row
    assert "body" not in row


def test_changes_future_since_empty(setup):
    _create(setup)
    r = setup.get("/api/notice/changes?since=2999-01-01T00:00:00Z")
    assert r.status_code == 200
    assert r.get_json()["count"] == 0


def test_changes_since_format_leniency(setup):
    """저장 포맷(+00:00Z)과 다른 클라이언트 포맷(Z만·오프셋만·naive)도 비교된다."""
    _create(setup)
    for ts in ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00"):
        r = setup.get(f"/api/notice/changes?since={ts.replace('+', '%2B')}")
        assert r.status_code == 200, ts
        assert r.get_json()["count"] == 1, ts


def test_changes_revision_and_revoke_visible(setup):
    """개정(PUT)·폐지 행이 변경분에 실린다 — updated_at 갱신 경로 3종 커버."""
    created = _create(setup, erp_ref_id="ERP-R")
    r = setup.put(f"/api/notice/{created['id']}", json={
        "erp_ref_id": "ERP-R", "revision_no": 2, "notice_type": "REVIEW_CRITERIA",
        "title": "t2", "body": "제1조(목적) 개정본.", "effective_from": "2026-08-01"})
    assert r.status_code in (200, 202), r.get_json()
    d = setup.get("/api/notice/changes?since=2000-01-01T00:00:00Z").get_json()
    by_rev = {(c["erp_ref_id"], c["revision_no"]): c["status"] for c in d["changes"]}
    assert by_rev[("ERP-R", 1)] == "superseded"
    assert by_rev[("ERP-R", 2)] == "active"


def test_changes_limit_truncated(setup):
    for i in range(3):
        _create(setup, erp_ref_id=f"ERP-{i}")
    d = setup.get("/api/notice/changes?since=2000-01-01T00:00:00Z&limit=2").get_json()
    assert d["count"] == 2 and d["truncated"] is True and len(d["changes"]) == 2


# ── POST /api/notice/sync/verify ───────────────────────────────────────────

def test_verify_bad_body(setup):
    assert setup.post("/api/notice/sync/verify", json={}).status_code == 400
    assert setup.post("/api/notice/sync/verify",
                      json={"snapshot": [{"revision_no": 1}]}).status_code == 400
    assert setup.post("/api/notice/sync/verify",
                      json={"snapshot": [{"erp_ref_id": "E", "revision_no": "x",
                                          "content_hash": "sha256:0"}]}).status_code == 400
    assert setup.post("/api/notice/sync/verify",
                      json={"snapshot": [{"erp_ref_id": "E", "revision_no": 1}]}).status_code == 400


def test_verify_three_reasons(setup):
    """설계 테스트 계획 #6 — hash_diff/missing/extra 3종 검출."""
    body = "제1조(목적) 본문.\n제2조 시행."
    _create(setup, erp_ref_id="ERP-OK", body=body)      # matched 대상
    _create(setup, erp_ref_id="ERP-DIFF", body=body)    # hash_diff 대상
    _create(setup, erp_ref_id="ERP-EXTRA", body=body)   # 스냅샷에 없음 → extra
    snap = [
        {"erp_ref_id": "ERP-OK", "revision_no": 1, "content_hash": _hash_of(body)},
        {"erp_ref_id": "ERP-DIFF", "revision_no": 1, "content_hash": "sha256:" + "0" * 64},
        {"erp_ref_id": "ERP-MISS", "revision_no": 1, "content_hash": _hash_of(body)},
    ]
    r = setup.post("/api/notice/sync/verify", json={"snapshot": snap})
    assert r.status_code == 200
    d = r.get_json()
    assert d["total"] == 3 and d["matched"] == 1
    reasons = {(m["erp_ref_id"], m["reason"]) for m in d["mismatched"]}
    assert reasons == {("ERP-DIFF", "hash_diff"), ("ERP-MISS", "missing"),
                       ("ERP-EXTRA", "extra")}


def test_verify_hash_prefix_leniency(setup):
    """content_hash의 sha256: 프리픽스 유무 무관 — verify_hash와 같은 규칙."""
    body = "제1조(목적) 본문.\n제2조 시행."
    _create(setup, erp_ref_id="ERP-P", body=body)
    bare = _hash_of(body).removeprefix("sha256:")
    d = setup.post("/api/notice/sync/verify", json={"snapshot": [
        {"erp_ref_id": "ERP-P", "revision_no": 1, "content_hash": bare}]}).get_json()
    assert d["matched"] == 1 and d["mismatched"] == []


def test_verify_superseded_not_extra(setup):
    """ERP가 최신 revision만 실어도 superseded 구행이 extra 위양성을 내지 않는다."""
    body2 = "제1조(목적) 개정본."
    created = _create(setup, erp_ref_id="ERP-S")
    r = setup.put(f"/api/notice/{created['id']}", json={
        "erp_ref_id": "ERP-S", "revision_no": 2, "notice_type": "REVIEW_CRITERIA",
        "title": "t2", "body": body2, "effective_from": "2026-08-01"})
    assert r.status_code in (200, 202)
    d = setup.post("/api/notice/sync/verify", json={"snapshot": [
        {"erp_ref_id": "ERP-S", "revision_no": 2, "content_hash": _hash_of(body2)}]}).get_json()
    assert d["matched"] == 1 and d["mismatched"] == []


def test_verify_empty_snapshot_reports_all_active_extra(setup):
    _create(setup, erp_ref_id="ERP-X")
    d = setup.post("/api/notice/sync/verify", json={"snapshot": []}).get_json()
    assert d["total"] == 0 and d["matched"] == 0
    assert d["mismatched"] == [{"erp_ref_id": "ERP-X", "revision_no": 1, "reason": "extra"}]


# ── (2026-09-28): 설계서 키(snapshot/mismatched)와 ERP 구현 키(items/mismatches) 양쪽 수용 ──
# ERP 호출부: services/core/src/erp/integrations/ai_publisher.py
#   post(".../sync/verify", json={"items": snapshot}) → resp.json().get("mismatches", [])

def test_verify_accepts_items_and_returns_mismatches(setup):
    body = "제1조(목적) 본문.\n제2조 시행."
    _create(setup, erp_ref_id="ERP-OK", body=body)
    _create(setup, erp_ref_id="ERP-EXTRA", body=body)
    items = [
        {"erp_ref_id": "ERP-OK", "revision_no": 1, "content_hash": _hash_of(body)},
        {"erp_ref_id": "ERP-MISS", "revision_no": 1, "content_hash": _hash_of(body)},
    ]
    r = setup.post("/api/notice/sync/verify", json={"items": items})
    assert r.status_code == 200
    d = r.get_json()
    assert d["total"] == 2 and d["matched"] == 1
    # ERP가 읽는 방식 그대로
    got = {(m["erp_ref_id"], m["reason"]) for m in d.get("mismatches", [])}
    assert got == {("ERP-MISS", "missing"), ("ERP-EXTRA", "extra")}
    assert d["mismatches"] == d["mismatched"]


def test_verify_snapshot_path_also_has_mismatches_alias(setup):
    _create(setup, erp_ref_id="ERP-X")
    d = setup.post("/api/notice/sync/verify", json={"snapshot": []}).get_json()
    assert d["mismatched"] == d["mismatches"] != []


def test_verify_both_keys_rejected(setup):
    r = setup.post("/api/notice/sync/verify", json={"snapshot": [], "items": []})
    assert r.status_code == 400 and "not both" in r.get_json()["error"]


def test_verify_items_errors_name_the_key(setup):
    r = setup.post("/api/notice/sync/verify", json={"items": [{"revision_no": 1}]})
    assert r.status_code == 400 and r.get_json()["error"].startswith("items[0]")
