"""core.hira_drug_sync 테스트 (네트워크 모킹, 인메모리 — 실 사이트/GPU 불요).

핵심: ①게시판 파서(최신 게시글·첨부 파라미터 키워드 선택) ②안전 갱신 게이트
(건수 sane·동일 게시글 skip·오류 시 기존 보존) ③다중 소스(약제급여목록+저가약
대체조제) ④_sync_due(워커 churn 과다호출 방지).
"""
import os
import sqlite3
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import hira_drug_sync as hds  # noqa: E402

DRUG_SRC = "약제급여목록"
SUB_SRC = "저가약대체조제"

LIST_HTML = """
<td class="col-tit"><a href="?pgmid=HIRAA030014050000&amp;brdScnBltNo=4&amp;brdBltNo=1707&amp;pageIndex=1">2026.07.01. 현재 약제급여목록및급여상한금액표</a></td>
<td class="col-tit"><a href="?pgmid=HIRAA030014050000&amp;brdScnBltNo=4&amp;brdBltNo=1706&amp;pageIndex=1">2026.06.01. 현재 약제급여목록및급여상한금액표</a></td>
<td class="col-tit"><a href="?pgmid=HIRAA030014050000&amp;brdScnBltNo=4&amp;brdBltNo=1710&amp;pageIndex=1">무관한 다른 공지</a></td>
"""

SUB_LIST_HTML = """
<td><a href="?pgmid=HIRAA030015000000&amp;brdBltNo=449&amp;pageIndex=1">저가약 대체조제 장려금 지급대상 의약품 현황('26. 6월 기준)</a></td>
<td><a href="?pgmid=HIRAA030015000000&amp;brdBltNo=448&amp;pageIndex=1">저가약 대체조제 장려금 지급대상 의약품 현황('26. 5월 기준)</a></td>
"""

VIEW_HTML = """
<li><i class="icon-file-hwp"></i> 저가약 대체조제 약제비 청구요령
  <a href="#none" onclick="downLoadBbs('1','449','1','56');">첨부파일 다운로드</a></li>
<li><i class="icon-file-xls"></i> 저가약 대체조제 장려금 지급대상 의약품 목록_10,974품목(2026.6.1.시행)
  <a href="#none" onclick="downLoadBbs('2','449','1','56');">첨부파일 다운로드</a></li>
"""


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def _patch_db(monkeypatch, c):
    import core.notice_db as ndb
    monkeypatch.setattr(ndb, "get_notice_db", lambda *a, **k: c)


def _patch_drug_parser(monkeypatch, rows, edition="2026-07-01"):
    import core.hira_drug_parser as parser
    doc = SimpleNamespace(edition=edition, count=len(rows), source_file="t.xlsx")
    monkeypatch.setattr(parser, "parse_drug_xlsx", lambda path: doc)
    monkeypatch.setattr(parser, "fee_rows", lambda d, **k: rows)


def _fake_http(monkeypatch, list_html=LIST_HTML, view_html=VIEW_HTML, download=b"PK\x03\x04xlsx"):
    def _get(url, timeout=60, max_bytes=0):
        if "bbsCDownLoad" in url:
            return download
        if "brdBltNo=" in url:
            return view_html.encode()
        return list_html.encode()
    monkeypatch.setattr(hds, "_http_get", _get)


def _drug_rows(n):
    return [{"code": f"C{i:06d}", "category": "DRUG", "name": f"약{i}"} for i in range(n)]


def _patch_min(monkeypatch, n=5):
    monkeypatch.setattr(hds, "_SOURCES", [
        (s[0], s[1], s[2], s[3], n, s[5]) for s in hds._SOURCES])


# --- 파서 ---------------------------------------------------------------

def test_parse_list_latest_picks_max_post():
    p = hds.parse_list_latest(LIST_HTML, "약제급여목록및급여상한금액표")
    assert p == {"post_no": "1707", "title": "2026.07.01. 현재 약제급여목록및급여상한금액표"}


def test_parse_list_latest_none_when_absent():
    assert hds.parse_list_latest("<html>없음</html>", "약제급여목록및급여상한금액표") is None


