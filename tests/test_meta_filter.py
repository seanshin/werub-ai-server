"""메타데이터 필터 판정 — hybrid 검색의 격리 우회 봉합 회귀.

🔴 2026-07-30 Cerno 보안 제보: `mode=hybrid`에서 `filter={"owner":"AAA"}`로 검색해도
`owner=BBB` 문서가 반환됐다(라이브 100% 재현). FTS 경로에 메타데이터가 없어 필터가
적용되지 않았고, 유출분은 metadata가 비어 있었다. **메타를 모르면 통과가 아니라 배제**가
정답이다 — 격리 판정에서 "해석 못 했으니 보여준다"는 곧 유출이다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meta_filter import matches                                    # noqa: E402

AAA = {"owner": "AAA", "visibility": "private", "year": 2026}
BBB = {"owner": "BBB", "visibility": "private", "year": 2024}


def test_no_filter_passes_everything():
    assert matches(AAA, None) and matches({}, {}) and matches(None, None)


def test_simple_equality_isolates_owner():
    """제보 시나리오 그대로 — AAA로 걸면 BBB는 안 나와야 한다."""
    assert matches(AAA, {"owner": "AAA"})
    assert not matches(BBB, {"owner": "AAA"})


def test_missing_metadata_is_excluded_not_passed():
    """FTS-only 히트처럼 메타가 비면 **배제**(fail-closed). 이것이 유출 경로였다."""
    assert not matches({}, {"owner": "AAA"})
    assert not matches(None, {"owner": "AAA"})
    assert not matches({"title": "제목만"}, {"owner": "AAA"})


def test_nonexistent_owner_returns_nothing():
    """존재하지 않는 owner로 검색하면 전건이 아니라 0건이어야 한다."""
    for meta in (AAA, BBB, {}, {"owner": ""}):
        assert not matches(meta, {"owner": "NOBODY"})


def test_operators():
    assert matches(AAA, {"owner": {"$eq": "AAA"}})
    assert matches(AAA, {"owner": {"$ne": "BBB"}})
    assert matches(AAA, {"owner": {"$in": ["AAA", "CCC"]}})
    assert not matches(AAA, {"owner": {"$in": ["BBB"]}})
    assert matches(AAA, {"owner": {"$nin": ["BBB"]}})
    assert matches(AAA, {"year": {"$gte": 2026}})
    assert not matches(BBB, {"year": {"$gte": 2026}})


def test_multiple_keys_are_and():
    assert matches(AAA, {"owner": "AAA", "visibility": "private"})
    assert not matches(AAA, {"owner": "AAA", "visibility": "org"})


def test_logical_operators():
    assert matches(AAA, {"$or": [{"owner": "AAA"}, {"owner": "ZZZ"}]})
    assert not matches(AAA, {"$or": [{"owner": "YYY"}, {"owner": "ZZZ"}]})
    assert matches(AAA, {"$and": [{"owner": "AAA"}, {"year": {"$gte": 2000}}]})
    assert not matches(AAA, {"$and": [{"owner": "AAA"}, {"year": {"$lt": 2000}}]})
    assert not matches(AAA, {"$or": []})            # 빈 조건은 fail-closed


def test_unknown_operator_is_fail_closed():
    """미지원 연산자를 '통과'로 처리하면 필터를 우회하는 새 구멍이 된다."""
    assert not matches(AAA, {"owner": {"$regex": "A.*"}})
    assert not matches(AAA, {"$nor": [{"owner": "BBB"}]})


def test_type_mismatch_comparison_is_false():
    assert not matches({"year": "2026"}, {"year": {"$gt": 2000}})


def test_non_dict_where_is_rejected():
    assert not matches(AAA, ["owner", "AAA"])
