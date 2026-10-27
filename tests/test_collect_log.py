"""core.collect_log 테스트 (인메모리 — 실 DB/GPU 불요).

핵심: ①이력 적재·필터 조회 ②retention purge ③보고서 집계(소스별 시도/적재/오류·
요약문) ④report_due 게이트(워커 churn 안전) ⑤저장·최신 조회.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import collect_log as cl  # noqa: E402


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _patch_db(monkeypatch, c):
    import core.notice_db as ndb
    monkeypatch.setattr(ndb, "get_notice_db", lambda *a, **k: c)


def test_log_event_and_query_filters(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    cl.log_event("약제급여목록", "ok", count=21959, edition="2026-07-01", detail="게시글")
    cl.log_event("약제급여목록", "skip", count=21959)
    cl.log_event("치료재료", "denied", error="403 활용신청 필요")
    cl.log_event("병용금기", "ok", count=1816, kind="datago-api")
    assert len(cl.query_logs()) == 4
    assert len(cl.query_logs(source="약제급여목록")) == 2
    assert len(cl.query_logs(status="ok")) == 2
    denied = cl.query_logs(status="denied")
    assert denied[0]["source"] == "치료재료" and "403" in denied[0]["error"]
    assert denied[0]["kind"] == "datago-api"                 # _API_SOURCES 자동 분류
    assert cl.query_logs(source="약제급여목록")[0]["status"] == "skip"  # 최신순


def test_log_event_never_raises(monkeypatch):
    import core.notice_db as ndb

    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(ndb, "get_notice_db", _boom)
    cl.log_event("약제급여목록", "ok")                        # 예외 전파 없어야 함


def test_purge_retention(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    cl.log_event("약제급여목록", "ok", count=1)
    cl._db().execute("UPDATE collect_log SET ts='2020-01-01T00:00:00+00:00Z'")
    cl.log_event("약제급여목록", "ok", count=2)               # 최근 1건
    c.commit()
    assert cl.purge_logs(365) == 1                            # 구로그만 삭제
    assert len(cl.query_logs()) == 1
    assert cl.purge_logs(0) == 0                              # <=0 무동작


def test_build_report_aggregates(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    cl.log_event("약제급여목록", "ok", count=21959, edition="2026-07-01")
    cl.log_event("약제급여목록", "skip", count=21959)
    cl.log_event("저가약대체조제", "ok", count=10980, detail="'26. 6월 기준")
    cl.log_event("치료재료", "denied", error="403")
    cl.log_event("e약은요", "error", error="HTTP 502")
    rep = cl.build_report(period_days=7)
    by = {s["source"]: s for s in rep["sources"]}
    assert by["약제급여목록"]["attempts"] == 2 and by["약제급여목록"]["ok"] == 1
    assert by["약제급여목록"]["loaded"][0]["edition"] == "2026-07-01"
    assert by["치료재료"]["denied"] == 1 and by["e약은요"]["error"] == 1
    assert rep["total_attempts"] == 5
    assert "신규 적재" in rep["summary"] and "점검 필요" in rep["summary"]
    assert "치료재료" in rep["summary"]                       # 문제 소스 표기
    assert "data_stats" in rep


def test_generate_and_latest_report(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    monkeypatch.setattr(cl, "_data_stats", lambda: {"fee_codes": {"total": 1}})
    cl.log_event("약제급여목록", "ok", count=10, edition="2026-07-01")
    rep = cl.generate_report(period_days=7, retention_days=365)
    assert rep["total_attempts"] == 1
    latest = cl.latest_report()
    assert latest["summary"] == rep["summary"]
    assert latest["report"]["sources"][0]["source"] == "약제급여목록"
    assert len(cl.list_reports()) == 1


def test_report_due_gates(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    monkeypatch.setattr(cl, "_data_stats", lambda: {})
    assert cl.report_due(7 * 86400) is True                   # 보고서 없음 → 필요
    cl.generate_report(period_days=7, retention_days=365)
    assert cl.report_due(7 * 86400) is False                  # 방금 생성 → skip
    cl._db().execute("UPDATE collect_reports SET ts='2020-01-01T00:00:00+00:00'")
    c.commit()
    assert cl.report_due(7 * 86400) is True                   # 오래됨 → 필요
    assert cl.generate_if_due(7) is not None                  # due → 생성
    assert cl.generate_if_due(7) is None                      # 직후 → None
