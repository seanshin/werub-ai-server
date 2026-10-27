#!/usr/bin/env python3
"""HIRA/복지부 발간물(편람·심사기준·분류체계 등) → medical_pub_knowledge RAG 색인.

상대가치점수책(다컬럼)은 ingest_hira_valuepoint.py가 처리. 이 스크립트는 단일컬럼 본문
중심의 발간물(약제 적용기준·청구 작성요령·치료재료 가이드맵·실무편람·분류체계·법령집 등)을
doc_parser(parse_document+chunk_notice) 경로로 색인한다. origin='hira_pub'로 구분.

🔑 **별도 컬렉션 `medical_pub_knowledge`에 색인**(2026-06-11): 발간물 15K를 기존
medical_law_knowledge(9.6K) 위에 얹으면 컬렉션이 ~25K로 커지며 ChromaDB(Rust) HNSW가
link_lists.bin 폭주(수백 GB sparse)로 손상→디스크풀→서버다운이 재현됨([[project_incident_chromadb_disk_full]]).
**신규 별도 컬렉션은 15K도 안전 확인.** routes/medical_law.py가 MEDLAW_AUX_COLLECTIONS로
본 코퍼스와 함께 질의·병합한다(멀티컬렉션). 운영 주의: 대량 색인 시 link_lists/디스크
워치독 동반 권장(인시던트 메모리의 load_monitored.sh 패턴), 기존 큰 컬렉션엔 대량 delete 금지.

파일별 제목/분류는 MANIFEST에 명시(BZ 코드 파일명은 제목 미상이라 수작업 매핑).
멱등: source_file delete-first. GPU 전용(bge-m3+ChromaDB).

  venv/bin/python scripts/ingest_hira_pubs.py --dir /tmp/hira_batch --dry-run
  venv/bin/python scripts/ingest_hira_pubs.py --dir /tmp/hira_batch
  venv/bin/python scripts/ingest_hira_pubs.py --dir /tmp/hira_batch --only BZ202509122216350,BZ202512262718250
"""
import argparse
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.doc_parser import parse_document, chunk_notice  # noqa: E402

COLLECTION = "medical_pub_knowledge"  # 발간물 전용(본 코퍼스 medical_law_knowledge와 분리)

# (파일 stem, 제목, category, doc_type). category는 의료법 RAG 컨벤션 라벨.
MANIFEST = [
    # B군 — 핵심 심사기준
    ("BZ202509122216350", "요양급여의 적용기준 및 방법에 관한 세부사항(약제) 2025.7", "심사기준", "약제기준"),
    ("BZ202509052163191", "요양급여비용 청구방법·심사청구서·명세서 서식 및 작성요령", "심사기준", "청구작성요령"),
    ("BZ202512262718250", "치료재료 건강보험 가이드맵 2025.12", "급여기준", "치료재료"),
    ("BZ202603123071288", "질병군 급여·비급여 목록 및 급여 상대가치점수(DRG)", "급여기준", "질병군"),
    ("BZ202512192693303", "자동차보험진료수가 심사업무처리에 관한 규정 2025.12", "심사기준", "자보수가"),
    # C군 — 참고편람/지침/분류체계
    ("BZ202603163084309", "2026 의료급여 실무편람", "급여기준", "의료급여편람"),
    ("BZ202512182688938", "입원 시 상병 코딩지침 및 사례집(POA) 2025", "심사기준", "코딩지침"),
    ("BZ202603243124987", "KDRG-KM 환자분류체계 2026", "급여기준", "환자분류체계"),
    ("BZ202603243124965", "KOPG-KM 환자분류체계(외래) 2026", "급여기준", "환자분류체계"),
    ("BZ202602132964761", "국민건강보험 요양급여 민원편람", "기타", "민원편람"),
    ("BZ202605113371138", "요양기관 의약품 청구 가이드(구입약가·구입수량) 2026", "심사기준", "의약품청구"),
    ("BZ202601202839720", "국민건강보험법령집", "기타", "법령집"),
]


def _doc_id(source_file: str, seq: int) -> str:
    return "hp-" + hashlib.md5(f"{source_file}|{seq}".encode("utf-8")).hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/tmp/hira_batch", help="PDF 폴더")
    ap.add_argument("--only", help="쉼표구분 stem 목록만 처리")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--collection", default=COLLECTION)
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    items = [m for m in MANIFEST if only is None or m[0] in only]

    # ① 파싱 → 청크 집계
    parsed = []   # (stem, title, category, dtype, source_file, chunks)
    grand = 0
    for stem, title, cat, dtype in items:
        path = os.path.join(args.dir, f"{stem}.pdf")
        if not os.path.isfile(path):
            print(f"[skip] 파일 없음: {path}", file=sys.stderr)
            continue
        t = time.time()
        pd = parse_document(path)
        if not pd.ok:
            print(f"[skip] {stem} 파싱 실패: {pd.parser}/{pd.error}", file=sys.stderr)
            continue
        chunks = chunk_notice(pd.text)
        parsed.append((stem, title, cat, dtype, os.path.basename(path), chunks))
        grand += len(chunks)
        print(f"[parse] {stem} ({pd.page_count}p, {time.time()-t:.0f}s) "
              f"chunks={len(chunks)} cat={cat} :: {title[:40]}")
    print(f"[parse] {len(parsed)}개 문서 / 총 {grand} 청크")

    if args.dry_run:
        print("[dry-run] 임베딩·색인 생략")
        return 0

    from client.rag import embed_text, get_collection
    col = get_collection(args.collection)
    ok = done = 0
    for stem, title, cat, dtype, sf, chunks in parsed:
        try:
            col.delete(where={"source_file": sf})  # 멱등
        except Exception as e:  # noqa: BLE001
            print(f"[warn] delete {sf}: {e}", file=sys.stderr)
        for seq, ch in enumerate(chunks):
            meta = {
                "law_name": title, "law_level": "발간물", "category": cat,
                "source": "HIRA", "source_file": sf, "origin": "hira_pub",
                "doc_type": dtype, "chunk_seq": seq,
            }
            try:
                emb = embed_text(ch, prefix="search_document")
                col.upsert(ids=[_doc_id(sf, seq)], embeddings=[emb],
                           documents=[ch], metadatas=[meta])
                ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"[warn] upsert {sf}#{seq}: {e}", file=sys.stderr)
            done += 1
            if done % 500 == 0:
                print(f"[index] {done}/{grand} ... (ok={ok})")
        print(f"[index] {stem} 완료 ({len(chunks)} 청크)")
    print(f"[index] 전체 완료 ok={ok}/{grand} collection={args.collection} count={col.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
