"""P6-1 Phase 0 — entail 확률 배선 회귀. 순수 단위(모델 호출 0 · 라우터 스텁).

고정하는 것:
  1. **기존 계약 불변** — `_qwen_entail_check`는 여전히 bool, `minicheck_verify`는
     기본값에서 리스트를 돌려준다(회귀 4건과 `checker` 프로토콜 호출자 보호).
  2. 확률의 청크 집계 규약(max-over-chunks 판정과 정합).
  3. 확률 축이 죽어도 **판정은 그대로 난다**(fail-open).
"""

import math

import pytest

import core.faithfulness as F


def _lg(p_true):
    """P(supported)=p_true인 logprobs 한 위치."""
    return [{"token": "x", "top_logprobs": [
        {"token": "true", "logprob": math.log(max(p_true, 1e-12))},
        {"token": "false", "logprob": math.log(max(1 - p_true, 1e-12))}]}]


def _stub_route(monkeypatch, supported, p_true=None, expect_logprobs=True):
    seen = {}

    def fake(task, prompt, *, model=None, system=None, schema=None, options=None,
             timeout=180, logprobs=False, top_logprobs=None):
        seen["logprobs"] = logprobs
        seen["top_logprobs"] = top_logprobs
        raw = {} if p_true is None else {"logprobs": _lg(p_true)}
        return F_Result(text='{"supported": %s}' % ("true" if supported else "false"), raw=raw)

    monkeypatch.setattr(F, "_route_full", fake)
    return seen


class F_Result:
    def __init__(self, text, raw):
        self.text, self.raw, self.ok = text, raw, True


def test_기존_함수는_여전히_bool을_준다(monkeypatch):
    _stub_route(monkeypatch, supported=True, p_true=0.9)
    assert F._qwen_entail_check("근거", "주장") is True


def test_ex는_판정과_확률을_준다(monkeypatch):
    _stub_route(monkeypatch, supported=True, p_true=0.87)
    ok, p = F._qwen_entail_check_ex("근거", "주장")
    assert ok is True and p == pytest.approx(0.87, rel=1e-6)


def test_logprobs를_실제로_요청한다(monkeypatch):
    seen = _stub_route(monkeypatch, supported=False, p_true=0.1)
    F._qwen_entail_check_ex("근거", "주장", top_logprobs=7)
    assert seen["logprobs"] is True and seen["top_logprobs"] == 7


def test_확률_미검출이어도_판정은_난다(monkeypatch):
    """🔑 fail-open — 확률 축이 죽어도 충실도 측정은 계속 돈다."""
    _stub_route(monkeypatch, supported=False, p_true=None)
    ok, p = F._qwen_entail_check_ex("근거", "주장")
    assert ok is False and p is None


def test_비정형_응답은_여전히_예외다(monkeypatch):
    monkeypatch.setattr(F, "_route_full",
                        lambda *a, **k: F_Result(text='{"x": 1}', raw={}))
    with pytest.raises(ValueError):
        F._qwen_entail_check_ex("근거", "주장")


# --- minicheck_verify 집계 규약 -------------------------------------------------

def _chunked(monkeypatch, n_chunks=3):
    monkeypatch.setattr(F, "_split_evidence", lambda ev, **k: ["c%d" % i for i in range(n_chunks)])


def test_기본값은_리스트를_그대로_준다(monkeypatch):
    """계약 불변 — `want_prob` 미지정이면 반환 형태가 종전과 같다."""
    _chunked(monkeypatch)
    out = F.minicheck_verify(["주장"], "근거", checker=lambda ch, c, model=None: True)
    assert out == [True]


def test_지지판정은_그_청크의_확률을_쓴다(monkeypatch):
    """max-over-chunks는 **첫 지지에서 break**하므로 확률도 그 지점 값이다."""
    _chunked(monkeypatch)
    seq = iter([(False, 0.2), (True, 0.81), (False, 0.99)])
    monkeypatch.setattr(F, "_qwen_entail_check_ex", lambda ch, c, model=None: next(seq))
    monkeypatch.setattr(F, "VERIFIER_BACKEND", "qwen-entail")
    verdicts, probs = F.minicheck_verify(["주장"], "근거", want_prob=True)
    assert verdicts == [True] and probs == [pytest.approx(0.81)]


