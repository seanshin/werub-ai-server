"""v2.125.47 — 약물 배치 LLM_SEED 비수렴 루프 정지 + 임상 표 반영 차단.

라이브 실측(2026-06-04~09-27): 자동 수집 소스가 LLM_SEED(모델 자체 지식·T4)뿐이라
밤마다 같은 약을 반복 생성 → staging 25,565건 전부 중복 skip(산출 0·14b 호출 ~17회/밤).
그 전에 통과한 51건은 임상 표 drug_codes에 들어가 있었고(성분명·용량 오류 포함),
31건은 is_common=1이라 medical_cli 처방 추천에 노출될 수 있었다.
"""
import re
from pathlib import Path

import pytest

from core import drug_batch_scheduler as dbs

SRC = Path(dbs.__file__).read_text(encoding="utf-8")


class _Touched(Exception):
    pass


def _boom():
    raise _Touched()


@pytest.mark.parametrize("trigger", ["night_auto", "scheduler"])
def test_auto_trigger_without_source_skips_before_db(monkeypatch, trigger):
    """자동 실행은 소스 미지정이면 LLM_SEED로 떨어지지 않고 DB도 안 연다(빈 batch_run 행도 없음)."""
    monkeypatch.setattr(dbs, "_get_clinic_db", _boom)
    r = dbs.run_pipeline("night_collect", trigger=trigger)
    assert r["status"] == "skipped" and r["reason"] == "no_automatic_source"


def test_manual_trigger_still_runs(monkeypatch):
    """수동 경로는 막지 않는다(운영자가 명시적으로 돌리는 것은 허용)."""
    monkeypatch.setattr(dbs, "_get_clinic_db", _boom)
    with pytest.raises(_Touched):
        dbs.run_pipeline("daily_collect", trigger="manual")


def test_explicit_source_auto_still_runs(monkeypatch):
    monkeypatch.setattr(dbs, "_get_clinic_db", _boom)
    with pytest.raises(_Touched):
        dbs.run_pipeline("daily_collect", trigger="scheduler", source="MANUAL")


def test_night_mode_default_off():
    assert re.search(r'NIGHT_MODE = \{\s*"enabled": False,', SRC), "야간 자동 수집 기본값이 off가 아니다"


def test_start_scheduler_honours_config_flag(monkeypatch):
    started = []
    monkeypatch.setattr(dbs.threading, "Thread",
                        lambda *a, **k: type("T", (), {"start": lambda self: started.append(1)})())
    saved = dbs.NIGHT_MODE["enabled"]
    try:
        dbs.start_scheduler_loop(night_enabled=False)
        assert dbs.NIGHT_MODE["enabled"] is False
        dbs.start_scheduler_loop(night_enabled=True)
        assert dbs.NIGHT_MODE["enabled"] is True
    finally:
        dbs.NIGHT_MODE["enabled"] = saved
    assert started == [1, 1]


def test_app_wires_config_flag_default_false():
    app_src = (Path(dbs.__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    assert 'start_drug_scheduler(night_enabled=load_config().get("drug_batch_night_enabled", False))' in app_src


def test_t4_guard_precedes_commit():
    """검증을 통과해도 T4는 needs_review로 빠지고 _commit_drug에 닿지 않는다(구조 고정)."""
    i_guard = SRC.index('row_dict.get("source_tier") in _REVIEW_ONLY_TIERS')
    i_commit = SRC.index("_commit_drug(conn, row_dict, parsed, run_id, model)")
    assert i_guard < i_commit
    block = SRC[i_guard:i_commit]
    assert "needs_review" in block and "elif all_passed" in block
    assert "T4" in SRC[SRC.index("_REVIEW_ONLY_TIERS ="):][:60]
