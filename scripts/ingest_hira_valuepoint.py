#!/usr/bin/env python3
"""HIRA 「건강보험요양급여비용」(상대가치점수책) PDF 적재 — fee_codes + medical_law RAG.

한 번의 파싱으로 두 산출물을 멱등 적재한다(C: 둘 다):
  ① fee_codes(notice.db) — code → category(TARIFF) → name  (ERP code→notice_type 해석)
  ② medical_law_knowledge(ChromaDB) — 분류번호 항목·산정지침·별첨3 평가결과 RAG 색인

GPU 전용(bge-m3+ChromaDB). 대량(2.9K 청크)이므로 임베딩에 수 분 소요.

  venv/bin/python scripts/ingest_hira_valuepoint.py ~/valuepoint.pdf --dry-run
  venv/bin/python scripts/ingest_hira_valuepoint.py ~/valuepoint.pdf
  venv/bin/python scripts/ingest_hira_valuepoint.py ~/valuepoint.pdf --no-fees   # RAG만
  venv/bin/python scripts/ingest_hira_valuepoint.py ~/valuepoint.pdf --no-rag    # fee_codes만
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.hira_fee_parser import parse_valuepoint, fee_rows, rag_documents  # noqa: E402

COLLECTION = "medical_law_knowledge"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="valuepoint PDF 경로")
    ap.add_argument("--dry-run", action="store_true", help="파싱·집계만(적재 생략)")
    ap.add_argument("--no-fees", action="store_true", help="fee_codes 적재 생략")
    ap.add_argument("--no-rag", action="store_true", help="RAG 색인 생략")
    ap.add_argument("--collection", default=COLLECTION)
    ap.add_argument("--category", default="TARIFF", help="fee_codes category(기본 TARIFF)")
    args = ap.parse_args()

    path = os.path.expanduser(args.pdf)
    if not os.path.isfile(path):
        print(f"[err] 파일 없음: {path}", file=sys.stderr)
        return 2

    t = time.time()
    doc = parse_valuepoint(path)
    rows = fee_rows(doc, category=args.category)
    docs = rag_documents(doc)
    print(f"[parse] {time.time()-t:.1f}s | edition={doc.edition or '?'} | "
          f"fee_items={len(doc.fee_items)} fee_rows={len(rows)} | "
          f"chunks={len(doc.chunks)} rag_docs={len(docs)}")

    if args.dry_run:
        print("[dry-run] 적재 생략")
        for r in rows[:5]:
            print("   fee:", r["code"], r["name"][:50])
        for did, txt, m in docs[:3]:
            print(f"   rag: [{m['section']}] {m['category_no']} {txt[:50]!r}")
        return 0

    # ① fee_codes 적재 (notice.db, INSERT OR REPLACE 멱등)
    if not args.no_fees and rows:
        from core.fee_codes import import_fee_codes, fee_stats
        n = import_fee_codes(rows)
        print(f"[fees] 적재 {n}건 | stats={fee_stats()}")

    # ② RAG 색인 (medical_law_knowledge, source_file delete-first 멱등)
    if not args.no_rag and docs:
        from client.rag import embed_text, get_collection
        col = get_collection(args.collection)
        sf = doc.source_file
        try:
            col.delete(where={"source_file": sf})  # 멱등(같은 책 재색인 시 구청크 제거)
            print(f"[rag] 기존 {sf} 청크 정리")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] delete: {e}", file=sys.stderr)
        ok = 0
        total = len(docs)
        for i, (did, txt, m) in enumerate(docs):
            try:
                emb = embed_text(txt, prefix="search_document")
                col.upsert(ids=[did], embeddings=[emb], documents=[txt], metadatas=[m])
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"[warn] upsert {did}: {e}", file=sys.stderr)
            if (i + 1) % 200 == 0:
                print(f"[rag] {i+1}/{total} ...")
        print(f"[rag] 완료 ok={ok}/{total} collection={args.collection} count={col.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