def test_parse_download_params_prefers_keyword_attachment():
    # 첨부 2개(hwp 청구요령 + xlsx 목록) 중 키워드 매칭 xlsx 선택
    p = hds.parse_download_params(VIEW_HTML, "지급대상 의약품 목록")
    assert p == {"apnd_no": "2", "brd_blt_no": "449", "brd_ty_no": "1", "blt_no": "56"}


def test_parse_download_params_fallback_first():
    html = "<a onclick=\"downLoadBbs('3','100','1','7');\">다운로드</a>"
    assert hds.parse_download_params(html, "없는키워드")["apnd_no"] == "3"
    assert hds.parse_download_params("<html/>", "x") is None


# --- 약제급여목록 소스 ---------------------------------------------------

def test_drug_sync_ok_replaces_drug_keeps_tariff(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c); _fake_http(monkeypatch)
    _patch_drug_parser(monkeypatch, _drug_rows(20)); _patch_min(monkeypatch)
    from core.fee_codes import import_fee_codes
    import_fee_codes([{"code": "OLD1", "category": "DRUG", "name": "구약"},
                      {"code": "EB411", "category": "TARIFF", "name": "수가"}], conn=c)
    r = hds.sync_one(DRUG_SRC)
    assert r["ok"] and r["status"] == "ok" and r["count"] == 20
    assert r["edition"] == "2026-07-01"
    drug = {x[0] for x in c.execute("SELECT code FROM fee_codes WHERE category='DRUG'")}
    assert "OLD1" not in drug and len(drug) == 20          # DRUG 전량 교체
    assert c.execute("SELECT COUNT(*) FROM fee_codes WHERE category='TARIFF'").fetchone()[0] == 1
    meta = hds._get_meta(DRUG_SRC)
    assert meta["status"] == "ok" and meta["edition"] == "2026-07-01"


def test_drug_sync_skip_low_count_keeps_existing(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c); _fake_http(monkeypatch)
    _patch_drug_parser(monkeypatch, _drug_rows(3))          # min(15000) 미달
    from core.fee_codes import import_fee_codes
    import_fee_codes([{"code": "OLD1", "category": "DRUG", "name": "구약"}], conn=c)
    r = hds.sync_one(DRUG_SRC)
    assert not r["ok"] and r["status"] == "skip"
    assert c.execute("SELECT COUNT(*) FROM fee_codes WHERE category='DRUG'").fetchone()[0] == 1


def test_drug_sync_skip_same_post_no_download(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c); _fake_http(monkeypatch)
    _patch_drug_parser(monkeypatch, _drug_rows(20)); _patch_min(monkeypatch)
    assert hds.sync_one(DRUG_SRC)["status"] == "ok"         # 1회차 적재
    calls = {"n": 0}
    orig = hds._http_get

    def _count(url, **k):
        if "bbsCDownLoad" in url:
            calls["n"] += 1
        return orig(url, **k)
    monkeypatch.setattr(hds, "_http_get", _count)
    r = hds.sync_one(DRUG_SRC)                              # 같은 게시글 → 다운로드 없이 skip
    assert r["ok"] and r["status"] == "skip" and calls["n"] == 0
    assert hds.sync_one(DRUG_SRC, force=True)["status"] == "ok"   # force → 재적재


def test_drug_sync_error_on_fetch_keeps_existing(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)

    def _boom(*a, **k):
        raise RuntimeError("site down")
    monkeypatch.setattr(hds, "_http_get", _boom)
    from core.fee_codes import import_fee_codes
    import_fee_codes([{"code": "OLD1", "category": "DRUG", "name": "구약"}], conn=c)
    r = hds.sync_one(DRUG_SRC)
    assert not r["ok"] and "list" in r["error"]
    assert c.execute("SELECT COUNT(*) FROM fee_codes WHERE category='DRUG'").fetchone()[0] == 1
    assert hds._get_meta(DRUG_SRC)["status"] == "error"


