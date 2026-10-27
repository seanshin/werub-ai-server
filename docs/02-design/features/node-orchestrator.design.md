# Node Orchestrator — GPU↔iMac 통합 동적 오케스트레이션 — Design

> **Plan**: `docs/01-plan/features/node-orchestrator.plan.md`
> **Scope**: 범용 작업 / 완전 동적 스케줄링 / 중앙 오케스트레이터부터 (2026-06-08 사용자 결정)
> **본 설계 범위**: **Phase 1 코어 + detect 이전** (v2.84.0) 구현 확정. Phase 2~4는 인터페이스 계약만.
> **불변식**: 노드 0개 = 기존 동작 100% / GPU 메인 최소변경 / 폴백 예외 전파 금지 / Darwin 가드 / 무상태 워커

## 1. 컴포넌트 구조

```
core/orchestrator/                         (신규 패키지 — 미사용 시 완전 휴면)
├─ registry.py    Capability 레지스트리 (선언적 — config/orchestrator.json 로드)
├─ nodes.py       Node Health Plane (노드별 실시간 상태 + EWMA + circuit breaker)
├─ scheduler.py   pick(cap, hints) — 후보 필터 → 점수화 → 노드 선택
├─ client.py      Node Client — 원격 워커 호출 (apple_worker 일반화·흡수)
├─ dispatch.py    run(cap, payload, local_fn) — 단일 진입점 (pick→호출→폴백→기록)
└─ metrics.py     관측성 (노드×cap 카운터/지연/결정 로그, Prometheus)

routes/image.py   detect 핸들러: bespoke 위임 → orchestrator.run("detect", …)  (1곳 치환)
routes/orchestrator_nodes.py  GET /api/orchestrator/nodes (Phase 4 제어판의 읽기부 선반영)
config/orchestrator.json      capability 선언 + 노드 정의 (tools.json 패턴 — 무재배포 reload)
```

기존 `core/apple_worker.py`는 **Node Client로 흡수**(`client.py`가 그 로직 계승: _conf/health 30s 캐시/None 폴백). 호환 위해 `apple_worker.detect()`는 한동안 얇은 wrapper로 유지(→ `dispatch.run`).

## 2. config/orchestrator.json (선언적 — tools.json 패턴, /reload 무재배포)

```json
{
  "nodes": {
    "gpu-local": { "kind": "local", "capabilities": ["detect", "mark", "stt", "embed", "llm_light", "llm_heavy"] },
    "imac-worker": {
      "kind": "remote",
      "url": "http://localhost:8587",
      "token_config_key": "apple_worker_token",   // 시크릿은 config.json에서 (저장소 미보관)
      "health_path": "/worker/health"
    }
  },
  "capabilities": {
    "detect": { "resource": "light", "worker_route": "/worker/detect/image", "payload_kind": "image", "policy": "auto" },
    "mark":   { "resource": "light", "worker_route": "/worker/mark/image",   "payload_kind": "image", "policy": "auto" }
  },
  "scheduler": { "w_inflight": 1.0, "w_latency": 0.5, "min_dwell_ms": 2000, "breaker_fails": 3, "breaker_open_ms": 30000 }
}
```

- `policy`: `auto`(동적) | `prefer_imac` | `prefer_gpu` | `gpu_only`(위임 금지, 즉시 무력화 스위치)
- 미존재/로드 실패 → **노드 0개로 간주**(전부 로컬). Darwin이면 remote 노드 무시.
- 노드의 실제 capability는 선언값이 아니라 **워커 health manifest가 진실** — 선언은 후보군, health가 가용 확정(버전 스큐 안전).

## 3. registry.py

```python
@dataclass(frozen=True)
class Capability:
    name: str
    resource: str            # "light" | "heavy"  → 타임아웃 매핑(light=5s/heavy=30s)
    worker_route: str
    payload_kind: str = "image"   # "image"(multipart 'image') | "json" | "file" — client 인코딩 계약(W3)
    policy: str = "auto"
    constraints: dict = field(default_factory=dict)   # 예: {"embed_model_pinned": True}

def load() -> None: ...                  # config/orchestrator.json 로드 (lazy, mtime 캐시)
def reload() -> dict: ...                 # /api/orchestrator/tools/reload 패턴 (localhost only)
def capability(name: str) -> Capability | None
def nodes_for(cap: str) -> list[str]     # 선언상 이 cap을 가진 노드 id 목록(gpu-local 포함)
def node(node_id: str) -> dict | None
```

