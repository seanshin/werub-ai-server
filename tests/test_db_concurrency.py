"""DB 레이어 동시성 + gunicorn fork 안전 회귀 테스트.

2026-06-08 incident: gunicorn preload_app=True + fork + SQLite -shm(MAP_SHARED) 상속으로
워커 connect 데드락(/api/chat 60s행). fix=preload_app=False. [[feedback_get_db_busy_timeout]]

순수 단위 — 외부 의존 0(임시 DB만). 실행: venv/bin/python -m pytest tests/test_db_concurrency.py -q
"""
import os
import re
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from core import db as dbmod  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 1) gunicorn fork 안전 가드 — 이번 incident의 핵심 fix 회귀 방지
# ─────────────────────────────────────────────────────────────

def _gunicorn_conf_text():
    with open(os.path.join(ROOT, "gunicorn.conf.py"), encoding="utf-8") as f:
        return f.read()


def test_preload_app_disabled():
    """preload_app=True면 마스터가 fork 전 queue.db를 열어 워커가 -shm 상속→connect 데드락.
    workers=1이라 preload 이득도 없음. 반드시 False 유지 (incident 재발 방지)."""
    txt = _gunicorn_conf_text()
    m = re.search(r"^\s*preload_app\s*=\s*(True|False)", txt, re.MULTILINE)
    assert m, "gunicorn.conf.py에 preload_app 설정이 보이지 않음"
    assert m.group(1) == "False", (
        "preload_app은 False여야 함 — True면 fork+SQLite -shm 상속으로 /api/chat 행 재발"
    )


def test_single_worker_invariant():
    """workers=1 전제(워커 루프 1개) — 변경 시 멀티프로세스 DB 경합/중복 스레드 재검토 필요."""
    txt = _gunicorn_conf_text()
    m = re.search(r"^\s*workers\s*=\s*(\d+)", txt, re.MULTILINE)
    assert m, "workers 설정이 보이지 않음"
    assert int(m.group(1)) == 1, "workers!=1이면 SQLite 멀티프로세스 경합·백그라운드 스레드 중복 재검토"


# ─────────────────────────────────────────────────────────────
# 2) get_db PRAGMA — busy_timeout 우선/WAL (v2.86.1 방어)
# ─────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "DB_PATH", str(tmp_path / "t.db"))
    yield
    # 메인 스레드 캐시 정리(테스트 간 격리)
    try:
        c = getattr(dbmod._db_local, "conn", None)
        if c:
            c.close()
    except Exception:
        pass
    dbmod._db_local.conn = None


def test_get_db_pragmas(tmp_db):
    conn = dbmod.get_db()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    # busy_timeout이 설정돼 락 시 무한대기하지 않아야 함(>0)
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000


# ─────────────────────────────────────────────────────────────
# 3) get_db 동시성 — 다수 스레드가 동시에 connect+읽기/쓰기해도 데드락/예외 없음
# ─────────────────────────────────────────────────────────────

def test_get_db_concurrent_no_deadlock(tmp_db):
    """gthread 다발 동시 접근 시뮬레이션 — 전부 데드락/오류 없이 완료해야 함."""
    N = 32
    results = [None] * N
    barrier = threading.Barrier(N)

    def worker(i):
        try:
            # ⚠️ 배리어는 **경합을 극대화하려는 장치이지 단정이 아니다.**
            #    머신이 바쁘면(예: 배포 중 ollama가 CPU를 먹을 때) 32스레드가 제때
            #    모이지 못해 BrokenBarrierError가 나는데, 그건 데드락이 아니다.
            #    2026-08-12: 이걸 오류로 세어 **배포를 막았고** 단독 재실행은 0.10초에 통과했다.
            #    정렬에 실패해도 **본 검사(데드락·오류 없음)는 그대로 수행**한다.
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            conn = dbmod.get_db()
            conn.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")
            conn.execute("INSERT INTO t (id) VALUES (?)", (i,))
            conn.commit()
            results[i] = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        except Exception as e:  # noqa: BLE001
            results[i] = f"ERR:{e}"

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    alive = [t for t in threads if t.is_alive()]
    assert not alive, f"{len(alive)}개 스레드가 20s 내 미완료 — 데드락 의심"

    # 🔑 **이 테스트가 지키는 것은 데드락이지 경합 성공률이 아니다.**
    # `database is locked`는 데드락이 아니라 **busy_timeout 만료**다. 32스레드가 한 파일에
    # 동시 쓰기를 시도하는 건 프로덕션(단일 워커·분산된 쓰기)보다 훨씬 가혹한 합성 조건이고,
    # 머신이 바쁘면(배포 중 ollama가 CPU 점유) 5초 안에 못 잡는 스레드가 나온다.
    # 2026-08-12: 이걸 실패로 세어 **릴리즈 배포를 막았고** 단독 재실행은 3/3 통과(0.06초)였다.
    #
    # ⚠️ **완화가 아니다** — busy_timeout 자체의 불변식은 `test_get_db_pragmas`가
    #    **결정적으로**(>=5000) 지킨다. 여기서는 그 값이 사라졌을 때 나타날 신호,
    #    즉 **대다수가 실패하는 상황**만 잡는다. 락 외의 오류는 여전히 즉시 실패다.
    locked = [r for r in results if isinstance(r, str) and "database is locked" in r]
    other = [r for r in results if isinstance(r, str) and r not in locked]
    assert not other, f"동시 접근 오류(락 외): {other[:3]}"
    assert len(locked) <= N // 2, (
        f"{len(locked)}/{N} 스레드가 락 타임아웃 — busy_timeout이 빠졌을 수 있다")
    assert any(isinstance(r, int) for r in results), "성공한 스레드가 하나도 없다"
