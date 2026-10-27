#!/usr/bin/env python3
"""dedup 삭제 예정 청크를 **임베딩 포함** 복구본으로 내보낸다.

`scripts/dedup_medlaw.py --apply` 전에 반드시 실행할 것 — ChromaDB 삭제는 되돌릴 수
없으므로 ids·documents·metadatas·embeddings까지 떠 두면 재색인(GPU 재연산) 없이
`collection.add(...)`로 복원할 수 있다(절차 정본 `docs/OPS_medlaw_dedup_20260831.md` §4).

  venv/bin/python scripts/export_medlaw_dedup_plan.py /path/restore.json
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.medlaw_processor import _doc_identity, _tid, MEDLAW_COLLECTION  # noqa: E402


def plan_files() -> list:
    """dedup_versions와 같은 규칙으로 삭제 후보 source_file 목록을 만든다."""
    from client.rag import get_collection
    col = get_collection(MEDLAW_COLLECTION)
    sf_count: Counter = Counter()
    for m in col.get(include=["metadatas"])["metadatas"]:
        if m.get("origin") == "khidi_notice":
            sf_count[m["source_file"]] += 1
    groups: dict = defaultdict(list)
    for sf, n in sf_count.items():
        groups[_doc_identity(sf)].append((sf, n))
    plan = []
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: _tid(x[0]), reverse=True)
        keep_chunks = members[0][1]
        plan += [sf for sf, n in members[1:] if n == keep_chunks]
    return plan


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out_path = sys.argv[1]
    from client.rag import get_collection
    col = get_collection(MEDLAW_COLLECTION)
    files = []
    total = 0
    for sf in plan_files():
        got = col.get(where={"source_file": sf},
                      include=["documents", "metadatas", "embeddings"])
        n = len(got["ids"])
        total += n
        files.append({"source_file": sf, "chunks": n, "ids": got["ids"],
                      "documents": got["documents"], "metadatas": got["metadatas"],
                      "embeddings": [list(map(float, e)) for e in got["embeddings"]]})
        print(f"export {sf}: {n}청크")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"collection": MEDLAW_COLLECTION, "files": files}, f, ensure_ascii=False)
    print(f"[export] {len(files)}파일 / {total}청크 → {out_path} "
          f"({os.path.getsize(out_path) / 1e6:.1f}MB, 임베딩 포함)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
