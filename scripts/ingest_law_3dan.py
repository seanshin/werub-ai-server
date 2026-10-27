#!/usr/bin/env python3
"""법령 3단비교표 PDF → medical_law_knowledge ChromaDB 색인 (medical-law-rag P0 부트스트랩).

GPU 서버 전용 실행(bge-m3 임베딩 + ChromaDB). iMac은 파서 단위테스트만.
  venv/bin/python scripts/ingest_law_3dan.py /path/to/*.pdf
  venv/bin/python scripts/ingest_law_3dan.py --dir ~/law_pdfs --dry-run

- 멱등: source_file 기준 기존 청크 삭제 후 upsert(재실행 안전).
- 컬렉션: medical_law_knowledge (notice_knowledge/medical_knowledge와 분리).
- dry-run: 임베딩/색인 없이 파싱·청크 수만 출력(iMac 검증 가능).
"""
import argparse
import glob
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.law_pdf_parser import parse_3dan_pdf, to_chunks  # noqa: E402

COLLECTION = "medical_law_knowledge"


def _chunk_id(meta: dict) -> str:
    raw = f"{meta['source_file']}|{meta['law_level']}|{meta['article_number']}|{meta['chunk_seq']}"
    return "ml-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", help="PDF 파일 경로들")
    ap.add_argument("--dir", help="디렉토리 내 *3단비교*.pdf 일괄")
    ap.add_argument("--dry-run", action="store_true", help="파싱만(임베딩/색인 없음)")
    ap.add_argument("--collection", default=COLLECTION)
    args = ap.parse_args()

    files = list(args.paths)
    if args.dir:
        # macOS는 파일명을 NFD로 저장 → NFC 패턴 glob 실패. 전체 PDF를 NFC 정규화 후 부분일치.
        import unicodedata
        d = os.path.expanduser(args.dir)
        for p in sorted(glob.glob(os.path.join(d, "*.pdf"))):
            base = unicodedata.normalize("NFC", os.path.basename(p))
            if "비교" in base:  # 3단비교 / 법령단위비교
                files.append(p)
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print("[ingest] PDF 없음", file=sys.stderr)
        return 2

    # 파싱 + 청크
    all_chunks: list[dict] = []
    for f in files:
        doc = parse_3dan_pdf(f)
        chunks = to_chunks(doc)
        all_chunks.extend(chunks)
        print(f"[parse] {doc.law_name:14s} chunks={len(chunks):4d}  ({os.path.basename(f)})")
    print(f"[parse] 총 {len(all_chunks)} 청크 / {len(files)} 파일")

    if args.dry_run:
        print("[dry-run] 임베딩·색인 생략")
        return 0

    # GPU 전용: 임베딩 + 색인
    from client.rag import embed_text, get_collection  # lazy(GPU)
    col = get_collection(args.collection)

    # 멱등: 이번 파일들의 기존 청크 제거
    src_files = sorted({c["metadata"]["source_file"] for c in all_chunks})
    for sf in src_files:
        try:
            col.delete(where={"source_file": sf})
        except Exception as e:  # noqa: BLE001
            print(f"[warn] delete {sf}: {e}", file=sys.stderr)

    ok = 0
    for i, c in enumerate(all_chunks):
        try:
            emb = embed_text(c["text"], prefix="search_document")
            col.upsert(ids=[_chunk_id(c["metadata"])], embeddings=[emb],
                       documents=[c["text"]], metadatas=[c["metadata"]])
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"[warn] embed/upsert #{i}: {e}", file=sys.stderr)
        if (i + 1) % 100 == 0:
            print(f"[index] {i + 1}/{len(all_chunks)} ...")
    print(f"[index] 완료 ok={ok}/{len(all_chunks)} collection={args.collection} count={col.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
