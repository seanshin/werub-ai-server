"""일괄 판정 오염 봉합 회귀 (2026-08-04, 폐루프 18).

일괄(번호) 판정이 근거와 문자 그대로 일치하는 주장을 contradicted로 떨어뜨렸다
(실측 3/3 결정적, 개별 판정은 supported 5/5). 비지지 판정만 개별 재판정한다.
여기 고정하는 것: 재판정이 **실오류를 지우지 않고**, 위양성만 되돌리며, 실패해도 안전하다.
"""
from core.faithfulness import judge_claims

EV = "제21조① 정당한 사유가 없으면 이를 거부하여서는 아니 된다."


def _asker(batch_verdicts, solo_verdicts):
    """일괄 호출과 단독 호출에 서로 다른 판정을 내는 mock judge.

    첫 호출=일괄, 이후=개별 재판정(구현이 재판정을 단독 호출로 하기 때문).
    프롬프트 내용으로 구분하면 주장이 1개일 때 일괄과 단독이 같아 구분되지 않는다.
    """
    calls = {"batch": 0, "solo": 0}

    def ask(system, prompt, schema, model=None):
        if calls["batch"] == 0:
            calls["batch"] += 1
            return {"verdicts": [{"i": i + 1, "verdict": v}
                                 for i, v in enumerate(batch_verdicts)]}
        calls["solo"] += 1
        v = solo_verdicts.pop(0) if solo_verdicts else "supported"
        return {"verdicts": [{"i": 1, "verdict": v}]}
    return ask, calls


def test_batch_false_positive_is_reverted():
    ask, calls = _asker(["supported", "contradicted"], ["supported"])
    out = judge_claims(["주장A", "주장B"], EV, ask_json=ask)
    assert [j["verdict"] for j in out] == ["supported", "supported"]
    assert calls["batch"] == 1 and calls["solo"] == 1      # 플래그된 1건만 재판정


def test_real_error_survives_reverification():
    """통제 — 진짜 오류는 개별 판정에서도 contradicted라 지워지지 않는다."""
    ask, _ = _asker(["supported", "contradicted"], ["contradicted"])
    out = judge_claims(["주장A", "주장B"], EV, ask_json=ask)
    assert [j["verdict"] for j in out] == ["supported", "contradicted"]


def test_unsupported_also_reverified():
    ask, calls = _asker(["unsupported"], ["supported"])
    out = judge_claims(["주장A", "주장B"], EV, ask_json=ask)
    # 두 번째 주장은 판정 누락 → unsupported(보수) → 재판정 대상
    assert calls["solo"] == 2


def test_single_claim_skips_reverification():
    """주장이 1개면 일괄 오염이 성립하지 않으므로 재판정하지 않는다(호출 절약)."""
    ask, calls = _asker(["contradicted"], ["supported"])
    out = judge_claims(["주장A"], EV, ask_json=ask)
    assert out[0]["verdict"] == "contradicted"
    assert calls["solo"] == 0


def test_opt_out_keeps_batch_verdict():
    ask, calls = _asker(["supported", "contradicted"], ["supported"])
    out = judge_claims(["주장A", "주장B"], EV, ask_json=ask, verify_flagged=False)
    assert out[1]["verdict"] == "contradicted"
    assert calls["solo"] == 0


def test_reverification_failure_keeps_batch_verdict():
    """재판정이 터져도 일괄 판정을 유지한다(무성 통과 금지)."""
    state = {"n": 0}

    def ask(system, prompt, schema, model=None):
        state["n"] += 1
        if state["n"] == 1:
            return {"verdicts": [{"i": 1, "verdict": "supported"},
                                 {"i": 2, "verdict": "contradicted"}]}
        raise RuntimeError("judge down")
    out = judge_claims(["주장A", "주장B"], EV, ask_json=ask)
    assert out[1]["verdict"] == "contradicted"


def test_empty_claims():
    assert judge_claims([], EV) == []
