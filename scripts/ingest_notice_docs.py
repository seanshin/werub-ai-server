#!/usr/bin/env python3
"""KHIDI 등 수동 다운로드 고시 문서 → medical_law_knowledge 색인 (medical-law-rag P1 백필).

robots.txt 준수(자동 다운로드 금지) — 사용자가 수동 다운로드한 PDF/HWPX를 적재.
GPU 전용 실행(bge-m3+ChromaDB). hwp/hwpx는 config.json rhwp 설정 시만(미설정 시 PDF 권장).

  venv/bin/python scripts/ingest_notice_docs.py ~/khidi_notices/*.pdf
  venv/bin/python scripts/ingest_notice_docs.py --dir ~/khidi_notices --dry-run
"""
import argparse
import glob
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.doc_parser import parse_document, chunk_notice, notice_metadata  # noqa: E402

COLLECTION = "medical_law_knowledge"


def _cid(meta: dict, seq: int) -> str:
    raw = f"{meta['source_file']}|{seq}"
    return "kn-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--dir")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true",
                    help="이미 색인된 source_file 건너뜀(재개·대량 백필용)")
    ap.add_argument("--collection", default=COLLECTION)
    args = ap.parse_args()

    files = list(args.paths)
    if args.dir:
        d = os.path.expanduser(args.dir)
        for ext in ("pdf", "hwpx", "hwp", "txt"):
            files += sorted(glob.glob(os.path.join(d, f"*.{ext}")))
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        print("[ingest] 문서 없음", file=sys.stderr)
        return 2

    records = []  # (meta, chunks)
    skipped = []
    for f in files:
        pd = parse_document(f)
        if not pd.ok:
            skipped.append((os.path.basename(f), pd.parser, pd.error))
            print(f"[skip] {os.path.basename(f)} ({pd.parser}: {pd.error})", file=sys.stderr)
            continue
        chunks = chunk_notice(pd.text)
        meta = notice_metadata(f, pd.text)
        records.append((meta, chunks))
        print(f"[parse] {meta['law_name'][:30]:30s} {pd.parser:10s} chunks={len(chunks):3d} docno={meta['doc_no']}")

    total = sum(len(c) for _, c in records)
    print(f"[parse] {len(records)} 문서 / {total} 청크 / skip {len(skipped)}")

    # 수작업 전환 유도: 자동 처리 실패분(주로 구형 .hwp — rhwp 미배포)을 manifest로 산출.
    # 운영자가 이 목록을 한컴오피스로 PDF 변환→khidi_notices에 투입→재실행(--skip-existing)하면 색인됨.
    if skipped:
        manifest = os.path.join(os.path.expanduser(args.dir) if args.dir else ".", "_needs_manual.tsv")
        try:
            with open(manifest, "w", encoding="utf-8") as mf:
                mf.write("source_file\tparser\treason\n")
                for name, parser, err in skipped:
                    mf.write(f"{name}\t{parser}\t{err}\n")
            print(f"[manual] 자동 처리 실패 {len(skipped)}건 → {manifest}")
            print("[manual] 조치: 위 .hwp 등을 한컴오피스로 PDF 저장 → 같은 폴더 투입 → "
                  "재실행 `ingest_notice_docs.py --dir <dir> --skip-existing`")
        except Exception as e:  # noqa: BLE001
            print(f"[manual] manifest 기록 실패: {e}", file=sys.stderr)

    if args.dry_run:
        print("[dry-run] 임베딩·색인 생략")
        return 0

    from client.rag import embed_text, get_collection  # GPU
    col = get_collection(args.collection)

    if args.skip_existing:
        try:
            existing = {m.get("source_file") for m in col.get(include=["metadatas"])["metadatas"]}
        except Exception:  # noqa: BLE001
            existing = set()
        before = len(records)
        records = [(m, c) for (m, c) in records if m["source_file"] not in existing]
        total = sum(len(c) for _, c in records)
        print(f"[skip-existing] {before - len(records)}개 기존 색인 건너뜀 → 신규 {len(records)}문서/{total}청크")
        if not records:
            print("[skip-existing] 신규 없음 — 종료")
            return 0

    for meta, _ in records:
        try:
            col.delete(where={"source_file": meta["source_file"]})  # 멱등
        except Exception as e:  # noqa: BLE001
            print(f"[warn] delete {meta['source_file']}: {e}", file=sys.stderr)

    ok = 0
    done = 0
    for meta, chunks in records:
        for seq, ch in enumerate(chunks):
            try:
                emb = embed_text(ch, prefix="search_document")
                m = dict(meta); m["chunk_seq"] = seq
                col.upsert(ids=[_cid(meta, seq)], embeddings=[emb],
                           documents=[ch], metadatas=[m])
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"[warn] upsert {meta['source_file']}#{seq}: {e}", file=sys.stderr)
            done += 1
            if done % 100 == 0:
                print(f"[index] {done}/{total} ...")
    print(f"[index] 완료 ok={ok}/{total} collection={args.collection} count={col.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
