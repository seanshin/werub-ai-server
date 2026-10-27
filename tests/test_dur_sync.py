"""core.dur_sync 안전 갱신 테스트 (fetch 모킹, 인메모리 — 실 API/GPU 불요).

핵심: fetch 성공+건수 sane → 교체 / 건수 이상(부분응답·오류) → 교체 skip(기존 정본 보존).
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from core import dur as dur_mod  # noqa: E402
from core import dur_sync  # noqa: E402
from core import collect_log  # noqa: E402


@pytest.fixture(autouse=True)
def _no_collect_log(monkeypatch):
    """_record→collect_log 훅이 실 notice.db에 쓰지 않도록 차단(테스트 격리)."""
    monkeypatch.setattr(collect_log, "log_event", lambda *a, **k: None)


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dur_mod._DDL)
    c.commit()
    return c


def _patch_db(monkeypatch, c):
    monkeypatch.setattr(dur_mod, "get_dur_db", lambda *a, **k: c)


def _interaction(seq):
    return {"INGR_CODE": f"D{seq:06d}", "INGR_NAME": f"성분{seq}", "INGR_ENG_NAME": "X",
            "MIXTURE_INGR_CODE": f"D9{seq:05d}", "MIXTURE_INGR_KOR_NAME": "상대",
            "PROHBT_CONTENT": "금기", "DUR_SEQ": str(seq)}


def test_refresh_ok_replaces(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    dur_mod.import_interactions_api([_interaction(1)], conn=c)   # 기존 1건
    monkeypatch.setattr(dur_sync, "fetch_all",
                        lambda *a, **k: [_interaction(i) for i in range(5)])
    r = dur_sync.refresh_one("병용금기", "B", "op", "interactions", min_count=3, key="k")
    assert r["status"] == "ok" and r["count"] == 5
    assert dur_mod.stats(conn=c)["interactions"] == 5           # 교체됨


def test_refresh_skip_on_low_count_keeps_existing(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    dur_mod.import_interactions_api([_interaction(i) for i in range(4)], conn=c)  # 기존 4건
    # API가 1건만 반환(부분응답/오류) → min 800 미달 → 교체 skip, 기존 보존
    monkeypatch.setattr(dur_sync, "fetch_all", lambda *a, **k: [_interaction(99)])
    r = dur_sync.refresh_one("병용금기", "B", "op", "interactions", min_count=800, key="k")
    assert r["status"] == "skip" and r["fetched"] == 1
    assert dur_mod.stats(conn=c)["interactions"] == 4           # 기존 보존(빈 테이블 방지)


def test_refresh_error_on_fetch_fail_keeps_existing(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    dur_mod.import_interactions_api([_interaction(i) for i in range(3)], conn=c)

    def _boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(dur_sync, "fetch_all", _boom)
    r = dur_sync.refresh_one("병용금기", "B", "op", "interactions", min_count=1, key="k")
    assert r["status"] == "error"
    assert dur_mod.stats(conn=c)["interactions"] == 3           # 기존 보존


def test_sync_status_records_meta(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    monkeypatch.setattr(dur_sync, "fetch_all", lambda *a, **k: [_interaction(i) for i in range(5)])
    dur_sync.refresh_one("병용금기", "B", "op", "interactions", min_count=1, key="k")
    st = dur_sync.sync_status()
    src = {s["source"]: s for s in st["sources"]}
    assert src["병용금기"]["status"] == "ok" and src["병용금기"]["count"] == 5
    assert src["병용금기"]["last_sync_at"]


def test_refresh_all_no_key(monkeypatch):
    monkeypatch.setattr(dur_sync, "get_service_key", lambda *a, **k: None)
    r = dur_sync.refresh_all()
    assert r["ok"] is False and "key" in r["error"]


# ── 워커 재생성 과다호출 방지: initial 동기화 staleness 게이트(_sync_due) ──

def test_sync_due_true_when_never_synced(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    # dur_sync_meta 비어 있음(한 번도 동기화 안 됨) → due
    assert dur_sync._sync_due(7 * 86400) is True


def test_sync_due_false_when_recently_synced(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    dur_sync._record("병용금기", "ok", 1816, 30.0)  # 방금 성공 동기화 기록
    # 워커가 ~22분마다 재생성돼 스케줄러가 다시 떠도, 7일 이내면 skip
    assert dur_sync._sync_due(7 * 86400) is False


def test_sync_due_true_when_stale(monkeypatch):
    import datetime as _dt
    c = _conn(); _patch_db(monkeypatch, c)
    dur_sync._meta_db()  # dur_sync_meta 테이블 생성 보장
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=8)).isoformat() + "Z"
    c.execute(
        "INSERT OR REPLACE INTO dur_sync_meta (source,status,count,last_sync_at,duration_s,last_error)"
        " VALUES (?,?,?,?,?,?)", ("병용금기", "ok", 1816, old, 30.0, ""))
    c.commit()
    assert dur_sync._sync_due(7 * 86400) is True


def test_sync_due_ignores_non_ok(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    # error 상태만 있으면 성공 기록 없음 → due(재시도 필요)
    dur_sync._record("병용금기", "error", 0, 1.0, "boom")
    assert dur_sync._sync_due(7 * 86400) is True
