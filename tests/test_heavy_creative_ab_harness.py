"""G1 A/B 하네스 순수 로직 회귀 — 측정 전에 **하네스를 먼저 검증한다**.

이 레포는 하네스 결함으로 두 번 데었다: 커밋본과 실행본이 갈려 재현 불가(v2.125.6) ·
하네스가 `success:false`를 안 봐 LLM 실패를 "결과 누락"으로 위장(v2.125.8 §6).
그래서 GPU 없이 검증 가능한 부분(부호검정·결정적 축·표본 무결성)을 여기서 고정한다.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import eval_heavy_creative_ab as H


# ── 표본 무결성(사전 등록 §1) ──────────────────────────────────────────────
def test_sample_is_locked_at_40_with_registered_mix():
    qs = H._load_questions()
    assert len(qs) == 40
    kinds = {}
    for q in qs:
        kinds[q["kind"]] = kinds.get(q["kind"], 0) + 1
    assert kinds == {"creative_short": 8, "creative_continue": 8, "creative_style": 8,
                     "long_summary": 6, "long_structure": 6, "long_multistep": 4}


def test_every_question_has_deterministic_axis():
    """judge가 죽어도 결정적 축은 남아야 한다 — 신호·길이 범위가 비면 그 문항은 판정 불가."""
    for q in H._load_questions():
        assert q["signals_all"], q["id"]
        assert q["min_len"] < q["max_len"], q["id"]


def test_both_arms_get_identical_options():
    """대칭성: 양팔이 같은 options를 쓴다(핀 값도 명시 — 엔진 기본값 의존 제거)."""
    assert H.OPTIONS["seed"] == 42
    assert H.OPTIONS["repeat_penalty"] == 1.1


# ── 부호검정 ───────────────────────────────────────────────────────────────
def test_sign_test_counts_and_symmetry():
    b, a, tie, p = H._sign_test([(1, 2), (2, 1), (3, 3), (1, 2)])
    assert (b, a, tie) == (2, 1, 1)
    b2, a2, _, p2 = H._sign_test([(2, 1), (1, 2), (3, 3), (2, 1)])
    assert (b2, a2) == (1, 2) and abs(p - p2) < 1e-9      # 대칭


def test_sign_test_all_one_side_is_significant():
    _, _, _, p = H._sign_test([(1, 2)] * 10)
    assert p < 0.01


def test_sign_test_ignores_none_and_empty():
    b, a, tie, p = H._sign_test([(None, 2), (1, None), (1, 1)])
    assert (b, a, tie) == (0, 0, 1) and p == 1.0
    assert H._sign_test([])[3] == 1.0


# ── 결정적 축 ──────────────────────────────────────────────────────────────
def _q(**kw):
    d = {"id": "t", "kind": "k", "prompt": "p", "signals_all": ["해무리"],
         "min_len": 10, "max_len": 100}
    d.update(kw)
    return d


def test_deterministic_signal_and_length():
    ok = H._deterministic(_q(), "해무리는 등대를 지켰다. 그날 밤 파도가 높았다.")
    assert ok["signals_ok"] and ok["length_ok"] and not ok["empty"]
    miss = H._deterministic(_q(), "등대지기는 밤새 불을 지켰고 파도는 높았다.")
    assert not miss["signals_ok"]                     # 신호 누락 = 지시 미준수
    short = H._deterministic(_q(), "해무리.")
    assert short["signals_ok"] and not short["length_ok"]


def test_deterministic_all_signals_required():
    q = _q(signals_all=["첫째", "둘째", "셋째"])
    assert not H._deterministic(q, "첫째 항목과 둘째 항목만 있다. 그리고 더 많은 문장.")["signals_ok"]
    assert H._deterministic(q, "첫째 항목. 둘째 항목. 셋째 항목까지 모두 담았다.")["signals_ok"]


def test_deterministic_empty_is_flagged():
    e = H._deterministic(_q(), "")
    assert e["empty"] and not e["signals_ok"] and e["chars"] == 0
