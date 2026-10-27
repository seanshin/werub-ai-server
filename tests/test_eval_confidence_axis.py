"""P6-1 Phase 0 — `/api/eval/status` 판정 확신 축 회귀. 순수 함수만(HTTP·DB 0).

고정하는 것:
  - 미측정(구 배치·확률 미지원)과 "0건"을 **가른다**
  - 평균은 **주장 수 가중** — 주장 1개 문항이 20개 문항과 같은 무게를 갖지 않는다
  - `low`(망설인 주장)는 합계 · `missing`은 분모 밖이 몇 건인지 보인다
"""

import pytest

from routes.activity import _confidence_summary, _entail_prob


# --- detail JSON 판독 -----------------------------------------------------------

def test_키가_없으면_미측정():
    """구 배치는 이 키 자체가 없다."""
    assert _entail_prob('{"claims": []}') is None


def test_값이_null이면_미측정():
    """확률 미지원 백엔드에서 돌린 배치."""
    assert _entail_prob('{"entail_prob": null}') is None


def test_dict가_아니면_미측정():
    assert _entail_prob('{"entail_prob": 0.7}') is None


def test_깨진_JSON은_미측정():
    assert _entail_prob("{[") is None
    assert _entail_prob("") is None
    assert _entail_prob(None) is None


def test_정상값을_그대로_준다():
    v = _entail_prob('{"entail_prob": {"n": 2, "mean": 0.7, "low": 1, "missing": 0}}')
    assert v == {"n": 2, "mean": 0.7, "low": 1, "missing": 0}


# --- 배치 집계 ------------------------------------------------------------------

def _q(n, mean, low=0, missing=0):
    return {"entail_prob": {"n": n, "mean": mean, "low": low, "missing": missing}}


def test_축이_없으면_None():
    """미측정 배치 — 0.0으로 채우면 축이 '나빠 보인다'."""
    assert _confidence_summary([{"entail_prob": None}, {}]) is None
    assert _confidence_summary([]) is None
    assert _confidence_summary(None) is None


def test_평균은_주장수_가중이다():
    """🔑 단순 평균이면 (0.9+0.5)/2=0.70. 가중이면 (0.9*1+0.5*9)/10=0.54."""
    out = _confidence_summary([_q(1, 0.9), _q(9, 0.5)])
    assert out["n"] == 10
    assert out["mean"] == pytest.approx(0.54)


def test_망설인_주장은_합계다():
    out = _confidence_summary([_q(5, 0.8, low=2), _q(5, 0.6, low=3)])
    assert out["low"] == 5


def test_결측은_따로_센다():
    """분모 밖이 몇 건인지 안 보이면 평균을 믿을 수 없다."""
    out = _confidence_summary([_q(3, 0.8, missing=2), _q(1, 0.4, missing=1)])
    assert out["n"] == 4 and out["missing"] == 3


def test_n이_0인_문항은_평균에서_빠지되_결측은_센다():
    out = _confidence_summary([_q(2, 0.9), {"entail_prob": {"n": 0, "mean": None,
                                                            "low": 0, "missing": 4}}])
    assert out["n"] == 2 and out["questions"] == 1 and out["missing"] == 4


def test_일부만_축을_가져도_집계된다():
    """축 도입 직후·재실행 혼재 배치."""
    out = _confidence_summary([_q(2, 0.8), {"entail_prob": None}, {}])
    assert out["questions"] == 1 and out["n"] == 2
