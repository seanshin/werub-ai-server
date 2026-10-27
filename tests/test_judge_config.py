"""평가 judge 모델 고정 — 단일 소스 + 추세 단절점 (기획서 §8-3a 구현).

🔴 배경: judge가 피평가 모델과 같으면(둘 다 `qwen2.5:14b`) **모델을 바꿀 때 측정 기준도
같이 바뀐다**. 그러면 "모델이 좋아진 것"과 "judge가 달라진 것"을 분리할 수 없다.
08-06 `complete` 기각(판정이 14/40 뒤집혔는데 양방향으로 흩어짐)·08-10 품질 기준선
오귀속이 같은 계열이다.

이 파일은 ① 우선순위(config → env → 기본) ② **기본 동작 불변** ③ 단절점 판정을 고정한다.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import judge_config as jc


# ── 우선순위 ──
def test_default_is_unchanged():
    """이관은 동작을 바꾸지 않는다 — 기본값이 종전과 같아야 한다."""
    assert jc.judge_model("quality", cfg={}) == "qwen2.5:14b"
    assert jc.judge_model("faithfulness", cfg={}) == jc.DEFAULT_JUDGE


def test_config_wins_over_env(monkeypatch):
    monkeypatch.setenv("QUALITY_JUDGE_MODEL", "env-model")
    assert jc.judge_model("quality", cfg={"eval_judge_model": "cfg-model"}) == "cfg-model"


def test_per_role_wins_over_global():
    cfg = {"eval_judge_model": "global", "eval_judge_models": {"faithfulness": "faith-only"}}
    assert jc.judge_model("faithfulness", cfg=cfg) == "faith-only"
    assert jc.judge_model("quality", cfg=cfg) == "global"


def test_legacy_env_still_works(monkeypatch):
    """기존 배포·스크립트가 env로 돌고 있다 — 깨뜨리지 않는다."""
    monkeypatch.setenv("FAITH_JUDGE_MODEL", "legacy")
    assert jc.judge_model("faithfulness", cfg={}) == "legacy"


def test_unknown_role_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("QUALITY_JUDGE_MODEL", raising=False)
    assert jc.judge_model("nope", cfg={}) == jc.DEFAULT_JUDGE


# ── 고정 여부 ──
def test_pinned_only_when_config_says_so(monkeypatch):
    monkeypatch.setenv("QUALITY_JUDGE_MODEL", "env-model")
    assert jc.is_pinned(cfg={}) is False          # env·기본값만으론 '고정'이 아니다
    assert jc.is_pinned(cfg={"eval_judge_model": "x"}) is True
    assert jc.is_pinned(cfg={"eval_judge_models": {"quality": "x"}}) is True


# ── 추세 단절점 ──
def test_no_break_when_judge_is_stable():
    rows = [{"ts": "t1", "judge_model": "a"}, {"ts": "t2", "judge_model": "a"}]
    assert jc.trend_breaks(rows) == []


def test_break_is_reported_with_from_to():
    rows = [{"ts": "t1", "judge_model": "a"},
            {"ts": "t2", "judge_model": "b"},
            {"ts": "t3", "judge_model": "b"}]
    b = jc.trend_breaks(rows)
    assert len(b) == 1
    assert b[0] == {"ts": "t2", "from": "a", "to": "b"}


def test_multiple_breaks():
    rows = [{"ts": "1", "judge_model": "a"}, {"ts": "2", "judge_model": "b"},
            {"ts": "3", "judge_model": "a"}]
    assert [x["ts"] for x in jc.trend_breaks(rows)] == ["2", "3"]


def test_missing_judge_model_is_not_a_break():
    """구 배치는 judge_model이 없다 — **판정 불가**이지 단절이 아니다."""
    rows = [{"ts": "1", "judge_model": "a"}, {"ts": "2"},
            {"ts": "3", "judge_model": "a"}]
    assert jc.trend_breaks(rows) == []


def test_empty_and_none():
    assert jc.trend_breaks([]) == []
    assert jc.trend_breaks(None) == []


# ── 소비 모듈이 실제로 이 소스를 쓰는가 ──
def test_faithfulness_uses_single_source(monkeypatch):
    import core.faithfulness as f
    monkeypatch.setattr(jc, "judge_model", lambda role="quality": "pinned-x")
    assert f._judge_model() == "pinned-x"


def test_llm_judge_uses_single_source(monkeypatch):
    import core.llm_judge as q
    monkeypatch.setattr(jc, "judge_model", lambda role="quality": "pinned-y")
    assert q._judge_model() == "pinned-y"


def test_resolution_is_lazy_not_import_time(monkeypatch):
    """config 무재배포 전환을 살리려면 **호출 시점**에 해석해야 한다."""
    import core.llm_judge as q
    monkeypatch.setattr(jc, "judge_model", lambda role="quality": "first")
    assert q._judge_model() == "first"
    monkeypatch.setattr(jc, "judge_model", lambda role="quality": "second")
    assert q._judge_model() == "second"


def test_failure_falls_back_not_crash(monkeypatch):
    """평가가 config 문제로 멈추면 안 된다."""
    import core.faithfulness as f
    monkeypatch.setattr(jc, "judge_model",
                        lambda role="quality": (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.delenv("FAITH_JUDGE_MODEL", raising=False)
    assert f._judge_model() == "qwen2.5:14b"