def test_drug_sync_rejects_non_zip_attachment(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    _fake_http(monkeypatch, download=b"<html>error page</html>")
    r = hds.sync_one(DRUG_SRC)
    assert not r["ok"] and "xlsx" in r["error"]


# --- 저가약대체조제 소스 --------------------------------------------------

def _patch_sub_parser(monkeypatch, n):
    import core.hira_drug_parser as parser
    rows = [{"code": f"S{i:04d}", "ingr_code": "1492XATB", "rep_code": f"R{i}",
             "product": f"약{i}", "price": "100", "suspended": ""} for i in range(n)]
    monkeypatch.setattr(parser, "iter_xlsx_records",
                        lambda path, headers, sheet_index=0: (rows, None, "t.xlsx"))
    return rows


def test_sub_sync_ok_clear_replaces(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    _fake_http(monkeypatch, list_html=SUB_LIST_HTML)
    _patch_sub_parser(monkeypatch, 20); _patch_min(monkeypatch)
    from core.drug_substitution import import_substitution, stats
    import_substitution([{"code": "OLDSUB", "ingr_code": "X", "rep_code": "R",
                          "product": "구약", "price": "10", "suspended": ""}],
                        {"OLDSUB"}, conn=c)
    r = hds.sync_one(SUB_SRC)
    assert r["ok"] and r["status"] == "ok" and r["count"] == 20
    assert "'26. 6월 기준" in r["edition"]                   # edition=게시글 제목 폴백
    s = stats(conn=c)
    assert s["total"] == 20                                  # OLDSUB 잔존 없음(clear 교체)


def test_sub_sync_skip_low_count_keeps_existing(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    _fake_http(monkeypatch, list_html=SUB_LIST_HTML)
    _patch_sub_parser(monkeypatch, 3)                        # min(8000) 미달
    from core.drug_substitution import import_substitution, stats
    import_substitution([{"code": "OLDSUB", "ingr_code": "X", "rep_code": "R",
                          "product": "구약", "price": "10", "suspended": ""}],
                        {"OLDSUB"}, conn=c)
    r = hds.sync_one(SUB_SRC)
    assert not r["ok"] and r["status"] == "skip"
    assert stats(conn=c)["total"] == 1                       # 기존 보존


def test_sync_all_sources(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    _fake_http(monkeypatch, list_html=LIST_HTML + SUB_LIST_HTML)
    _patch_drug_parser(monkeypatch, _drug_rows(20))
    # iter_xlsx_records는 sub 전용 — drug는 parse_drug_xlsx 모킹이 우선이라 충돌 없음
    _patch_sub_parser(monkeypatch, 20); _patch_min(monkeypatch)
    r = hds.sync()
    assert r["ok"] and r["summary"] == "2/2 ok"
    assert {x["source"] for x in r["results"]} == {DRUG_SRC, SUB_SRC}
    st = hds.sync_status()
    # 게시판 2소스 + 치료재료(API, 메타 없으면 None) 관측
    assert {s["source"] for s in st["sources"]} == {DRUG_SRC, SUB_SRC, "치료재료"}
    board = [s for s in st["sources"] if s["source"] in (DRUG_SRC, SUB_SRC)]
    assert all(s["meta"]["status"] == "ok" for s in board)


def test_sync_unknown_source():
    assert hds.sync(source="없는소스")["ok"] is False


# --- _sync_due (워커 churn 과다호출 방지) -----------------------------------

def test_sync_due_true_when_never_checked(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    assert hds._sync_due(7 * 86400, DRUG_SRC) is True


def test_sync_due_false_after_recent_check(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c); _fake_http(monkeypatch)
    _patch_drug_parser(monkeypatch, _drug_rows(20)); _patch_min(monkeypatch)
    hds.sync_one(DRUG_SRC)
    assert hds._sync_due(7 * 86400, DRUG_SRC) is False       # 방금 확인 → skip
    assert hds._sync_due(7 * 86400, SUB_SRC) is True         # 소스별 독립


def test_sync_due_true_when_stale(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    hds._meta_db().execute(
        "INSERT INTO hira_drug_sync_meta (source,status,last_check_at) VALUES (?,?,?)",
        (DRUG_SRC, "ok", "2020-01-01T00:00:00+00:00Z"))
    c.commit()
    assert hds._sync_due(7 * 86400, DRUG_SRC) is True


def test_sync_due_false_on_unparseable(monkeypatch):
    c = _conn(); _patch_db(monkeypatch, c)
    hds._meta_db().execute(
        "INSERT INTO hira_drug_sync_meta (source,status,last_check_at) VALUES (?,?,?)",
        (DRUG_SRC, "ok", "not-a-date"))
    c.commit()
    assert hds._sync_due(7 * 86400, DRUG_SRC) is False       # 불확실 → 보수적 skip
