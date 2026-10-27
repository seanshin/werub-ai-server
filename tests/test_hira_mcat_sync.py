"""core.hira_mcat_sync 테스트 (API 모킹, 인메모리 — 실 API/서비스키 불요).

핵심: ①XML→행 변환(재료대코드·패킹) ②안전 갱신(건수 게이트·MATERIAL만 교체)
③denied(활용신청 전) 휴면 — 기존 데이터 불변·메타 기록.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import hira_mcat_sync as hms  # noqa: E402
from core import hira_drug_sync as hds  # noqa: E402


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _patch_db(monkeypatch, c):
    import core.notice_db as ndb
    monkeypatch.setattr(ndb, "get_notice_db", lambda *a, **k: c)


def _patch_key(monkeypatch, key="k"):
    import core.dur_sync as ds
    monkeypatch.setattr(ds, "get_service_key", lambda *a, **k: key)


def _items(n):
    return [{"mcatCd": f"BC{i:04d}ON", "itmNm": f"재료{i}", "nomNm": "전규격",
             "unit": "1EA", "mnfEntpNm": "제조사", "mxUnprc": "1000",
             "payTpNm": "급여"} for i in range(n)]


def test_to_fee_rows_packing():
    rows = hms.to_fee_rows([{"mcatCd": "BC4200ON", "itmNm": "고정용 PIN", "nomNm": "전규격",
                             "unit": "1EA", "impEntpNm": "수입사", "mxUnprc": "129",
                             "payTpNm": "선별급여"}])
    assert rows[0]["code"] == "BC4200ON" and rows[0]["category"] == "MATERIAL"
    assert "고정용 PIN" in rows[0]["name"] and "상한 129원" in rows[0]["name"]
    assert "선별급여" in rows[0]["name"] and "수입사" in rows[0]["name"]
    assert hms.to_fee_rows([{"itmNm": "코드없음"}]) == []      # mcatCd 없으면 제외


def test_sync_ok_replaces_material_keeps_others(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c); _patch_key(monkeypatch)
    monkeypatch.setattr(hms, "fetch_all", lambda key: _items(20))
    monkeypatch.setattr(hms, "MIN_COUNT", 5)
    from core.fee_codes import import_fee_codes
    import_fee_codes([{"code": "OLDMAT", "category": "MATERIAL", "name": "구재료"},
                      {"code": "645302132", "category": "DRUG", "name": "약"}], conn=c)
    r = hms.sync()
    assert r["ok"] and r["count"] == 20
    mat = {x[0] for x in c.execute("SELECT code FROM fee_codes WHERE category='MATERIAL'")}
    assert "OLDMAT" not in mat and len(mat) == 20             # MATERIAL 전량 교체
    assert c.execute("SELECT COUNT(*) FROM fee_codes WHERE category='DRUG'").fetchone()[0] == 1
    assert hds._get_meta(hms.SOURCE)["status"] == "ok"


def test_sync_skip_low_count_keeps_existing(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c); _patch_key(monkeypatch)
    monkeypatch.setattr(hms, "fetch_all", lambda key: _items(3))  # MIN 10000 미달
    from core.fee_codes import import_fee_codes
    import_fee_codes([{"code": "OLDMAT", "category": "MATERIAL", "name": "구재료"}], conn=c)
    r = hms.sync()
    assert not r["ok"] and r["status"] == "skip"
    assert c.execute("SELECT COUNT(*) FROM fee_codes WHERE category='MATERIAL'").fetchone()[0] == 1


def test_sync_denied_dormant(monkeypatch):
    """활용신청 전 403 → denied 기록·데이터 불변·last_check 갱신(주기 재시도만)."""
    c = _conn(); _patch_db(monkeypatch, c); _patch_key(monkeypatch)

    def _denied(key):
        raise PermissionError("data.go.kr 403 — 활용신청 필요")
    monkeypatch.setattr(hms, "fetch_all", _denied)
    from core.fee_codes import import_fee_codes
    import_fee_codes([{"code": "OLDMAT", "category": "MATERIAL", "name": "구재료"}], conn=c)
    r = hms.sync()
    assert not r["ok"] and r["status"] == "denied"
    assert c.execute("SELECT COUNT(*) FROM fee_codes WHERE category='MATERIAL'").fetchone()[0] == 1
    meta = hds._get_meta(hms.SOURCE)
    assert meta["status"] == "denied" and meta["last_check_at"]   # 주기 게이트 동작
    assert hds._sync_due(7 * 86400, hms.SOURCE) is False          # churn마다 403 안 때림


def test_sync_no_key(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c); _patch_key(monkeypatch, key=None)
    r = hms.sync()
    assert not r["ok"] and "key" in r["error"]


def test_fetch_page_parses_xml(monkeypatch):
    import pytest
    requests = pytest.importorskip("requests")
    xml = b"""<response><header><resultCode>00</resultCode></header>
    <body><totalCount>2</totalCount><items>
    <item><mcatCd>BC1</mcatCd><itmNm>A</itmNm><mxUnprc>10</mxUnprc></item>
    <item><mcatCd>BC2</mcatCd><itmNm>B</itmNm><mxUnprc>20</mxUnprc></item>
    </items></body></response>"""

    class _Resp:
        status_code = 200
        content = xml

        def raise_for_status(self):
            pass
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    items, total = hms._fetch_page("k", 1)
    assert total == 2 and items[0]["mcatCd"] == "BC1" and items[1]["itmNm"] == "B"


def test_fetch_page_retry_subdivides_on_410(monkeypatch):
    """1000행에서만 410인 페이지 → 100행 × 10 세분화로 누락 없이 수집(실측 클래스)."""
    import pytest
    requests = pytest.importorskip("requests")
    calls = []

    def _fake(key, page, rows=hms.PAGE_ROWS):
        calls.append((page, rows))
        if rows == hms.PAGE_ROWS:
            resp = type("R", (), {"status_code": 410})()
            raise requests.HTTPError("410 Gone", response=resp)
        return ([{"mcatCd": f"C{page}"}], 91211)

    monkeypatch.setattr(hms, "_fetch_page", _fake)
    monkeypatch.setattr(hms.time, "sleep", lambda *a: None)
    items = hms._fetch_page_retry("k", 60)
    assert len(items) == 10                                   # 세분화 10개 전부 수집
    sub = [p for p, r in calls if r == 100]
    assert sub == list(range(591, 601))                        # 오프셋 59000~59999 정확 매핑


def test_fetch_page_retry_raises_on_non_retryable(monkeypatch):
    """404 등 비재시도 상태는 세분화하지 않고 즉시 전파."""
    import pytest
    requests = pytest.importorskip("requests")

    def _fake(key, page, rows=hms.PAGE_ROWS):
        raise requests.HTTPError("404", response=type("R", (), {"status_code": 404})())
    monkeypatch.setattr(hms, "_fetch_page", _fake)
    with pytest.raises(requests.HTTPError):
        hms._fetch_page_retry("k", 2)


def test_fetch_page_denied_on_403(monkeypatch):
    import pytest
    requests = pytest.importorskip("requests")

    class _Resp:
        status_code = 403
        content = b""

        def raise_for_status(self):
            pass
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(PermissionError):
        hms._fetch_page("k", 1)


# ── 상대 감소 가드 (2026-08-06) ────────────────────────────────────────────
# 🔴 08-03 동기화가 91,211 → 59,000(-35%)을 status=ok로 전량 교체했다. 절대 하한
# MIN_COUNT=10,000을 넉넉히 넘어 통과했고 정본 32,211행이 조용히 사라졌다.
# 절대 하한만으로는 **부분 응답을 잡을 수 없다**는 것이 실증된 것이다.

def test_partial_response_is_rejected(monkeypatch):
    """직전 91,211에서 59,000으로 떨어지면 **교체하지 않는다**(실사고 재현).

    가드 자체의 순수 로직은 `test_sync_guard.py`가 담당한다 — 여기서는 **배선**을 본다.
    """
    monkeypatch.setattr(hms, "_current_material_count", lambda: 91211)
    rows = [{"code": "X%05d" % i, "category": "MATERIAL", "name": "n"} for i in range(59000)]
    monkeypatch.setattr(hms, "to_fee_rows", lambda items: rows)
    monkeypatch.setattr(hms, "fetch_all", lambda key: [{}])
    monkeypatch.setattr("core.dur_sync.get_service_key", lambda: "k")
    recorded = {}
    monkeypatch.setattr("core.hira_drug_sync._record",
                        lambda *a, **k: recorded.update({"args": a}))
    out = hms.sync()
    assert out["ok"] is False and out["status"] == "skip"
    assert out["current"] == 91211
    assert recorded["args"][1] == "skip"


def test_normal_variation_passes(monkeypatch):
    """정상 변동(-5%)은 통과해야 한다 — 가드가 갱신 자체를 막으면 안 된다."""
    monkeypatch.setattr(hms, "_current_material_count", lambda: 91211)
    rows = [{"code": "X%05d" % i, "category": "MATERIAL", "name": "n"} for i in range(86000)]
    monkeypatch.setattr(hms, "to_fee_rows", lambda items: rows)
    monkeypatch.setattr(hms, "fetch_all", lambda key: [{}])
    monkeypatch.setattr("core.dur_sync.get_service_key", lambda: "k")
    monkeypatch.setattr("core.hira_drug_sync._record", lambda *a, **k: None)
    imported = {}

    def fake_import(rows_, conn=None):
        imported["n"] = len(rows_)
        return len(rows_)

    monkeypatch.setattr("core.fee_codes.import_fee_codes", fake_import)
    monkeypatch.setattr("core.fee_codes._ensure", lambda conn: None)

    class _C:
        def execute(self, *a):
            return self

        def commit(self):
            pass
    monkeypatch.setattr("core.notice_db.get_notice_db", lambda: _C())
    monkeypatch.setattr("core.notice_db.checkpoint_truncate", lambda conn: None)
    out = hms.sync()
    assert out["ok"] is True and imported["n"] == 86000


def test_current_count_failure_fails_open(monkeypatch):
    """현재 건수 조회가 실패하면 가드 미적용 — 조회 때문에 갱신이 막히면 안 된다."""
    def boom():
        raise RuntimeError("db locked")

    monkeypatch.setattr(hms, "_current_material_count", boom)
    rows = [{"code": "X%05d" % i, "category": "MATERIAL", "name": "n"} for i in range(20000)]
    monkeypatch.setattr(hms, "to_fee_rows", lambda items: rows)
    monkeypatch.setattr(hms, "fetch_all", lambda key: [{}])
    monkeypatch.setattr("core.dur_sync.get_service_key", lambda: "k")
    monkeypatch.setattr("core.hira_drug_sync._record", lambda *a, **k: None)
    monkeypatch.setattr("core.fee_codes.import_fee_codes", lambda r, conn=None: len(r))
    monkeypatch.setattr("core.fee_codes._ensure", lambda conn: None)

    class _C:
        def execute(self, *a):
            return self

        def commit(self):
            pass
    monkeypatch.setattr("core.notice_db.get_notice_db", lambda: _C())
    monkeypatch.setattr("core.notice_db.checkpoint_truncate", lambda conn: None)
    assert hms.sync()["ok"] is True


def test_empty_page_does_not_stop_collection(monkeypatch):
    calls = []

    def fake_page(key, page, num_rows=1000):
        calls.append(page)
        return ([{"mcatCd": "C%d" % (page * 1000 + i)} for i in range(3)], 5000)

    def fake_retry(key, page, rows=1000, attempts=3):
        calls.append(page)
        if page == 3:                      # 중간 구멍(200 OK + 빈 배열)
            return []
        return [{"mcatCd": "C%d" % (page * 1000 + i)} for i in range(3)]

    monkeypatch.setattr(hms, "_fetch_page", fake_page)
    monkeypatch.setattr(hms, "_fetch_page_retry", fake_retry)
    monkeypatch.setattr(hms, "_subdivide", lambda key, page, rows=1000: [])
    monkeypatch.setattr(hms.time, "sleep", lambda s: None)
    out = hms.fetch_all("k")
    # 5000/1000 = 5페이지 전부 방문했는가(구멍에서 멈추지 않음)
    assert max(calls) == 5, calls
    # 구멍은 재시도된다 — page 3이 두 번 이상 호출
    assert calls.count(3) >= 2, calls
    assert len(out) == 3 + 3 * 3   # page1(3건) + page2·4·5(각 3건) · page3은 비어 있음


def test_empty_page_recovered_on_retry(monkeypatch):
    state = {"n": 0}

    def fake_retry(key, page, rows=1000, attempts=3):
        if page == 3:
            state["n"] += 1
            if state["n"] == 1:            # 첫 시도만 비고 재시도에서 회복
                return []
        return [{"mcatCd": "C%d" % (page * 1000 + i)} for i in range(3)]

    monkeypatch.setattr(hms, "_fetch_page",
                        lambda key, page, num_rows=1000: ([{"mcatCd": "C1"}], 4000))
    monkeypatch.setattr(hms, "_fetch_page_retry", fake_retry)
    monkeypatch.setattr(hms.time, "sleep", lambda s: None)
    out = hms.fetch_all("k")
    assert len(out) == 1 + 3 * 3           # page1(1건) + page2·3·4 각 3건


def test_connection_error_is_retried(monkeypatch):
    """연결 리셋 한 번이 전량 수집을 무효화하면 안 된다(2026-08-06 실측)."""
    import pytest
    requests = pytest.importorskip("requests")
    state = {"n": 0}

    def flaky(key, page, rows=1000):
        state["n"] += 1
        if state["n"] == 1:
            raise requests.ConnectionError("Connection reset by peer")
        return ([{"mcatCd": "C1"}], 1000)

    monkeypatch.setattr(hms, "_fetch_page", flaky)
    monkeypatch.setattr(hms.time, "sleep", lambda s: None)
    assert hms._fetch_page_retry("k", 2) == [{"mcatCd": "C1"}]
    assert state["n"] == 2


def test_connection_error_exhausted_raises(monkeypatch):
    """계속 실패하면 예외 — 조용히 빈 결과로 넘기지 않는다(부분 교체 방지)."""
    import pytest
    requests = pytest.importorskip("requests")

    def always(key, page, rows=1000):
        raise requests.ConnectionError("reset")

    monkeypatch.setattr(hms, "_fetch_page", always)
    monkeypatch.setattr(hms.time, "sleep", lambda s: None)
    try:
        hms._fetch_page_retry("k", 2, attempts=2)
    except requests.ConnectionError:
        return
    raise AssertionError("예외가 전파되어야 한다")


def test_persistently_empty_page_falls_back_to_subdivision(monkeypatch):
    """1000행에서 계속 비는 페이지는 세분화로 회수한다(실측: page 60 → 1,000행 전부)."""
    seen = {"sub": 0}

    def fake_retry(key, page, rows=1000, attempts=3):
        return [] if page == 3 else [{"mcatCd": "C%d" % page}]

    def fake_sub(key, page, rows=1000):
        seen["sub"] += 1
        assert page == 3
        return [{"mcatCd": "S%d" % i} for i in range(10)]

    monkeypatch.setattr(hms, "_fetch_page",
                        lambda key, page, num_rows=1000: ([{"mcatCd": "C1"}], 4000))
    monkeypatch.setattr(hms, "_fetch_page_retry", fake_retry)
    monkeypatch.setattr(hms, "_subdivide", fake_sub)
    monkeypatch.setattr(hms.time, "sleep", lambda s: None)
    out = hms.fetch_all("k")
    assert seen["sub"] == 1                     # 빈 페이지에만 세분화
    assert len(out) == 1 + 2 + 10               # page1 + page2·4 + 세분화 10건
