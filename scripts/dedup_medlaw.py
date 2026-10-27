#!/usr/bin/env python3
"""의료법 RAG 코퍼스 **중복 색인** 정리 (medical-law-rag 유지보수).

⚠️ 2026-08-31 재설계 — 종전 이 스크립트는 "같은 규정 다버전 중 전문(최대청크·최신titleId)
보존, 구버전 제거"였고, 그 로직이 **최신 일부개정을 삭제**하고 있었다(실데이터 16그룹 판독:
`의료급여수가` 전문 + 2026-138호 개정이 한 그룹, `건강보험 행위 급여` 목록표 + 2025~2026
개정 8건이 한 그룹). 검색 실측(N=10·top-5)에서 삭제 대상이 근거의 32%였고 다수가 최신
개정분이었다. 자세한 경위는 `core.medlaw_processor._base_key` docstring.

지금 정리 대상은 **같은 문서가 두 번 색인된 것**뿐이다(동일 문서명 + 동일 청크 수).
구버전 전문은 정책상 **보존**한다(과거 시점 기준 질의 대비).

기본은 **dry-run**(계획만 출력·삭제 없음). 실제 삭제는 ChromaDB `col.delete` 폭주 이력
(link_lists.bin 137G 디스크풀 인시던트)이 있으므로 **워치독을 켜고** 명시 실행할 것:

  venv/bin/python scripts/dedup_medlaw.py            # 계획만 (안전)
  venv/bin/python scripts/dedup_medlaw.py --apply    # 실제 삭제
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.medlaw_processor import dedup_versions, MEDLAW_COLLECTION  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="medlaw 중복 색인 정리 (기본 dry-run)")
    ap.add_argument("--apply", action="store_true",
                    help="실제 삭제 실행 (기본은 계획만 출력). 워치독 동반 권장")
    args = ap.parse_args()

    from client.rag import get_collection
    col = get_collection(MEDLAW_COLLECTION)
    before = col.count()
    r = dedup_versions(apply=args.apply)

    print(f"[dedup] 계획: {r['planned_files']}파일 / {r['planned_chunks']}청크")
    if r.get("ambiguous_files"):
        print(f"[dedup] 보류(동명이나 청크 수 상이 — 다른 파싱본/다른 개정본 의심): "
              f"{r['ambiguous_files']}파일")
    if r.get("skipped_unsafe"):
        print(f"[dedup] ⚠️ 가드 발동 — 삭제 대상 {r['skipped_unsafe']}청크가 임계 초과라 미실행")
    if r["dry_run"]:
        print("[dedup] dry-run — 삭제하지 않았다. 실행하려면 --apply")
        return 0
    print(f"[dedup] 삭제: {r['deduped_files']}파일 / {r['deduped_chunks']}청크")
    print(f"[dedup] count {before} → {col.count()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