## 4. nodes.py — Node Health Plane

```python
@dataclass
class NodeState:
    id: str
    kind: str                # local | remote
    reachable: bool = True    # local은 항상 True
    capabilities: list = field(default_factory=list)   # health manifest 기준(remote)
    mem_pressure: str = "normal"   # normal|warn|critical|unknown  (worker_guard 실제 어휘)
    thermal: str = "nominal"       # nominal|throttled|unknown     (worker_guard 실제 어휘)
    inflight: int = 0
    latency_ewma_ms: dict = field(default_factory=dict)  # {cap: ms}
    breaker: str = "closed"   # closed|open|half_open
    breaker_until: float = 0.0
    fails: int = 0

_LOCK = threading.Lock()   # nodes-plane 단일 락 — gthread 96 동시성 보호(W2)

# 헬스 폴링 — 기존 apple_worker 30s 캐시 계승. remote는 health_path GET.
def state(node_id: str) -> NodeState           # 캐시 반영 최신 상태(스냅샷 복사 반환)
def refresh(node_id: str) -> None              # health 강제 갱신
def on_dispatch_start(node_id, cap):           # with _LOCK: inflight += 1
def on_dispatch_end(node_id, cap, ok, ms):     # with _LOCK: EWMA + breaker + inflight -= 1
    # ok=False면 fails++; fails>=breaker_fails → breaker=open(until=now+open_ms)
    # ok=True면 fails=0; half_open에서 성공 시 closed
def ewma(prev, sample, alpha=0.3) -> float
```

**동시성(W2)**: `inflight`/`fails`/breaker/EWMA dict는 gthread 96 요청 스레드에서 변이되므로
**모든 변이를 `_LOCK`으로 보호**(GIL 가정 금지 — `inflight += 1`은 비원자). `state()`는 락 안에서
스냅샷을 복사해 반환(읽기 측 torn-read 방지). `on_dispatch_end`는 inflight 감소를 **반드시 finally
경로**로 보장(누수 시 영구 오스케줄). 회귀: N개 동시 dispatch 후 inflight==0 단언 테스트(§11).

**Health 필드 매핑(C2)** — worker_app.py `/worker/health` 실제 스키마:
| health 경로 | NodeState | 비고 |
|---|---|---|
| `ok` | reachable(=200 AND ok) | 도달 실패 → reachable=False |
| `capabilities` | capabilities | manifest = 가용 진실 |
| `memory.pressure` | mem_pressure | **중첩 경로**. 없거나 `unknown` → "unknown" |
| `thermal` | thermal | 문자열. 없으면 "unknown" |
| `locks.heavy_task` | (Phase 3 게이트용) | heavy 점유 시 heavy cap 후보 제외 |

- **`unknown` 처리**: mem/thermal이 `unknown`이면 **게이트하지 않고 통과**(신호 부재 ≠ 위험). 단 mem warn은 소프트 패널티.
- **local 노드(gpu-local)**: reachable 항상 True, inflight만 추적. mem/thermal은 Phase 3에서 GPU self-stat 연동(현 단계 normal 고정).
- **remote 노드**: 위 매핑. 도달 실패 시 reachable=False.

## 5. scheduler.py — pick()

```python
def pick(cap: str, hints: dict | None = None) -> str | None:
    c = registry.capability(cap)
    if c is None: return "gpu-local" if _local_has(cap) else None
    if c.policy == "gpu_only": return "gpu-local" if _local_has(cap) else None

    cands = []
    for nid in registry.nodes_for(cap):
        st = nodes.state(nid)
        if not _eligible(st, cap, c): continue       # 하드 게이트
        cands.append((nid, _score(nid, st, cap, c)))
    if not cands:
        return "gpu-local" if _local_has(cap) else None
    return max(cands, key=lambda x: x[1])[0]

def _eligible(st, cap, c) -> bool:
    if st.kind == "local": return True               # 로컬은 항상 적격(폴백 보장)
    if not st.reachable: return False
    if cap not in st.capabilities: return False       # health manifest 기준
    if st.breaker == "open" and now() < st.breaker_until: return False
    if st.mem_pressure == "critical": return False    # 하드 게이트
    if st.thermal == "throttled": return False        # 하드 게이트 (C1: worker는 nominal|throttled|unknown만 방출)
    if c.resource == "heavy" and st.locks_heavy: return False  # heavy 점유 시 heavy cap 제외(Phase 3 활성)
    if c.constraints.get("embed_model_pinned") and not _model_ok(cap, hints): return False
    return True
    # mem/thermal == "unknown" 은 게이트하지 않음(신호 부재 ≠ 위험)

def _score(nid, st, cap, c) -> float:
    base = {"prefer_imac": 10, "prefer_gpu": -10}.get(c.policy, 0)
    if st.kind == "remote" and c.policy == "prefer_imac": base += 5
    if st.kind == "local"  and c.policy == "prefer_gpu":  base += 5
    pen  = cfg.w_inflight * st.inflight
    pen += cfg.w_latency  * (st.latency_ewma_ms.get(cap, 0) / 1000.0)
    if st.mem_pressure == "warn": pen += 5            # 소프트 패널티
    return base - pen
```

