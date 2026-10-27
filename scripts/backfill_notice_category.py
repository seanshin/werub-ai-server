#!/usr/bin/env python3
"""기존 khidi_notice 청크에 category 메타 백필 (medical-law-rag O5).

col.update(ids, metadatas)로 메타만 갱신(재임베딩 없음). GPU 전용.
신규 색인은 notice_metadata가 자동 부여하므로 1회성 백필.
  venv/bin/python scripts/backfill_notice_category.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.doc_parser import classify_notice_category  # noqa: E402
from core.medlaw_processor import MEDLAW_COLLECTION  # noqa: E402


def main() -> int:
    from collections import Counter
    from client.rag import get_collection
    col = get_collection(MEDLAW_COLLECTION)
    g = col.get(include=["metadatas"])
    ids, metas = g["ids"], g["metadatas"]
    upd_ids, upd_metas = [], []
    for i, m in zip(ids, metas):
        if m.get("origin") != "khidi_notice":
            continue
        cat = classify_notice_category(m.get("law_name", ""))
        if m.get("category") != cat:
            m2 = dict(m)
            m2["category"] = cat
            upd_ids.append(i)
            upd_metas.append(m2)
    print(f"[backfill] 갱신 대상 {len(upd_ids)} 청크")
    B = 500
    for j in range(0, len(upd_ids), B):
        col.update(ids=upd_ids[j:j + B], metadatas=upd_metas[j:j + B])
        print(f"  {min(j + B, len(upd_ids))}/{len(upd_ids)}")
    # 분포 확인
    g2 = col.get(include=["metadatas"])
    dist = Counter(m.get("category") for m in g2["metadatas"] if m.get("origin") == "khidi_notice")
    print("[backfill] khidi category 분포:", dict(dist))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
