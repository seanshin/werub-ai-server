"""메타데이터 필터 판정 — ChromaDB `where` 스타일을 우리 쪽에서도 평가한다.

🔴 2026-07-30 Cerno 보안 제보: `/api/rag/search`의 `mode=hybrid`가 `where` 필터를 적용하지
않아 **타 소유자 문서가 반환**됐다(라이브 100% 재현). 원인은 하이브리드가 벡터(Chroma·필터
적용)와 키워드(FTS5·필터 없음) 결과를 합치는데, **FTS 경로에는 메타데이터가 없어** 필터가
적용될 수 없었던 것. 그래서 병합 뒤 **우리가 직접 판정**할 도구가 필요하다.

Chroma의 where 문법 중 실제로 쓰는 부분만 구현한다:
  {"owner": "A"} · {"owner": {"$eq": "A"}} · {"$ne"} · {"$in"} · {"$nin"} ·
  {"$gt"/"$gte"/"$lt"/"$lte"} · {"$and": [...]} · {"$or": [...]}

**모르는 연산자는 통과가 아니라 불일치로 본다**(fail-closed) — 격리 판정에서 "해석 못 했으니
일단 보여준다"는 곧 유출이다.
"""

_LOGICAL = ("$and", "$or")


def _cmp(op, actual, expected):
    if op == "$eq":
        return actual == expected
    if op == "$ne":
        return actual != expected
    if op == "$in":
        return isinstance(expected, (list, tuple)) and actual in expected
    if op == "$nin":
        return isinstance(expected, (list, tuple)) and actual not in expected
    if op in ("$gt", "$gte", "$lt", "$lte"):
        try:
            if op == "$gt":
                return actual > expected
            if op == "$gte":
                return actual >= expected
            if op == "$lt":
                return actual < expected
            return actual <= expected
        except TypeError:                       # 타입 불일치 비교는 불일치로
            return False
    return False                                # 미지원 연산자 = fail-closed


def matches(metadata, where) -> bool:
    """메타데이터가 where 조건을 만족하는가. where가 비면 항상 True(필터 없음).

    metadata가 None/빈 dict인데 조건이 있으면 **False**다 — 메타를 모르는 문서를
    통과시키면 격리가 뚫린다(이번 사고의 유출 경로가 정확히 이것이었다).
    """
    if not where:
        return True
    if not isinstance(where, dict):
        return False
    meta = metadata if isinstance(metadata, dict) else {}

    for key, cond in where.items():
        if key in _LOGICAL:
            if not isinstance(cond, (list, tuple)) or not cond:
                return False
            results = [matches(meta, c) for c in cond]
            if key == "$and" and not all(results):
                return False
            if key == "$or" and not any(results):
                return False
            continue
        if key.startswith("$"):                 # 알 수 없는 최상위 연산자
            return False
        if isinstance(cond, dict):
            if not cond:
                return False
            for op, expected in cond.items():
                if not _cmp(op, meta.get(key), expected):
                    return False
        else:
            if meta.get(key) != cond:
                return False
    return True
