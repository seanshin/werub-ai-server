#!/usr/bin/env python3
"""단일 발간물/지침 파일(PDF/HWPX/HWP/TXT) → medical_pub_knowledge RAG 색인.

ingest_hira_pubs.py(폴더+MANIFEST 일괄)의 단건 버전. 구형 .hwp는 doc_parser가
olefile(순수 Python)로 처리. 멱등(source_file delete-first). GPU 전용(bge-m3+ChromaDB).

  venv/bin/python scripts/ingest_pub_file.py ~/guide.hwp \
    --title "저가약 대체조제 약제비 청구요령" --category 심사기준 --doc-type 청구작성요령 --dry-run
"""
import argparse
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.doc_parser import chunk_notice, parse_document  # noqa: E402

COLLECTION = "medical_pub_knowledge"


def _doc_id(source_file: str, seq: int) -> str:
    return "hp-" + hashlib.md5(f"{source_file}|{seq}".encode("utf-8")).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="문서 경로(.pdf/.hwpx/.hwp/.txt)")
    ap.add_argument("--title", required=True, help="문서 제목(law_name 메타)")
    ap.add_argument("--category", default="심사기준", help="의료법 RAG category 라벨")
    ap.add_argument("--doc-type", default="발간물", help="doc_type 메타")
    ap.add_argument("--origin", default="hira_pub")
    ap.add_argument("--collection", default=COLLECTION)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = os.path.expanduser(args.file)
    if not os.path.isfile(path):
        print(f"[err] 파일 없음: {path}", file=sys.stderr)
        return 2

    t = time.time()
    pd = parse_document(path)
    if not pd.ok:
        print(f"[err] 파싱 실패: parser={pd.parser} error={pd.error}", file=sys.stderr)
        return 1
    chunks = chunk_notice(pd.text)
    sf = os.path.basename(path)
    print(f"[parse] {time.time()-t:.1f}s | parser={pd.parser} | chars={len(pd.text)} "
          f"chunks={len(chunks)} :: {args.title[:50]}")

    if args.dry_run:
        print("[dry-run] 색인 생략")
        for ch in chunks[:3]:
            print("   chunk:", repr(ch[:90]))
        return 0
    if not chunks:
        print("[err] 청크 0 — 색인 생략", file=sys.stderr)
        return 1

    from client.rag import embed_text, get_collection
    col = get_collection(args.collection)
    try:
        col.delete(where={"source_file": sf})  # 멱등(재색인 시 구청크 제거)
        print(f"[rag] 기존 {sf} 청크 정리")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] delete {sf}: {e}", file=sys.stderr)
    ok = 0
    for seq, ch in enumerate(chunks):
        meta = {
            "law_name": args.title, "law_level": "발간물", "category": args.category,
            "source": "HIRA", "source_file": sf, "origin": args.origin,
            "doc_type": args.doc_type, "chunk_seq": seq,
        }
        try:
            emb = embed_text(ch, prefix="search_document")
            col.upsert(ids=[_doc_id(sf, seq)], embeddings=[emb], documents=[ch], metadatas=[meta])
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[warn] upsert {sf}#{seq}: {e}", file=sys.stderr)
        if (seq + 1) % 100 == 0:
            print(f"[rag] {seq+1}/{len(chunks)} ...")
    print(f"[rag] 완료 ok={ok}/{len(chunks)} collection={args.collection} count={col.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
