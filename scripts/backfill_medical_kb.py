#!/usr/bin/env python3
"""의료 지식 RAG 통합 백필 (v2.88.2 이중 store 통합)

medical.db medical_knowledge(원본)를 client.rag 통합 store(vector_db/medical_knowledge
벡터 + FTS5 키워드)로 재색인한다. 구 chromadb_data store는 손대지 않음(동결 보존).

멱등(upsert) — 재실행 안전. GPU에서: venv/bin/python scripts/backfill_medical_kb.py [--dry-run]
"""
import json
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

COLLECTION = "medical_knowledge"


def main(dry_run: bool = False) -> int:
    conn = sqlite3.connect(os.path.join(BASE_DIR, "medical.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, tenant_id, category, title, content, source FROM medical_knowledge").fetchall()
    print(f"medical.db 원본: {len(rows)}행")
    if not rows:
        return 0

    docs = []
    for r in rows:
        docs.append({
            "id": r["id"],
            "text": f"{r['title']}\n{r['content']}",
            "metadata": {
                "category": r["category"] or "",
                "title": r["title"] or "",
                "source": r["source"] or "",
                # 레거시 tenant_id NULL = 공유 지식
                "tenant_id": r["tenant_id"] if r["tenant_id"] else "shared",
            },
        })

    if dry_run:
        print(json.dumps(docs[0], ensure_ascii=False)[:300])
        print(f"[dry-run] {len(docs)}건 색인 예정 (vector+FTS, collection={COLLECTION})")
        return 0

    from client.rag import index_batch, fts_index_batch, collection_stats
    res = index_batch(docs, collection_name=COLLECTION)
    fts_index_batch(docs, collection=COLLECTION)
    print(f"벡터 색인: {res} / FTS 색인: {len(docs)}건")
    print(f"collection_stats: {collection_stats(COLLECTION)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
