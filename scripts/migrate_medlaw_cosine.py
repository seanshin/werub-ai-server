"""medical_law_knowledge 컬렉션 거리 공간 마이그레이션 (L2 → cosine).

배경(2026-07-16 실측): 본 법령 컬렉션만 hnsw:space 미지정(ChromaDB 기본 L2)으로
생성돼 있었다. bge-m3 임베딩은 정규화(norm=1)라 L2² = 2−2cos → score = 1−dist =
2cos−1 로 압축된다(cos 0.55 → 0.10, cos 0.665는 돼야 게이트 0.33 통과). 반면
발간물(medical_pub_knowledge) 등 다른 컬렉션은 전부 cosine이라, 법령 근거가
구조적으로 발간물에 밀리는 권위역전·게이트 전멸(eval id197/id199 ctx_precision
0.0)·근거 오염(id95)·조문 창작의 공통 뿌리였다. 랭킹은 단조라 순서는 불변,
절대 점수만 왜곡 — 게이트·floor·컬렉션 간 병합이 전부 절대 점수 기반이라 실해.

동작: 저장 임베딩 재사용(재임베딩 0회) — 전량 export 백업(npz+jsonl) → 무결성
검증 → delete_collection(전체 드롭, col.delete 대량삭제의 link_lists 폭주와 무관)
→ cosine으로 재생성 → 배치 add → 건수 검증. 실측 12,686청크 ~10s.

사용: cd <app root> && venv/bin/python scripts/migrate_medlaw_cosine.py
"""
import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

from client.rag import get_chroma  # noqa: E402

COLLECTION = "medical_law_knowledge"
BACKUP_DIR = os.path.expanduser("~/backups")


def main():
    c = get_chroma()
    col = c.get_collection(COLLECTION)
    space = (col.metadata or {}).get("hnsw:space")
    n = col.count()
    print(f"{COLLECTION}: count={n}, space={space or 'l2(default)'}")
    if space == "cosine":
        print("이미 cosine — 마이그레이션 불요.")
        return 0

    # 1) 전량 export
    ids, embs, docs, metas = [], [], [], []
    for off in range(0, n, 2000):
        r = col.get(include=["embeddings", "documents", "metadatas"],
                    limit=2000, offset=off)
        ids += r["ids"]
        embs += [list(e) for e in r["embeddings"]]
        docs += r["documents"]
        metas += r["metadatas"]
    assert len(ids) == n, (len(ids), n)

    # 2) 백업 + 무결성 검증
    os.makedirs(BACKUP_DIR, exist_ok=True)
    base = os.path.join(BACKUP_DIR, f"{COLLECTION}_{date.today():%Y%m%d}")
    np.savez_compressed(base + ".npz", embeddings=np.array(embs, dtype=np.float32))
    with open(base + ".jsonl", "w", encoding="utf-8") as f:
        for i in range(n):
            f.write(json.dumps({"id": ids[i], "document": docs[i],
                                "metadata": metas[i]}, ensure_ascii=False) + "\n")
    arr = np.load(base + ".npz")["embeddings"]
    lines = sum(1 for _ in open(base + ".jsonl", encoding="utf-8"))
    assert arr.shape[0] == lines == n, (arr.shape, lines, n)
    print(f"백업 검증 OK: {base}.npz/.jsonl rows={n} dim={arr.shape[1]}")

    # 3) 재생성(cosine) + 배치 add
    c.delete_collection(COLLECTION)
    new_col = c.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    t0 = time.time()
    B = 1000
    for i in range(0, n, B):
        new_col.add(ids=ids[i:i + B], embeddings=embs[i:i + B],
                    documents=docs[i:i + B],
                    metadatas=[m or {} for m in metas[i:i + B]])
    assert new_col.count() == n, new_col.count()
    print(f"MIGRATION OK: {n} chunks, space=cosine, {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
