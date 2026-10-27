"""정본 전량 교체 전 상대 감소 가드 (2026-08-06 · v2.124.93).

🔴 실사고: 치료재료 91,211 → **59,000**(-35%)이 절대 하한 `MIN_COUNT=10,000`을 넉넉히
통과해 `status=ok`로 전량 교체됐고 정본 32,377행이 조용히 사라졌다.
절대 하한은 "0에 가까운 응답"만 잡고 **"35% 빠진 응답"은 못 잡는다.**

기준은 메타의 직전 건수가 아니라 **현재 적재된 실제 행 수**다 — 메타는 source가 PK라
skip이 한 번 기록되면 직전 ok 건수를 잃는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import sync_guard as g  # noqa: E402


def test_partial_response_rejected():
    """실사고 그대로 — 91,211에서 59,000이면 거부."""
    assert g.drop_too_large(59000, 91211, 0.20) is True


def test_normal_variation_passes():
    """정상 변동(-5%)은 통과 — 가드가 갱신 자체를 막으면 안 된다."""
    assert g.drop_too_large(86000, 91211, 0.20) is False


def test_boundary_is_inclusive():
    """정확히 임계(-20%)는 통과, 그보다 적으면 거부."""
    assert g.drop_too_large(80, 100, 0.20) is False
    assert g.drop_too_large(79, 100, 0.20) is True


def test_increase_always_passes():
    assert g.drop_too_large(200000, 91211, 0.20) is False


def test_first_load_fails_open():
    """첫 적재(현재 0/None)는 비교 대상이 없다 — 통과."""
    assert g.drop_too_large(5, 0, 0.20) is False
    assert g.drop_too_large(5, None, 0.20) is False


def test_ratio_one_disables_guard():
    """1.0 = 가드 해제(운영자가 의도적으로 끌 수 있어야 한다)."""
    assert g.drop_too_large(1, 100000, 1.0) is False


def test_ratio_zero_forbids_any_drop():
    assert g.drop_too_large(99, 100, 0.0) is True


def test_max_drop_ratio_clamped(monkeypatch):
    import core.config as cfg
    monkeypatch.setattr(cfg, "load_config", lambda: {"sync_max_drop_ratio": 0.05})
    assert g.max_drop_ratio() == 0.05
    monkeypatch.setattr(cfg, "load_config", lambda: {"sync_max_drop_ratio": 9})
    assert g.max_drop_ratio() == 1.0          # 오타가 가드를 무력화하지 않게 클램프
    monkeypatch.setattr(cfg, "load_config", lambda: {"sync_max_drop_ratio": -1})
    assert g.max_drop_ratio() == 0.0


def test_max_drop_ratio_config_failure_keeps_default(monkeypatch):
    import core.config as cfg

    def boom():
        raise OSError("config unreadable")

    monkeypatch.setattr(cfg, "load_config", boom)
    assert g.max_drop_ratio() == g.DEFAULT_MAX_DROP_RATIO


def test_safe_count_fails_open():
    """현재 건수 조회 실패는 None — 조회 때문에 갱신이 막히면 안 된다."""
    def boom():
        raise RuntimeError("db locked")

    assert g.safe_count(boom) is None
    assert g.drop_too_large(1, g.safe_count(boom), 0.20) is False


def test_safe_count_normalizes():
    assert g.safe_count(lambda: 42) == 42
    assert g.safe_count(lambda: None) is None


def test_drop_reason_mentions_numbers():
    """사유는 사람이 판단할 수 있게 실제 수치를 담는다."""
    msg = g.drop_reason(59000, 91211, 0.20)
    assert "59000" in msg and "91211" in msg and "-35%" in msg
