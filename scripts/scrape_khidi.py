#!/usr/bin/env python3
"""KHIDI 고시 게시판 직접 수집 → medical_law_knowledge 색인 (medical-law-rag P1 백필).

공공 법령정보(저작권 비보호) 정중 수집(단일스레드+지연). GPU 전용 실행(bge-m3+ChromaDB).
  venv/bin/python scripts/scrape_khidi.py --max-pages 2                 # 최근 2페이지(40건)
  venv/bin/python scripts/scrape_khidi.py --max-pages 79 --dest ~/khidi # 전량 백필
  venv/bin/python scripts/scrape_khidi.py --max-pages 1 --limit 5 --no-index  # 수집만(검증)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.khidi_collector as khidi  # noqa: E402
from core.doc_parser import parse_document, chunk_notice  # noqa: E402

COLLECTION = "medical_law_knowledge"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-pages", type=int, default=1)
    ap.add_argument("--start-page", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="최대 고시 수")
    ap.add_argument("--dest", default="khidi_notices", help="다운로드 저장 디렉토리")
    ap.add_argument("--pdf-only", action="store_true", default=True)
    ap.add_argument("--no-index", action="store_true", help="수집만(색인 생략)")
    ap.add_argument("--collection", default=COLLECTION)
    args = ap.parse_args()

    dest = os.path.expanduser(args.dest)
    max_no1 = [0]

    def _prog(no1, title, ext, path):
        max_no1[0] = max(max_no1[0], int(no1))
        print(f"[down] no1={no1} {ext} {title[:45]}", flush=True)

    print(f"[scrape] pages {args.start_page}..{args.start_page + args.max_pages - 1} "
          f"delay={khidi.DELAY_S}s → {dest}", flush=True)
    saved = khidi.collect(max_pages=args.max_pages, start_page=args.start_page,
                          pdf_only=args.pdf_only, dest_dir=dest, limit=args.limit, progress=_prog)
    print(f"[scrape] 다운로드 {len(saved)}건 (max no1={max_no1[0]})", flush=True)

    # 증분 watermark 갱신 — 백필이 최고 게시번호를 기록 → 이후 스케줄러는 신규(>last_no1)만 수집
    if max_no1[0]:
        try:
            from core.medlaw_processor import _load_state, _save_state
            st = _load_state()
            if max_no1[0] > int(st.get("last_no1", 0)):
                st["last_no1"] = max_no1[0]
                _save_state(st)
                print(f"[scrape] watermark last_no1={max_no1[0]} 기록", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] watermark: {e}", flush=True)

    if not saved:
        return 0

    # 파싱 + 청크
    import hashlib
    from core.doc_parser import notice_metadata

    records = []
    for p in saved:
        pd = parse_document(p)
        if not pd.ok:
            print(f"[skip] {os.path.basename(p)} ({pd.parser}: {pd.error})", flush=True)
            continue
        records.append((notice_metadata(p, pd.text), chunk_notice(pd.text)))
    total = sum(len(c) for _, c in records)
    print(f"[parse] {len(records)} 문서 / {total} 청크", flush=True)

    if args.no_index:
        print("[no-index] 색인 생략", flush=True)
        return 0

    from client.rag import embed_text, get_collection  # GPU
    col = get_collection(args.collection)
    for meta, _ in records:
        try:
            col.delete(where={"source_file": meta["source_file"]})
        except Exception as e:  # noqa: BLE001
            print(f"[warn] delete: {e}", flush=True)

    def _cid(meta, seq):
        return "kn-" + hashlib.md5(f"{meta['source_file']}|{seq}".encode()).hexdigest()[:16]

    ok = done = 0
    for meta, chunks in records:
        for seq, ch in enumerate(chunks):
            try:
                emb = embed_text(ch, prefix="search_document")
                m = dict(meta); m["chunk_seq"] = seq
                col.upsert(ids=[_cid(meta, seq)], embeddings=[emb], documents=[ch], metadatas=[m])
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"[warn] upsert: {e}", flush=True)
            done += 1
            if done % 200 == 0:
                print(f"[index] {done}/{total} ...", flush=True)
    print(f"[index] 완료 ok={ok}/{total} count={col.count()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