def test_부정판정은_최댓값을_쓴다(monkeypatch):
    """🔑 *확신한 부정*과 *간신히 부정*을 가르는 값 — 이 축의 실사용 지점."""
    _chunked(monkeypatch)
    seq = iter([(False, 0.05), (False, 0.47), (False, 0.11)])
    monkeypatch.setattr(F, "_qwen_entail_check_ex", lambda ch, c, model=None: next(seq))
    monkeypatch.setattr(F, "VERIFIER_BACKEND", "qwen-entail")
    verdicts, probs = F.minicheck_verify(["주장"], "근거", want_prob=True)
    assert verdicts == [False] and probs == [pytest.approx(0.47)]


def test_판독불능은_확률도_None(monkeypatch):
    _chunked(monkeypatch)

    def boom(ch, c, model=None):
        raise RuntimeError("판독 불능")

    monkeypatch.setattr(F, "_qwen_entail_check_ex", boom)
    monkeypatch.setattr(F, "VERIFIER_BACKEND", "qwen-entail")
    verdicts, probs = F.minicheck_verify(["주장"], "근거", want_prob=True)
    assert verdicts == [None] and probs == [None]


def test_커스텀checker면_확률은_null이고_판정은_정상(monkeypatch):
    """확률 미지원 백엔드를 **거절하지 않는다** — 축만 null."""
    _chunked(monkeypatch)
    verdicts, probs = F.minicheck_verify(["주장"], "근거", want_prob=True,
                                         checker=lambda ch, c, model=None: True)
    assert verdicts == [True] and probs == [None]


# --- cross_check 병합 · 집계 ----------------------------------------------------

def _ex_seq(monkeypatch, seq):
    it = iter(seq)
    _chunked(monkeypatch, 1)
    monkeypatch.setattr(F, "_qwen_entail_check_ex", lambda ch, c, model=None: next(it))
    monkeypatch.setattr(F, "VERIFIER_BACKEND", "qwen-entail")


def test_cross_check가_주장별_확률을_싣는다(monkeypatch):
    _ex_seq(monkeypatch, [(True, 0.92), (False, 0.48)])
    r = F.cross_check({"claims": [{"claim": "a"}, {"claim": "b"}]}, "근거")
    assert [c["entail_prob"] for c in r["claims"]] == [0.92, 0.48]


def test_기존_교차검증_축은_그대로다(monkeypatch):
    """주 KPI·mc_* 집계는 확률 축 추가로 흔들리지 않는다."""
    _ex_seq(monkeypatch, [(True, 0.9), (False, 0.1)])
    r = F.cross_check({"claims": [{"claim": "a", "verdict": "supported"},
                                  {"claim": "b", "verdict": "supported"}]}, "근거")
    assert r["mc_checked"] == 2 and r["mc_conflicts"] == 1 and r["mc_agree"] == 0.5


def test_확률_결측은_분모에서_빠진다(monkeypatch):
    """🔑 0으로 세면 평균이 내려가 축이 '나빠 보인다' — 미측정은 분모 밖."""
    _ex_seq(monkeypatch, [(True, 0.8), (False, None)])
    r = F.cross_check({"claims": [{"claim": "a"}, {"claim": "b"}]}, "근거")
    assert r["entail_prob_n"] == 1
    assert r["entail_prob_mean"] == 0.8
    assert r["entail_prob_missing"] == 1
    assert r["claims"][1]["entail_prob"] is None


def test_망설인_건을_센다(monkeypatch):
    """0.35~0.65 = 판정자가 망설인 구간. 원문을 열어볼 대상 지목이 이 축의 용도."""
    _ex_seq(monkeypatch, [(False, 0.47), (True, 0.99), (False, 0.02)])
    r = F.cross_check({"claims": [{"claim": "a"}, {"claim": "b"}, {"claim": "c"}]}, "근거")
    assert r["entail_prob_low"] == 1


def test_want_prob_False면_확률_키가_없다(monkeypatch):
    """종전 동작 그대로 — 축을 끌 수 있어야 롤백이 한 줄이다."""
    _chunked(monkeypatch, 1)
    r = F.cross_check({"claims": [{"claim": "a"}]}, "근거",
                      checker=lambda ch, c, model=None: True, want_prob=False)
    assert "entail_prob_n" not in r
    assert "entail_prob" not in r["claims"][0]


def test_빈_주장이면_집계가_비어도_예외없다(monkeypatch):
    _ex_seq(monkeypatch, [])
    r = F.cross_check({"claims": []}, "근거")
    assert r["entail_prob_n"] == 0 and r["entail_prob_mean"] is None