- **하드 게이트**(후보 제외): 미도달 / cap 없음 / breaker open / mem critical / **thermal throttled** / heavy 점유(heavy cap) / constraint 위반
- **소프트 가중**: inflight, latency EWMA, mem warn. mem/thermal `unknown`은 미게이트(통과)
- **policy**로 즉시 제어: `gpu_only` = 위임 봉쇄, `prefer_gpu/imac` = 편향
- **hysteresis(I1·W2)**: 직전 선택을 `_last_pick: dict[cap→(node,ts)]`에 `_LOCK`으로 저장. 신점수와 직전 노드
  점수차가 임계 미만이고 `now-ts < min_dwell_ms`면 직전 노드 유지 — 진동 방지. 상태는 nodes-plane 락 공유.

## 6. dispatch.py — run() (엔드포인트 단일 진입점)

```python
def run(cap: str, payload, *, local_fn, hints=None, remote_call=None):
    """노드 선택 → 원격이면 호출, 실패/미선택 시 local_fn 폴백. 예외 전파 금지."""
    nid = scheduler.pick(cap, hints)
    if nid is None or nid == "gpu-local":
        return _run_local(cap, local_fn)
    nodes.on_dispatch_start(nid, cap); t0 = now()
    try:
        rc = remote_call or client.call
        res = rc(nid, cap, payload, hints)          # 워커 route POST + 토큰
        ok = res is not None
        nodes.on_dispatch_end(nid, cap, ok, ms(t0))
        if ok:
            metrics.record(cap, nid, "delegated", ms(t0)); res["engine"] = nid; return res
        metrics.record(cap, nid, "fallback", ms(t0))
    except Exception as e:
        nodes.on_dispatch_end(nid, cap, False, ms(t0))
        metrics.record(cap, nid, "error", ms(t0), detail=str(e)[:80])
    return _run_local(cap, local_fn)                 # 항상 폴백

def _run_local(cap, local_fn):
    t0 = now()
    try:
        r = local_fn()                               # local_fn은 self-guard 권장(no-raise 계약)
    except Exception as e:                            # W4: 폴백도 예외 전파 금지 — 방어 가드
        logger.warning("local_fn(%s) raised, returning error shape: %s", cap, e)
        r = {"available": False, "error": str(e)[:120]}
    if isinstance(r, dict):
        r.setdefault("engine", "gpu-local")          # W6: 로컬도 engine 스탬프
    metrics.record(cap, "gpu-local", "local", ms(t0))
    return r
```

**폴백 불변식(W4)**: 원격뿐 아니라 `_run_local`도 `local_fn` 예외를 가드해 **절대 전파하지 않는다**.
detect 이전 시 `routes/image.py`의 기존 `classify` try/except는 `local_fn` 내부에 유지(이중 안전).

**engine 필드(W6)**: 원격=노드 id(`imac-worker`), 로컬=`gpu-local`. ⚠️ 기존 `apple_worker.detect`는
`engine="imac"`였음 → **`imac`→`imac-worker` 리네임**. 호환 wrapper·파리티 테스트·소비자(있다면) 동시 갱신.

폴백 계약은 기존 `apple_worker`와 동일하되 **중앙화** — 모든 capability가 자동으로 동일 보장.

## 7. client.py — Node Client (apple_worker 흡수)

