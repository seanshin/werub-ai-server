"""core.eval_pool 테스트 — 결정적 회전·core 고정·풀 커버리지·폴백."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.eval_pool import load_pool, select_questions, questions_for_day  # noqa: E402

CORE = ["c1", "c2"]
POOL = [f"p{i}" for i in range(7)]


def test_core_always_included_and_deterministic():
    a = select_questions(CORE, POOL, 3, day_ordinal=100)
    b = select_questions(CORE, POOL, 3, day_ordinal=100)
    assert a == b                                   # 같은 날=같은 세트(러너 2종 정합)
    assert a[:2] == CORE and len(a) == 5


def test_rotation_covers_entire_pool():
    seen = set()
    for day in range(10):                           # ceil(7/3)=3일 주기 — 10일이면 전부
        for q in select_questions(CORE, POOL, 3, day):
            seen.add(q)
    assert seen >= set(POOL)                        # 풀 전체 커버


def test_different_days_rotate():
    d1 = select_questions(CORE, POOL, 3, 0)[2:]
    d2 = select_questions(CORE, POOL, 3, 1)[2:]
    assert d1 != d2                                 # 일자별 다른 구간


def test_k_edge_cases():
    assert select_questions(CORE, POOL, 0, 5) == CORE            # k=0 → core만
    assert select_questions(CORE, [], 3, 5) == CORE              # 빈 풀
    big = select_questions(CORE, POOL, 99, 5)
    assert len(big) == len(CORE) + len(POOL)                     # k>n 클램프


def test_dedup_preserves_order():
    out = select_questions(["c1", "p0"], POOL, 3, 0)             # core에 풀 문항 중복
    assert out.count("p0") == 1 and out[0] == "c1"


def test_load_pool_file_and_fallback(tmp_path):
    f = tmp_path / "q.json"
    f.write_text(json.dumps({"core": ["a"], "pool": ["b", " ", 3], "rotate_k": 2}),
                 encoding="utf-8")
    p = load_pool(str(f))
    assert p == {"core": ["a"], "pool": ["b"], "rotate_k": 2}    # 공백/비문자 필터
    fb = load_pool(str(tmp_path / "missing.json"))
    assert len(fb["core"]) == 5 and fb["rotate_k"] == 0          # 폴백=기존 core 5


def test_questions_for_day_real_file():
    qs = questions_for_day(739000)                               # 실제 config 파일
    assert len(qs) >= 5 and "진료기록부 보존기간은?" in qs        # core 포함
