"""공시 색인 잡 처리기 — erp-notice-rag Day 3.

pending `notice_index_jobs` → ChromaDB(`notice_knowledge`) 색인/제거.
- index : 공시 본문 chunk_notice → bge-m3 임베딩 → upsert
          (메타: notice_id/erp_ref_id/notice_type/status/effective_from·to/chunk_seq)
- remove: notice_id 청크 일괄 삭제 (개정 supersede / 폐지 / 삭제)
- immediate: 제출 직후 백그라운드 처리(SLA 5분 목표)
- nightly  : 스케줄러가 notice_nightly_hour(기본 3시 UTC)에 일괄 처리(24h SLA)

설계: docs/02-design/features/erp-notice-rag.design.md §5,§9
임베딩: 운영 중인 bge-m3(1024d, client.rag.embed_text) 재사용 — 14b와 공존 검증됨
        (qwen3-embedding:8b는 MAX_LOADED_MODELS=1에서 chat 모델 축출 우려로 보류).
검색: `POST /api/rag/search {collection:"notice_knowledge", ...}`
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from core.notice_db import get_notice_db

logger = logging.getLogger("ollama-admin")

NOTICE_COLLECTION = "notice_knowledge"
_EMBED_ENGINE = "bge-m3"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _chunk_meta(notice, seq: int, kind: str) -> dict:
    """ChromaDB 메타 — None 값은 제외(ChromaDB는 None 메타 불가)."""
    m = {
        "notice_id": notice["id"],
        "erp_ref_id": notice["erp_ref_id"],
        "revision_no": notice["revision_no"],
        "notice_type": notice["notice_type"],
        "title": notice["title"],
        "status": notice["status"],
        "effective_from": notice["effective_from"],
        "chunk_seq": seq,
        "chunk_kind": kind,
    }
    for k in ("effective_to", "doc_no", "source"):
        if notice[k]:
            m[k] = notice[k]
    return m


def remove_notice(notice_id: str) -> int:
    """공시의 모든 청크를 컬렉션에서 제거."""
    from client.rag import get_collection
    col = get_collection(NOTICE_COLLECTION)
    col.delete(where={"notice_id": notice_id})
    return 0


def index_notice(conn, notice_id: str) -> int:
    """공시 본문을 청크·임베딩하여 색인(재색인 멱등: 기존 청크 선삭제). 청크 수 반환."""
    from client.rag import get_collection, embed_text
    from core.notice_indexer import chunk_notice

    row = conn.execute("SELECT * FROM notices WHERE id=?", (notice_id,)).fetchone()
    if not row:
        raise ValueError(f"notice {notice_id} not found")

    chunks = chunk_notice(row["body"])
    col = get_collection(NOTICE_COLLECTION)
    col.delete(where={"notice_id": notice_id})  # 멱등 재색인
    if not chunks:
        return 0

    ids, embs, docs, metas = [], [], [], []
    for ch in chunks:
        ids.append(f"{notice_id}#c{ch.seq}")
        embs.append(embed_text(ch.text, prefix="search_document"))
        docs.append(ch.text)
        metas.append(_chunk_meta(row, ch.seq, ch.kind))
    col.upsert(ids=ids, embeddings=embs, documents=docs, metadatas=metas)
    return len(ids)


def process_job(conn, job) -> dict:
    """단일 잡 처리 — pending→processing→completed/failed 상태 전이."""
    jid = job["id"]
    conn.execute("UPDATE notice_index_jobs SET status='processing', started_at=? WHERE id=?",
                 (_now(), jid))
    conn.commit()
    try:
        if job["action"] == "index":
            n = index_notice(conn, job["notice_id"])
        elif job["action"] == "remove":
            n = remove_notice(job["notice_id"])
        else:
            raise ValueError(f"unknown action {job['action']}")
        conn.execute(
            "UPDATE notice_index_jobs SET status='completed', engine=?, chunks=?, "
            "error=NULL, finished_at=? WHERE id=?",
            (_EMBED_ENGINE, n, _now(), jid))
        conn.commit()
        return {"id": jid, "notice_id": job["notice_id"], "action": job["action"],
                "status": "completed", "chunks": n}
    except Exception as e:
        logger.warning(f"[notice] job {jid} ({job['action']} {job['notice_id']}) failed: {e}")
        conn.execute(
            "UPDATE notice_index_jobs SET status='failed', error=?, "
            "retry_count=retry_count+1, finished_at=? WHERE id=?",
            (str(e)[:500], _now(), jid))
        conn.commit()
        return {"id": jid, "notice_id": job["notice_id"], "action": job["action"],
                "status": "failed", "error": str(e)[:200]}


def process_pending_jobs(conn=None, modes=None, limit: int = 500) -> dict:
    """pending 잡 일괄 처리. modes=["immediate"]/["nightly"]/None(전체)."""
    conn = conn or get_notice_db()
    q = "SELECT * FROM notice_index_jobs WHERE status='pending'"
    params: list = []
    if modes:
        q += " AND mode IN (%s)" % ",".join("?" * len(modes))
        params += list(modes)
    q += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    jobs = conn.execute(q, params).fetchall()
    results = [process_job(conn, j) for j in jobs]
    return {
        "processed": len(results),
        "completed": sum(1 for r in results if r["status"] == "completed"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "jobs": results,
    }


def process_immediate_async():
    """immediate 잡을 백그라운드 스레드로 즉시 소진(요청 비차단)."""
    def _run():
        try:
            conn = get_notice_db()  # 신규 스레드 → 자체 thread-local 연결
            r = process_pending_jobs(conn, modes=["immediate"])
            if r["processed"]:
                logger.info(f"[notice] immediate processed: {r['completed']} ok / {r['failed']} fail")
        except Exception as e:
            logger.warning(f"[notice] immediate worker error: {e}")
    threading.Thread(target=_run, name="notice-immediate", daemon=True).start()


_scheduler_started = False


def start_notice_scheduler(nightly_hour: int = 3, interval_s: int = 300):
    """경량 백그라운드 스케줄러 — immediate 안전망(5분) + nightly 일괄(하루 1회 nightly_hour)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        last_nightly_day = None
        while True:
            try:
                conn = get_notice_db()
                # immediate 누락 안전망
                process_pending_jobs(conn, modes=["immediate"])
                now = datetime.now(timezone.utc)
                if now.hour == nightly_hour and last_nightly_day != now.date():
                    r = process_pending_jobs(conn, modes=["nightly"])
                    last_nightly_day = now.date()
                    logger.info(f"[notice] nightly batch: {r['completed']} ok / {r['failed']} fail")
            except Exception as e:
                logger.warning(f"[notice] scheduler error: {e}")
            time.sleep(interval_s)

    threading.Thread(target=_loop, name="notice-scheduler", daemon=True).start()
    logger.info(f"[notice] scheduler started (nightly_hour={nightly_hour} UTC, interval={interval_s}s)")
