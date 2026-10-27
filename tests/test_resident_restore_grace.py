"""v2.125.48 — 상주 모델 복원 유예(적재 스래싱 봉합) + health_check 스왑 경고 상태 전이.

라이브 실측(2026-09-28 평가 배치 8.6분): Ollama 적재 125회(14b 62·7b 63), 앱 예열 로그 63회
⇒ 14b 적재가 거의 전부 큐 워커의 2초 주기 예열이었다(7b 교차검증 호출 사이 틈마다 끼어듦).
"""
import os
import subprocess
from pathlib import Path

import pytest

from core.llm_residency import restore_due, restore_grace_s, DEFAULT_RESTORE_GRACE_S

R = "qwen2.5:14b"


def _m(name, exp):
    return {"name": name, "expires_at": exp}


def test_resident_loaded_never_restores():
    st = {"sig": "x"}
    assert restore_due([_m(R, "2318")], R, st, 100.0, 30) is False
    assert st == {}


def test_nothing_loaded_restores_after_grace():
    st = {}
    assert restore_due([], R, st, 0.0, 30) is False
    assert restore_due([], R, st, 29.0, 30) is False
    assert restore_due([], R, st, 30.0, 30) is True


def test_empty_gap_during_swap_does_not_restore():
    """회귀(v2.125.48 1차 실측): 7b→(빈 순간)→7b 교체 중 빈 상태에서 끼어들면 안 된다."""
    st = {}
    t = 0.0
    seq = [[_m("qwen2.5:7b", "A")], [], [_m("qwen2.5:7b", "B")], [], [_m("qwen2.5:7b", "C")]]
    for ps in seq * 10:
        assert restore_due(ps, R, st, t, 30) is False
        t += 2.0


def test_active_other_model_blocks_restore():
    """핵심: 7b가 계속 쓰이는 동안(expires_at 전진)은 몇 분이 지나도 복원하지 않는다."""
    st = {}
    t = 0.0
    for i in range(150):                       # 2초 폴링 × 150 = 5분
        exp = f"2026-09-28T09:{i // 30:02d}:{(i * 2) % 60:02d}"
        assert restore_due([_m("qwen2.5:7b", exp)], R, st, t, 30) is False
        t += 2.0


def test_restores_after_grace_once_other_idle():
    st = {}
    assert restore_due([_m("qwen2.5:7b", "A")], R, st, 0.0, 30) is False
    assert restore_due([_m("qwen2.5:7b", "A")], R, st, 29.0, 30) is False
    assert restore_due([_m("qwen2.5:7b", "A")], R, st, 30.0, 30) is True


def test_activity_resets_grace_clock():
    st = {}
    restore_due([_m("qwen2.5:7b", "A")], R, st, 0.0, 30)
    assert restore_due([_m("qwen2.5:7b", "B")], R, st, 25.0, 30) is False   # 사용됨 → 시계 리셋
    assert restore_due([_m("qwen2.5:7b", "B")], R, st, 50.0, 30) is False
    assert restore_due([_m("qwen2.5:7b", "B")], R, st, 55.0, 30) is True


def test_zero_grace_is_legacy_immediate():
    assert restore_due([_m("qwen2.5:7b", "A")], R, {}, 0.0, 0) is True


@pytest.mark.parametrize("cfg,exp", [
    ({}, DEFAULT_RESTORE_GRACE_S), ({"resident_restore_grace_s": 5}, 5.0),
    ({"resident_restore_grace_s": -1}, DEFAULT_RESTORE_GRACE_S),
    ({"resident_restore_grace_s": "x"}, DEFAULT_RESTORE_GRACE_S), (None, DEFAULT_RESTORE_GRACE_S),
])
def test_grace_config(cfg, exp):
    assert restore_grace_s(cfg) == exp


def test_app_uses_restore_due():
    src = (Path(__file__).parent.parent / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def _ensure_default_model"):src.index("_LLM_BATCH_SIZE = 4")]
    assert "restore_due(running, default_model, _resident_restore_state" in body
    assert "if default_model not in loaded_names" not in body   # 즉시 복원 분기 재유입 금지


# ── health_check.sh 스왑 경고: 상태 변화 + 1h 요약만 ────────────────────────────
SCRIPT = Path(__file__).parent.parent / "scripts" / "health_check.sh"


def _swap_block():
    s = SCRIPT.read_text(encoding="utf-8")
    return s[s.index("# 스왑"):s.index("# 서비스 상태")]


def _run(tmp_path, pct, now):
    """스왑 블록만 떼어 free/date를 가짜로 바꿔 실행."""
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "free").write_text(f"#!/bin/sh\necho 'Swap: 100 {pct} {100 - pct}'\n")
    (fake / "date").write_text(f"#!/bin/sh\nif [ \"$1\" = \"+%s\" ]; then echo {now}; else echo TS; fi\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    (tmp_path / "logs").mkdir(exist_ok=True)
    script = (f'SWAP_THRESHOLD=80\nPROJECT_DIR={tmp_path}\nLOG={tmp_path}/logs/h.log\n'
              'ts() { date "+%Y"; }\n' + _swap_block())
    subprocess.run(["bash", "-c", script], check=True,
                   env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"})
    p = tmp_path / "logs" / "h.log"
    return p.read_text().splitlines() if p.exists() else []


def test_swap_logs_only_on_transitions(tmp_path):
    assert len(_run(tmp_path, 100, 1000)) == 1            # 진입
    for t in (1300, 1600, 3000, 4500):                     # 5분 간격 반복 — 추가 기록 없음
        assert len(_run(tmp_path, 100, t)) == 1
    lines = _run(tmp_path, 100, 4700)                      # 진입 후 1h 경과 → 요약 1줄
    assert len(lines) == 2 and "1h 요약" in lines[-1]
    lines = _run(tmp_path, 10, 4800)                       # 해소
    assert len(lines) == 3 and "해소" in lines[-1]
    assert len(_run(tmp_path, 10, 5100)) == 3              # 정상 지속 — 기록 없음
