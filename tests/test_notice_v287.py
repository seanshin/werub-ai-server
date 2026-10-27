"""v2.87.0 — 공시 검색/평가/코드매핑/리졸버/index-status 라우트 회귀테스트.

search/diagnose의 LLM·임베딩 경로는 제외(외부 의존) — notice.db 단독 로직·입력검증만.
백그라운드 색인(process_immediate_async)은 no-op 패치로 격리(프로덕션 notice.db 미접촉).
flask/requests 없으면 skip. 실행(GPU): venv/bin/python -m pytest tests/test_notice_v287.py -q
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
         "body": "제1조(목적) 본문.\n제2조 시행.", "effective_from": "2026-07-01",
         "client_request_id": "req-1"}
    p.update(over)
    return client.post("/api/notice", json=p)


# ── 순수 헬퍼 ──────────────────────────────────────────────
def test_effective_on_ok():
    from routes.notice import _effective_on_ok
    assert _effective_on_ok({"effective_from": "2026-01-01"}, "2026-06-01") is True
    assert _effective_on_ok({"effective_from": "2026-07-01"}, "2026-06-25") is False   # 시행 전
    assert _effective_on_ok({"effective_from": "2026-01-01", "effective_to": "2026-05-31"},
                            "2026-06-25") is False                                       # 만료
    assert _effective_on_ok({"effective_from": "2026-01-01", "effective_to": "2026-12-31"},
                            "2026-06-25") is True


def test_risk_level_bands():
    from routes.notice import _risk_level
    assert _risk_level(0.9) == "HIGH"
    assert _risk_level(0.70) == "HIGH"
    assert _risk_level(0.699) == "MEDIUM"
    assert _risk_level(0.40) == "MEDIUM"
    assert _risk_level(0.399) == "LOW"
    assert _risk_level(0.0) == "LOW"


def test_risk_thresholds_config_override(monkeypatch):
    # config로 임계 조정(무재배포 캘리브레이션) — ERP precision≥0.8 목표용
    import routes.notice as nm
    monkeypatch.setattr(nm, "load_config",
                        lambda: {"notice_diagnose_thresholds": {"high": 0.85, "medium": 0.50}})
    assert nm._risk_thresholds() == (0.85, 0.50)
    assert nm._risk_level(0.86) == "HIGH"
    assert nm._risk_level(0.84) == "MEDIUM"
    assert nm._risk_level(0.49) == "LOW"


def test_risk_thresholds_invalid_falls_back(monkeypatch):
    import routes.notice as nm
    # high<=medium(역전) → 기본값 폴백
    monkeypatch.setattr(nm, "load_config",
                        lambda: {"notice_diagnose_thresholds": {"high": 0.3, "medium": 0.5}})
    assert nm._risk_thresholds() == (0.70, 0.40)
    # 파싱 불가도 폴백
    monkeypatch.setattr(nm, "load_config",
                        lambda: {"notice_diagnose_thresholds": {"high": "x"}})
    assert nm._risk_thresholds() == (0.70, 0.40)


def test_risk_thresholds_default(monkeypatch):
    import routes.notice as nm
    monkeypatch.setattr(nm, "load_config", lambda: {})
    assert nm._risk_thresholds() == (0.70, 0.40)


# ── 리졸버: revoke/delete를 erp_ref_id로 (과거 404 수정) ──────
def test_revoke_by_erp_ref_id(setup):
    assert _create(setup, erp_ref_id="ERP-REV").status_code == 202
    r = setup.post("/api/notice/ERP-REV/revoke", json={})
    assert r.status_code == 200 and r.get_json()["status"] == "revoked"


def test_delete_by_erp_ref_id(setup):
    _create(setup, erp_ref_id="ERP-DEL")
    r = setup.delete("/api/notice/ERP-DEL")
    assert r.status_code == 200 and r.get_json()["deleted"] is True


def test_revoke_unknown_404(setup):
    assert setup.post("/api/notice/NOPE/revoke", json={}).status_code == 404


def test_revoke_by_ntc_id_still_works(setup):
    nid = _create(setup, erp_ref_id="ERP-NTC").get_json()["id"]
    assert setup.post(f"/api/notice/{nid}/revoke", json={}).status_code == 200


# ── index-status: 컬렉션 라우트(405 해소) + 경로(erp_ref_id) ──
def test_index_status_collection_by_client_request_id(setup):
    _create(setup, client_request_id="REQ-X")
    r = setup.get("/api/notice/index-status?client_request_id=REQ-X")
    assert r.status_code == 200 and len(r.get_json()["jobs"]) >= 1


def test_index_status_collection_by_erp_ref_id(setup):
    _create(setup, erp_ref_id="ERP-IS")
    r = setup.get("/api/notice/index-status?erp_ref_id=ERP-IS")
    assert r.status_code == 200 and r.get_json()["latest"] == "pending"


def test_index_status_collection_requires_param(setup):
    assert setup.get("/api/notice/index-status").status_code == 400


def test_index_status_path_by_ntc_id(setup):
    nid = _create(setup, erp_ref_id="ERP-PATH").get_json()["id"]
    assert setup.get(f"/api/notice/{nid}/index-status").status_code == 200


# ── fee-codes 적재/조회 ─────────────────────────────────────
def test_fee_codes_import_rows_and_resolve(setup):
    r = setup.post("/api/notice/fee-codes/import",
                   json={"rows": [{"code": "AA157", "category": "TARIFF", "name": "재진"}]})
    assert r.status_code == 200 and r.get_json()["imported"] == 1
    assert setup.get("/api/notice/fee-codes/AA157").get_json()["notice_type"] == \
        ["REVIEW_CRITERIA", "TARIFF"]


def test_fee_codes_import_csv_korean_headers(setup):
    r = setup.post("/api/notice/fee-codes/import",
                   json={"csv": "코드,분류,명칭\nM6020,MATERIAL,인공관절",
                         "columns": {"code": "코드", "category": "분류", "name": "명칭"}})
    assert r.status_code == 200 and r.get_json()["imported"] == 1
    assert setup.get("/api/notice/fee-codes/M6020").get_json()["notice_type"] == \
        ["DRUG_MATERIAL", "REVIEW_CRITERIA"]


def test_fee_codes_import_requires_data(setup):
    assert setup.post("/api/notice/fee-codes/import", json={}).status_code == 400


def test_fee_code_unknown_resolves_false(setup):
    g = setup.get("/api/notice/fee-codes/UNKNOWN").get_json()
    assert g["notice_type"] == [] and g["resolved"] is False


# ── 검색/평가 입력검증(외부 의존 진입 전) ────────────────────
def test_search_requires_query(setup):
    assert setup.post("/api/notice/search", json={}).status_code == 400


def test_search_invalid_notice_type(setup):
    assert setup.post("/api/notice/search",
                      json={"query": "x", "notice_type": ["BOGUS"]}).status_code == 400


def test_diagnose_requires_claim(setup):
    assert setup.post("/api/notice/diagnose", json={}).status_code == 400
    assert setup.post("/api/notice/diagnose", json={"claim_line": {}}).status_code == 400


def test_process_jobs_invalid_modes(setup):
    assert setup.post("/api/notice/process-jobs",
                      json={"modes": ["bogus"]}).status_code == 400