```python
def call(node_id: str, cap: str, payload, hints=None) -> dict | None:
    """remote 노드 워커 route 호출. 성공 dict, 실패 None (예외는 dispatch가 처리)."""
    n = registry.node(node_id); c = registry.capability(cap)
    url = n["url"].rstrip("/") + c.worker_route
    token = load_config().get(n["token_config_key"], "")
    files, json_body = _encode(payload, c.payload_kind)   # payload_kind 기반(W3): image→multipart 'image'
    r = requests.post(url, files=files, json=json_body,
                      headers={"X-Worker-Token": token}, timeout=_timeout(c.resource))
    if r.status_code == 200:
        d = r.json()
        return d if d.get("available", True) else None    # ok = HTTP200 AND available!=false (W1)
    return None
```

- **`_encode(payload, kind)`(W3)**: `image`→`files={"image": ("img.png", bytes)}`(워커 `_get_image_bytes` 필드명 일치) / `json`→body / `file`→files. 타입 추론이 아니라 **capability의 payload_kind 계약**으로 결정.
- **ok 정의(W1)**: HTTP 200 **그리고** `available != false`. 모든 워커 route가 지켜야 할 파리티 계약(현 detect는 503/200으로 이미 보장, mark/future도 동일 준수).
- 타임아웃: light=5s, heavy=30s (capability resource 기반, 기존 apple_worker 상수와 동일)
- 헬스 캐시/Darwin 가드는 nodes.py/registry.py로 이동(기존 apple_worker 로직 분해 흡수)

## 8. detect 이전 (routes/image.py)

```python
# BEFORE (현재 1115~1123)
from core import apple_worker
delegated = apple_worker.detect(img_bytes)
if delegated is not None: ml = delegated
# (else 아래 로컬 classify 경로)

# AFTER
from core.orchestrator import dispatch
# local_fn은 self-guard: classify는 이미 내부 try/except로 {available:False}를 반환(no-raise)
ml = dispatch.run(
    "detect", img_bytes,
    local_fn=lambda: ai_image_detector.classify(img_bytes,
                model=cfg.get("ai_detector_model","Organika/sdxl-detector"),
                device=cfg.get("ai_detector_device","cpu"),
                threads=cfg.get("ai_detector_threads",4)),
)
# ml["engine"] in {"imac-worker", "gpu-local"} — 양 경로 모두 engine 스탬프(W6). 그 외 스키마 불변
```

동작 보존: 노드 미설정/미도달 → local_fn 실행(기존 CPU classify와 바이트 동일, `engine="gpu-local"`만 추가). 결과 스키마 불변.

**apple_worker 호환 wrapper(I2)**: `imac_link.py`가 `apple_worker._conf()`/`stats()`를, 코드가 `detect()`/`available()`를 사용 중 → wrapper는 `_conf`/`available`/`stats`/`detect` **모두** 유지(내부적으로 orchestrator/nodes/dispatch에 위임). `engine` 리네임으로 `stats`의 recent 라벨도 노드 id로 통일.

## 9. metrics.py + 읽기 API

- 인메모리: `{(cap,node,decision): count}`, `{(cap,node): latency_ewma}`, 최근 결정 deque(20)
- Prometheus: `orchestrator_dispatch_total{capability,node,decision}`, `orchestrator_latency_ms{capability,node}`
- `GET /api/orchestrator/nodes` (읽기): 노드×cap×health×breaker×최근결정 — imac-link status와 상호 보완(중복은 link가 터널, 여기는 라우팅 결정)

## 10. 보안
- 워커 인증 X-Worker-Token 유지(`client.py`). 토큰은 config.json만(저장소 미보관).
- reload·정책 변경의 **localhost 보장은 전역 미들웨어**(`local_bypass`/auth)에서 옴(W5) — 핸들러 내 자체 검사는 기존 `/api/orchestrator/tools/reload`와 동일하게 없음. reload는 프로세스 상태를 변이하므로 외부 인증 호출자도 트리거 가능함을 명시(운영상 localhost 한정 유지).
- Darwin 가드: remote 노드는 Darwin 호스트에서 후보 제외(iMac 자기 위임 금지).

## 11. 테스트 계획 (tests/test_orchestrator_*.py — 순수 단위, 네트워크 0)

