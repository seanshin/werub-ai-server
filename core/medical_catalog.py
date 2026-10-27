"""의료 데이터 카탈로그 — 내부 시스템용 단일 발견 지점.

왜 필요한가(2026-08-31 실측): 의료 엔드포인트가 **26개 태그로 흩어져** 있다
(`Medical - DUR`·`Medical - DrugInfo`·`Notice`·`Medical Law`… 1~3개짜리 태그가 다수).
전체 622경로 안에서 내부 시스템 연동자가 "무슨 의료 데이터가 있고 어떤 API로 꺼내는가"를
알아낼 방법이 문서를 통독하는 것뿐이었다. 이 모듈은 그 답을 **기계가 읽는 형태**로 낸다.

설계 규율:
- **데이터셋별 fail-open**: 한 DB가 없거나 깨져도 나머지는 낸다. 대신 `available: false`와
  `error`를 실어 **조용히 비어 보이지 않게** 한다(이 레포 1급 결함 클래스 = 200인데 빈 결과).
- **집계만** 노출한다 — 건수·최신성·서빙 엔드포인트. 레코드 본문은 각 API 소관이고
  여기서 PHI·본문을 흘리지 않는다.
- 최신성은 **수집 로그(collect_log)·동기화 메타**에서 읽는다. 없으면 `null`(0으로 세지 않는다 —
  결측을 0으로 세면 "최신"으로 보인다).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _db(name: str) -> sqlite3.Connection:
    path = os.path.join(_BASE_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(name)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _count(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return int(conn.execute(sql).fetchone()[0])


# ── 카탈로그 정의 ───────────────────────────────────────────────────────────
# (id, 한글명, 출처, DB, 표, 서빙 엔드포인트, 수집 로그의 source 라벨)
_DATASETS = [
    ("drug_master", "약가 마스터(제품·표준코드)", "HIRA 약제급여목록", "notice.db", "drug_master",
     ["GET /api/notice/drug-master/stats", "POST /api/notice/drug-master/resolve"], "약제급여목록"),
    ("fee_codes", "코드 마스터(수가·치료재료·약가)", "HIRA·data.go.kr", "notice.db", "fee_codes",
     ["GET /api/notice/fee-codes/stats", "GET /api/notice/fee-codes/{code}"], "치료재료"),
    ("drug_substitution", "저가약 대체조제", "HIRA 저가약대체조제", "notice.db", "drug_substitution",
     ["GET /api/notice/drug-substitution/stats", "GET /api/notice/drug-substitution/{code}",
      "POST /api/notice/drug-substitution"], "저가약대체조제"),
    ("drug_easy_info", "의약품 개요정보(e약은요·환자용)", "식약처 data.go.kr", "drug_info.db",
     "drug_easy_info",
     ["POST /api/medical/drug-info", "GET /api/medical/drug-info/{item_seq}",
      "GET /api/medical/drug-info/stats"], "e약은요"),
    ("dur_interaction", "DUR 병용금기", "식약처 DUR", "dur.db", "dur_interaction",
     ["POST /api/medical/dur/check", "GET /api/medical/dur/stats"], "병용금기"),
    ("dur_single", "DUR 단일성분 금기·주의(6종)", "식약처 DUR", "dur.db", "dur_single",
     ["POST /api/medical/dur/drug", "GET /api/medical/dur/drug/{code}"], None),
    ("dur_product", "DUR 품목→성분 색인", "식약처 DUR", "dur.db", "dur_product",
     ["POST /api/medical/dur/check"], None),
    ("dur_split", "서방정 분할주의", "식약처 DUR 품목정보", "dur.db", "dur_split",
     ["POST /api/medical/dur/drug"], "서방정분할주의"),
    ("kcd_codes", "KCD 상병코드", "내부 구축", "clinic_codes.db", "kcd_codes",
     ["POST /api/medical/code/kcd", "POST /api/medical/code/mapping"], None),
    ("edi_codes", "EDI 청구코드", "내부 구축", "clinic_codes.db", "edi_codes",
     ["POST /api/medical/code/edi", "POST /api/medical/code/mapping"], None),
    ("claim_rules", "청구 심사 규칙", "내부 구축", "clinic_codes.db", "claim_rules",
     ["POST /api/medical/cli/execute"], None),
    ("denial_reasons", "삭감 사유 코드", "내부 구축", "clinic_codes.db", "denial_reasons",
     ["POST /api/medical/cli/execute"], None),
    ("dx_order_mapping", "진단-처방 매핑", "내부 구축", "clinic_codes.db", "dx_order_mapping",
     ["POST /api/medical/code/mapping"], None),
    ("notices", "고시·공고 원문 색인", "HIRA·복지부·식약처", "notice.db", "notices",
     ["POST /api/notice/search", "GET /api/notice/changes"], None),
    ("law_articles", "의료법 조문", "법제처", "medical_law.db", "law_articles",
     ["POST /api/medical-law/ask"], None),
    ("reportable_diseases", "법정감염병", "질병관리청", "medical_law.db", "reportable_diseases",
     ["POST /api/medical-law/ask"], None),
]

# ChromaDB 컬렉션(행 수 산정 방식이 SQLite와 다르다 — 별도 취급)
_RAG_COLLECTIONS = [
    ("medical_law_knowledge", "의료법·고시 RAG 코퍼스", "KHIDI·HIRA 수집",
     ["POST /api/medical-law/ask", "POST /api/medical-law/review"]),
    ("medical_knowledge", "의료 지식 RAG", "내부 구축", ["POST /api/medical/knowledge/search"]),
]


def _collect_log_index() -> dict:
    """수집 로그에서 source별 최근 1건(ts·status·count). 실패해도 카탈로그는 낸다."""
    out: dict = {}
    try:
        conn = _db("notice.db")
        rows = conn.execute(
            "SELECT source, status, count, ts FROM collect_log ORDER BY ts DESC").fetchall()
        for r in rows:
            out.setdefault(r["source"], {"status": r["status"], "count": r["count"],
                                         "ts": r["ts"]})
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    return out


def _age_days(ts: str | None) -> int | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(ts.rstrip("Z").replace("+00:00", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).days
    except Exception:  # noqa: BLE001
        return None


def build(include_rag: bool = True) -> dict:
    """의료 데이터 카탈로그 스냅샷."""
    logs = _collect_log_index()
    conns: dict = {}
    datasets = []
    for did, label, source, dbname, table, endpoints, log_src in _DATASETS:
        entry = {"id": did, "label": label, "source": source, "store": dbname,
                 "endpoints": endpoints, "available": False, "rows": None,
                 "last_sync_at": None, "last_sync_status": None, "age_days": None}
        try:
            if dbname not in conns:
                conns[dbname] = _db(dbname)
            entry["rows"] = _count(conns[dbname], table)
            entry["available"] = True
        except Exception as e:  # noqa: BLE001
            entry["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        if log_src and log_src in logs:
            lg = logs[log_src]
            entry["last_sync_at"] = lg["ts"]
            entry["last_sync_status"] = lg["status"]
            entry["age_days"] = _age_days(lg["ts"])
        datasets.append(entry)
    for c in conns.values():
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass

    rag = []
    if include_rag:
        for name, label, source, endpoints in _RAG_COLLECTIONS:
            item = {"id": name, "label": label, "source": source, "store": "chromadb",
                    "endpoints": endpoints, "available": False, "chunks": None}
            try:
                from client.rag import get_collection
                item["chunks"] = get_collection(name).count()
                item["available"] = True
            except Exception as e:  # noqa: BLE001
                item["error"] = f"{type(e).__name__}: {str(e)[:120]}"
            rag.append(item)

    avail = sum(1 for d in datasets if d["available"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        "datasets": datasets,
        "rag_collections": rag,
        "summary": {
            "datasets": len(datasets),
            "datasets_available": avail,
            "datasets_unavailable": len(datasets) - avail,
            "total_rows": sum(d["rows"] or 0 for d in datasets),
            "stalest_age_days": max((d["age_days"] for d in datasets
                                     if d["age_days"] is not None), default=None),
        },
        "sync_endpoints": {
            "dur": "POST /api/medical/dur/sync",
            "hira_board": "POST /api/notice/hira-drug/sync",
            "status": ["GET /api/medical/dur/sync-status",
                       "GET /api/notice/hira-drug/sync-status",
                       "GET /api/notice/collect/logs"],
        },
    }
