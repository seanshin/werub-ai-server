"""노드 스케줄러 — **동점 처리**와 **측정 대칭성** (2026-08-12 실결함).

🔴 증상: iMac 위임이 **한 건도 일어나지 않았다**. 그런데 터널 3채널·워커·설정·breaker가
   전부 정상이라 대시보드는 초록이었다 — `위임 0`만 유일한 신호였다.

근본 원인 두 겹:

  ① **동점을 목록 순서가 갈랐다.** iMac이 `mem_pressure=warn`(free 7.2GB·45% —
     macOS의 평상 상태)일 때 소프트 패널티 **5.0**이 remote+prefer_imac 보너스 **5.0**과
     정확히 상쇄돼 두 노드가 **10.00 동점**이 됐고, 안정 정렬이 후보 목록 첫 항목인
     gpu-local을 남겼다. ⇒ `prefer_imac`이 조용히 **'절대 imac 아님'**으로 뒤집혔다.

  ② **비교의 한쪽만 재고 있었다.** 로컬 실행은 `on_dispatch_end`를 타지 않아
     gpu-local의 `latency_ewma_ms`가 **영원히 비어 있었고**(지연 0 = 무한히 빠름),
     그래서 동점이 풀릴 여지조차 없었다.

📌 오늘 아침 경보 교훈과 같은 형태다 — **재지 않는 것과 비교하면 그건 판정이 아니라 편향이다.**
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core.orchestrator import nodes, registry, scheduler


def _st(nid, kind, **kw):
    st = nodes.NodeState(id=nid, kind=kind)
    st.reachable = kw.get("reachable", True)
    st.capabilities = kw.get("caps", ["detect", "mark", "stt"])
    st.mem_pressure = kw.get("mem", "normal")
    st.thermal = kw.get("thermal", "nominal")
    st.inflight = kw.get("inflight", 0)
    st.latency_ewma_ms = dict(kw.get("lat", {}))
    st.breaker = kw.get("breaker", "closed")
    return st


@pytest.fixture
def cap_detect():
    c = registry.capability("detect")
    assert c is not None and c.policy == "prefer_imac"
    return c


# ── ① 동점은 정책이 가른다 ──────────────────────────────────────────────────
def test_tie_goes_to_policy_preferred_node(monkeypatch, cap_detect):
    """🔴 이 한 줄이 위임을 통째로 죽였다 — 동점이면 정책이 이겨야 한다."""
    states = {nodes.GPU_LOCAL: _st(nodes.GPU_LOCAL, "local"),
              nodes.IMAC: _st(nodes.IMAC, "remote", mem="warn")}
    monkeypatch.setattr(nodes, "state", lambda nid: states.get(nid))
    monkeypatch.setattr(scheduler, "_candidate_ids",
                        lambda cap: [nodes.GPU_LOCAL, nodes.IMAC])
    sched = registry.scheduler_cfg()
    assert (scheduler._score(states[nodes.GPU_LOCAL], "detect", cap_detect, sched)
            == scheduler._score(states[nodes.IMAC], "detect", cap_detect, sched)), "동점 전제"
    assert scheduler.pick("detect") == nodes.IMAC


def test_tie_respects_prefer_gpu_too(monkeypatch):
    """정책이 반대면 동점은 로컬로 간다 — 규칙이지 iMac 편애가 아니다."""
    class _C:
        policy = "prefer_gpu"
        resource = "light"
        constraints = {}
    states = {nodes.GPU_LOCAL: _st(nodes.GPU_LOCAL, "local"),
              nodes.IMAC: _st(nodes.IMAC, "remote")}
    monkeypatch.setattr(registry, "capability", lambda cap: _C())
    monkeypatch.setattr(nodes, "state", lambda nid: states.get(nid))
    monkeypatch.setattr(scheduler, "_candidate_ids",
                        lambda cap: [nodes.IMAC, nodes.GPU_LOCAL])   # 순서 뒤집어도
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_policy_rank_only_breaks_ties_not_scores(monkeypatch, cap_detect):
    """정책은 **동점일 때만** 개입한다 — 점수가 확실히 낮으면 지는 게 맞다."""
    states = {nodes.GPU_LOCAL: _st(nodes.GPU_LOCAL, "local"),
              nodes.IMAC: _st(nodes.IMAC, "remote", mem="warn",
                              lat={"detect": 20000})}      # 20초 = 확실히 느림
    monkeypatch.setattr(nodes, "state", lambda nid: states.get(nid))
    monkeypatch.setattr(scheduler, "_candidate_ids",
                        lambda cap: [nodes.GPU_LOCAL, nodes.IMAC])
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_hard_gates_still_win_over_policy(monkeypatch, cap_detect):
    """정책이 하드 게이트를 이기면 안 된다 — 메모리 critical은 여전히 제외."""
    states = {nodes.GPU_LOCAL: _st(nodes.GPU_LOCAL, "local"),
              nodes.IMAC: _st(nodes.IMAC, "remote", mem="critical")}
    monkeypatch.setattr(nodes, "state", lambda nid: states.get(nid))
    monkeypatch.setattr(scheduler, "_candidate_ids",
                        lambda cap: [nodes.GPU_LOCAL, nodes.IMAC])
    assert scheduler.pick("detect") == nodes.GPU_LOCAL


def test_warn_penalty_still_applies():
    """패널티를 없앤 게 아니다 — warn은 여전히 점수를 깎는다."""
    c = registry.capability("detect")
    sched = registry.scheduler_cfg()
    normal = scheduler._score(_st(nodes.IMAC, "remote"), "detect", c, sched)
    warned = scheduler._score(_st(nodes.IMAC, "remote", mem="warn"), "detect", c, sched)
    assert warned < normal


# ── ② 측정 대칭성 ───────────────────────────────────────────────────────────
def test_local_run_records_latency():
    """🔴 로컬 지연이 기록되지 않으면 스케줄러가 **재지 않은 노드**를 무한히 빠르다고 본다."""
    from core.orchestrator import dispatch
    nodes.record_latency(nodes.GPU_LOCAL, "detect-test-cap", 0)   # 초기화
    dispatch._run_local("detect-test-cap", lambda: {"ok": True})
    st = nodes.state(nodes.GPU_LOCAL)
    assert "detect-test-cap" in st.latency_ewma_ms


def test_record_latency_does_not_touch_breaker():
    """지연 기록이 breaker·inflight를 건드리면 로컬 실행이 원격 회로를 흔든다."""
    before = nodes.state(nodes.GPU_LOCAL)
    nodes.record_latency(nodes.GPU_LOCAL, "cap-x", 123)
    after = nodes.state(nodes.GPU_LOCAL)
    assert after.breaker == before.breaker and after.inflight == before.inflight


def test_local_failure_still_returns_shape():
    """폴백은 예외를 던지지 않는다(기존 불변식) — 지연 기록을 넣어도 유지."""
    from core.orchestrator import dispatch

    def boom():
        raise RuntimeError("local broke")
    r = dispatch._run_local("cap-y", boom)
    assert r["available"] is False and r["engine"] == nodes.GPU_LOCAL