| 파일 | 검증 |
|------|------|
| test_orchestrator_registry | config 로드/reload, capability/nodes_for, 미존재 시 빈 노드 |
| test_orchestrator_scheduler | 게이트(미도달/breaker/mem critical/thermal throttled/cap 없음 → 제외), 점수(inflight·latency·policy), gpu_only=로컬, 후보 0 → gpu-local 폴백, Darwin=remote 제외, **hysteresis 유지/전환** |
| test_orchestrator_nodes | EWMA 갱신, breaker 전이(closed→open→half_open→closed), **health 필드 매핑(memory.pressure 중첩·thermal·unknown)**, **동시 dispatch N건 후 inflight==0**(W2) |
| test_orchestrator_scheduler | (위 게이트) + **thermal=="throttled" 제외·unknown 통과**(C1/C2), hysteresis 유지 |
| test_orchestrator_dispatch | 원격 성공=engine="imac-worker", 원격 None/예외=local_fn 폴백·**예외 전파 0(local_fn raise 포함, W4)**, 노드 0개=항상 local, **로컬 engine="gpu-local"**(W6), metrics 기록 |
| test_orchestrator_detect_parity | detect 이전 후 스키마/값이 기존 classify와 동일(mock client) + **로컬 분기 engine=="gpu-local" 단언**(W6) |

- 기존 `test_apple_worker.py`/`test_worker.py`는 wrapper 호환(`_conf`/`available`/`stats`/`detect`) 유지 검증으로 존속(I2)
- 폴백·Darwin·노드0 불변식은 전 플랫폼(GPU/iMac) 동일 통과
- **동시성 테스트 필수**(W2): threading으로 N개 동시 run() → inflight 누수 0, breaker 카운트 정확

## 12. 구현 순서 (Phase 1)

1. `config/orchestrator.json` + `registry.py` (+ reload 엔드포인트)
2. `nodes.py` (health 폴링 = apple_worker 캐시 로직 이식 + EWMA/breaker)
3. `scheduler.py` (pick + 게이트 + 점수 + hysteresis)
4. `client.py` (apple_worker call 로직 흡수, resource 타임아웃)
5. `dispatch.py` (run + 폴백 + metrics 연동)
6. `metrics.py` + `GET /api/orchestrator/nodes`
7. `routes/image.py` detect 이전 + `apple_worker` 얇은 wrapper 전환
8. 테스트 5파일 + 회귀(노드0 불변식)
9. 문서: ARCHITECTURE/CROSS_ARCH 갱신, RELEASE_v2.84.0

## 13. Phase 2~4 인터페이스 계약 (선반영, 본 설계 외 구현)
- **Phase 2**: 워커 `/worker/run/<cap>` generic + manifest 확장 → registry에 mark/stt/embed 항목만 추가, run() 그대로
- **Phase 3**: `llm_light` capability + 큐 잡 라우팅 → gpu-local self-stat(mem/thermal) nodes.py 연동, 메모리 상호배제 게이트
- **Phase 4**: `POST /api/orchestrator/policy`(localhost) 정책/가중치/drain + Admin 콘솔 카드

## 14. 검토 이력

| 일자 | 검토 결과 |
|------|----------|
| 2026-06-08 | design-validator 검증(82/100) 후 반영 — **C1** thermal 어휘 정정(worker는 `nominal\|throttled\|unknown`만 방출 → 게이트 `throttled`) **C2** health 필드 매핑 표 명시(`memory.pressure` 중첩·`unknown` 미게이트) **W2** nodes-plane `_LOCK` 동시성 + inflight finally + 동시성 테스트 추가 **W3** `payload_kind`를 Capability 계약으로 **W4** `_run_local` 예외 가드(폴백 무전파) **W6** 로컬 `engine="gpu-local"` 스탬프 + `imac`→`imac-worker` 리네임 **W1** ok=HTTP200∧available **W5** localhost는 전역 미들웨어 명시 **I2** wrapper를 `_conf/available/stats/detect` 전부 커버 |
| 2026-06-08 (구현 정합, gap 96%) | **apple_worker 처리 = 흡수 대신 "유지+config 재사용"으로 reconcile** (저위험·동작보존): `core/apple_worker.py`는 **그대로 존속**(`imac_link.py`의 `_conf/stats/IS_DARWIN` 소비 유지, `engine="imac"` 보존). 오케스트레이터는 별도 신규 패키지로, 노드 런타임 설정을 **config.json `apple_worker_*` 키 재사용**(`nodes.imac_conf`)해 활성화 스크립트·imac_link와 정합. 라이브 detect는 `dispatch.run` 경로 사용. 두 경로 공존은 **Phase 2 기술부채**(apple_worker를 dispatch 위 thin wrapper로 수렴 + imac_link를 orchestrator 상태로 이전)로 기록. §1/§7/§8의 "흡수" 서술은 이 reconcile이 우선. + hysteresis 테스트 추가로 잔여 Medium gap 해소. |
