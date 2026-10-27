"""P6-1 Phase 0 — 판정 확률 추출 회귀. 순수 단위(외부 의존 0·모델 호출 0).

고정하는 것은 **2026-09-22 GPU 실측에서 나온 함정들**이다:
  - 대소문자 변종이 확률을 쪼갠다(`false` 0.951 + `False` 0.049)
  - 제약 디코딩에서 선택 토큰 자신의 logprob은 못 쓴다 → `top_logprobs`를 읽는다
  - 라벨 미검출(미측정)과 라벨 결정적 패배(0.0)는 **다른 사실**이다
"""

import math

import pytest

from core.decision_prob import BOOL_LABELS, extract_label_prob, prob_of


def _pos(*pairs):
    """(토큰, 확률) → logprobs 한 위치."""
    return {"token": pairs[0][0],
            "top_logprobs": [{"token": t, "logprob": math.log(p)} for t, p in pairs]}


def test_대소문자_변종을_합산한다():
    """실측 형상: false=0.9508 · False=0.0492 — 합산 안 하면 confidence 과소평가."""
    lg = [_pos(("false", 0.9508), ("False", 0.0492))]
    assert extract_label_prob(lg) == {"false": pytest.approx(1.0)}


def test_true_false_분포를_재정규화한다():
    lg = [_pos(("true", 0.30), ("false", 0.60))]
    d = extract_label_prob(lg)
    assert d["true"] == pytest.approx(1 / 3)
    assert d["false"] == pytest.approx(2 / 3)
    assert sum(d.values()) == pytest.approx(1.0)


def test_판정위치를_건너뛰고_값_토큰에서_잡는다():
    """JSON 객체 출력에서 키 토큰(`supported`)은 라벨이 아니므로 값에서 잡혀야 한다."""
    lg = [_pos(("{\n", 0.99)), _pos((' "', 1.0)), _pos(("supported", 0.9)),
          _pos(("true", 0.8), ("false", 0.2))]
    assert extract_label_prob(lg) == {"true": pytest.approx(0.8), "false": pytest.approx(0.2)}


def test_따옴표_접두를_턴다():
    """제약 디코딩은 값 앞에 따옴표를 붙여 내보낸다(`'\"C'`)."""
    lg = [_pos(('"C', 0.9999), ('"B', 0.0001))]
    d = extract_label_prob(lg, {"C": ["C"], "B": ["B"]})
    assert d["C"] == pytest.approx(0.9999, rel=1e-3)


def test_logprobs_부재는_None이다():
    """엔진이 안 실어 보내거나 미지원 — 0.5로 채우지 않는다."""
    assert extract_label_prob(None) is None
    assert extract_label_prob([]) is None
    assert prob_of(None) is None


def test_라벨_미검출은_None이다():
    """판정 위치 자체를 못 찾으면 미측정이다."""
    lg = [_pos(("가나다", 0.9), ("라마바", 0.1))]
    assert extract_label_prob(lg) is None
    assert prob_of(lg, "true") is None


def test_확률합_0은_None이다():
    lg = [{"token": "x", "top_logprobs": [{"token": "true", "logprob": -1e9}]}]
    assert extract_label_prob(lg) is None


def test_깨진_항목은_건너뛴다():
    """logprob 키 부재·비수치는 그 후보만 버린다(예외 없이)."""
    lg = [{"token": "true", "top_logprobs": [
        {"token": "true", "logprob": math.log(0.7)},
        {"token": "false"},                      # logprob 없음
        {"token": "False", "logprob": "nope"},   # 비수치
    ]}]
    assert extract_label_prob(lg) == {"true": pytest.approx(1.0)}


def test_결정적_패배는_0이고_미측정이_아니다():
    """🔑 top_logprobs에 true가 아예 없으면 P(true)=0.0 — None이면 **가장 확신한 건들이
    축에서 통째로 빠져** 분포가 뒤집힌다(실측: 확신한 false 판정에 true가 없었다)."""
    lg = [_pos(("false", 0.95), ("False", 0.05))]
    assert prob_of(lg, "true") == 0.0
    assert prob_of(lg, "false") == pytest.approx(1.0)


def test_기본_라벨집합은_bool이다():
    assert set(BOOL_LABELS) == {"true", "false"}
