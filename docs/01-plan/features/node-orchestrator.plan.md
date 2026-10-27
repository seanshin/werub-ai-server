# Node Orchestrator — GPU↔iMac 통합 동적 작업 오케스트레이션

> **Feature**: node-orchestrator
> **Created**: 2026-06-08
> **Version Target**: v2.84.0 ~ v2.87.0 (Phase별 릴리스)
> **Priority**: P1 (사용자 핵심 요구 — "GPU만 호출해도 자동으로 필요한 역할을 iMac에 배정")
> **Related**: apple-silicon-worker(워커), gpu-imac-link(터널), imac-link-console(콘솔), feedback_cross_arch_device
> **Scope 결정(2026-06-08)**: 범용 작업 전체 / 완전 동적 스케줄링 / 중앙 오케스트레이터부터

## 1. 목적

GPU 서버와 iMac을 **하나의 시스템처럼** 운영한다. 호출자는 "어느 노드에서 처리할지"를
의식하지 않고 GPU 서버 API만 호출하면, **중앙 오케스트레이터가 작업의 capability와 양 노드의
실시간 상태(부하·큐·메모리압박·발열·지연)를 보고 최적 노드에 자동 배정**한다. 어떤 경우에도
원격 실패 시 로컬로 폴백해 기존 동작을 100% 보존한다.

핵심 전환: **"엔드포인트마다 손으로 박는 분산 위임" → "capability 기반 중앙 동적 스케줄러"**.

## 2. 현황 — 부품은 있으나 "묶는 층"이 없음 (2026-06-08 코드 실측)

### 2-1. 이미 구축된 토대 ✅

| 구성 | 위치 | 상태 |
|------|------|------|
| iMac 워커 :8587 | `worker_app.py` | 가동. capabilities=`[detect, mark]`. 이중 인증(X-Worker-Token/Bearer), worker_guard(heavy/light 락·memory/thermal) |
| 위임 클라이언트 | `core/apple_worker.py` | config-gated, 헬스 30s 캐시, **실패→None→로컬 폴백** 계약, 인메모리 stats |
| 상태 콘솔 | `routes/imac_link.py` | Phase A(읽기전용) — 터널/워커/위임 가시화 |
| 통합 터널 | `setup/com.werub.link-tunnel.plist`(iMac) | L1~L8 토폴로지 설계됨(gpu-imac-link) |
| device 추상화 | `core/device.py` | cross-arch(cuda/mps/cpu) 단일화 |
| 워커 헬스 신호 | `/worker/health` | version/capabilities/**memory/thermal/locks**/detector/expose/link |

### 2-2. 비어 있는 것 — 이번 기획의 본체 🔴

| 격차 | 현재 | 문제 |
|------|------|------|
| **중앙 라우터 부재** | `routes/image.py:1118`에 detect만 손으로 `apple_worker.detect()`+폴백 | 새 기능마다 위임 코드 반복. mark/stt/embed는 GPU측 미배선 |
| **정적·이분법 배정** | `delegate=["detect"]` 고정 배열, up/down만 판단 | 워커가 memory/thermal을 보고하는데 **라우팅에 안 씀**. 부하 인식 0 |
| **휴면 상태** | GPU `config.json` 위임 키 `{}` (빈 값) | 코드는 있으나 현재 아무것도 위임 안 됨 |
| **LLM 오프로드 분리** | llama3.2:3b 오프로드(`app.py`)가 워커 위임과 별개 경로 | 라우팅 정책 이원화 — 통합 관리 불가 |
| **제어판 부재** | 무재배포 정책 변경 수단 없음 | imac-link Phase B(쓰기) 미구현 |
| **관측성 부재** | 위임 stats만 인메모리 카운터 | 노드별·capability별 지연/결정 로그·메트릭 없음 → 동적 라우팅 신뢰 근거 부족 |

**결론**: 워커·위임·콘솔이라는 부품은 있으나, 이를 묶는 **(a) capability 레지스트리 (b) 노드 헬스 플레인 (c) 동적 스케줄러 (d) 통합 디스패처 (e) 제어판/관측성**이 없다. 이 5개가 이번 기획이다.

## 3. 목표 아키텍처

```
                       GPU Server (오케스트레이터 호스트)
  호출자 ─API─▶ ┌───────────────────────────────────────────────┐
  (메인웹/ERP/  │  엔드포인트: orchestrator.run(cap, payload,     │
   내부도구)    │              local_fn=…, hints=…)              │
                │        │                                       │
                │        ▼                                       │
                │  ┌─ Scheduler ──────────────────────────────┐ │
                │  │ 1) Capability Registry: 이 cap 처리 가능   │ │
                │  │    노드 후보 = {gpu-local, imac-worker,…}   │ │
                │  │ 2) Node Health Plane: 각 후보 실시간 상태   │ │
                │  │    reachable/caps/mem_pressure/thermal/    │ │
                │  │    inflight/queue/latency(EWMA)/breaker     │ │
                │  │ 3) Score & Pick: 정책가중 + 부하/발열/지연  │ │
                │  │ 4) Circuit Breaker: 연속실패 시 차단·반개방 │ │
                │  └──────────────┬───────────────────────────┘ │
                │     remote │     │ local                       │
                │            ▼     ▼                             │
                │   Node Client   local_fn()  ← 항상 폴백 보장    │
                │   (apple_worker │                              │
                │    일반화)       └─ 결과 + 지연/결정 기록(관측성)│
                └────────────┼──────────────────────────────────┘
                   SSH 터널   │  POST /worker/run/<cap>  (또는 기존 /worker/detect…)
                             ▼
          iMac Worker :8587  ─ capability manifest + worker_guard(부하/발열 가드)
          (+ 향후 N개 노드로 확장 가능 — 노드 레지스트리에 추가만)
```

### 설계 원칙 (기존 기획 계승)
- **GPU 메인 코드 최소 변경**: 엔드포인트는 bespoke 위임 대신 `orchestrator.run(cap, payload, local_fn=…)` 한 줄. 나머지(LLM 서빙·ComfyUI·큐·SSE·인증) 무변경.
- **폴백 절대 보장**: 원격 실패(타임아웃/5xx/토큰/미설정/breaker open) 시 `local_fn` 실행, 예외 전파 금지. 노드 0개 = 기존 동작 100%.
- **Darwin 가드**: iMac 자신은 위임 개시 안 함(`core.device.IS_DARWIN`/`apple_worker.IS_DARWIN` 계승).
- **무상태 워커**: 워커는 큐/DB 없이 동기 처리(긴 작업은 job+콜백). 오케스트레이터 상태는 GPU 인메모리(초기 DB 불필요).
- **제약 존중**: 임베딩은 컬렉션-모델 고정 등 capability별 constraint를 레지스트리가 강제.
- **N-노드 확장성**: 노드는 레지스트리 항목. iMac 1대 → 추후 다대로 코드 변경 없이 확장.

## 4. 핵심 인터페이스 (추상화 우선 — Phase 1의 산출물)

```python
# core/orchestrator/registry.py
@dataclass
class Capability:
    name: str                     # "detect" | "mark" | "stt" | "embed" | "llm_light" | …
    resource: str                 # "light" | "heavy"
    worker_route: str             # "/worker/detect/image"  (원격 호출 경로)
    default_policy: str = "auto"  # "auto" | "prefer_imac" | "prefer_gpu" | "gpu_only"
    constraints: dict = None      # 예: {"embed_model_pinned": True}

# core/orchestrator/nodes.py — 노드 헬스 플레인 (폴링 + EWMA)
@dataclass
class NodeState:
    id: str                       # "gpu-local" | "imac-worker"
    reachable: bool
    capabilities: list[str]
    mem_pressure: str             # normal|warn|critical (워커 health)
    thermal: str
    inflight: int                 # 현재 처리 중
    latency_ewma_ms: dict         # cap별 최근 지연 지수이동평균
    breaker: str                  # closed|open|half_open
    score_penalty: float          # 발열/압박/실패 누적 패널티

# core/orchestrator/scheduler.py
def pick(cap: str, hints: dict|None=None) -> str|None:
    """후보 노드 필터(capability+reachable+breaker+constraint) → 점수화 → 선택.
       적격 원격 없음/전부 부적격이면 'gpu-local'(폴백) 반환, 그것도 없으면 None."""

# core/orchestrator/dispatch.py — 엔드포인트가 쓰는 단일 진입점
def run(cap: str, payload, *, local_fn, hints=None):
    """pick(cap) → 원격이면 Node Client 호출, 실패 시 local_fn() 폴백.
       지연·결정·성공/폴백을 관측성에 기록. 항상 결과를 반환(예외 없음)."""
```

점수 함수(초기 단순화안, 이후 튜닝): `score = policy_weight − w1·norm(inflight) − w2·latency_ewma − gate(mem_pressure≥warn? −∞) − gate(thermal hot? −big)`. 발열/임계압박은 **하드 게이트**(해당 노드 후보 제외), 부하/지연은 소프트 가중. 진동 방지 hysteresis + breaker.

## 5. 구현 범위 (Phase)

### Phase 1: 오케스트레이터 코어 + detect 이전 (v2.84.0) — 추상화 본체
> 목표: 5개 부품 골격 구축 + 유일하게 배선된 detect를 코어 위로 이전(동작 보존 증명)

| # | 항목 | 내용 |
|---|------|------|
| 1-1 | Capability Registry | `core/orchestrator/registry.py` — detect/mark 등록(워커 manifest와 동기) |
| 1-2 | Node Health Plane | `core/orchestrator/nodes.py` — gpu-local + imac-worker, `/worker/health` 폴링(30s 캐시 계승)·EWMA·breaker |
| 1-3 | Scheduler | `core/orchestrator/scheduler.py` — pick() 필터+점수+게이트+hysteresis |
| 1-4 | Dispatcher | `core/orchestrator/dispatch.py` — run(), apple_worker를 Node Client로 일반화(detect/generic) |
| 1-5 | detect 이전 | `routes/image.py:1118` bespoke 위임 → `orchestrator.run("detect", img, local_fn=local_classify)` |
| 1-6 | 관측성 v1 | 노드·cap별 카운터/지연 + 결정 로그, Prometheus 메트릭 노출 |
| 1-7 | 회귀/폴백 검증 | 노드 0개=기존 동작, 워커 차단 시 자동 폴백 0 오류, detect 결과 동일성 |

GPU 변경: image.py 1곳 치환 + 신규 core/orchestrator 패키지(엔드포인트 무관 시 휴면).

### Phase 2: 특화 워커 capability 일반화 (v2.85.0)
> 목표: mark/stt/embed를 "레지스트리 등록 + run() 호출"만으로 편입(bespoke 0)

| # | 항목 | 내용 |
|---|------|------|
| 2-1 | 워커 generic dispatch | `worker_app.py` capability manifest + `/worker/run/<cap>`(또는 기존 라우트 등록) |
| 2-2 | mark 편입 | 이미지 생성 3경로 마킹을 run("mark")로 (iMac 위임 가능화) |
| 2-3 | stt 편입 | apple-silicon-worker Phase 2(mlx-whisper) 구현 시 run("stt") job+콜백, faster-whisper 폴백 |
| 2-4 | embed 편입 | qwen3-embedding 컬렉션-모델 constraint 강제 + run("embed") |

### Phase 3: LLM/범용 작업 통합 (v2.86.0)
> 목표: 분리된 llama3.2:3b 오프로드와 큐/파이프라인을 오케스트레이터로 일원화

| # | 항목 | 내용 |
|---|------|------|
| 3-1 | LLM capability | `llm_light`(3b급→iMac Metal) / `llm_heavy`(GPU 고정). app.py 오프로드 경로를 run()으로 통합 |
| 3-2 | 큐 워커 연동 | 큐 잡 처리 시 모델/작업의 capability로 노드 선택(경량=iMac 후보) |
| 3-3 | 메모리 상호배제 | §apple-silicon-worker §5(STT↔임베딩↔7b 상호배제)를 스케줄러 게이트로 통합 |

### Phase 4: 제어판 + 동적 정책 (v2.87.0) — "쉽게 관리"
> 목표: 무재배포로 역할 배정을 보고/조정하는 통합 컨트롤 플레인(imac-link Phase B 흡수)

| # | 항목 | 내용 |
|---|------|------|
| 4-1 | `GET /api/orchestrator/nodes` | 노드×capability×헬스×최근결정 매트릭스 |
| 4-2 | `POST /api/orchestrator/policy` (localhost) | capability별 정책/가중치/enable·disable, 노드 drain — 무재배포 |
| 4-3 | Admin 콘솔 카드 | imac-link Phase A 상태에 정책 토글·drain·실시간 라우팅 그래프 추가 |
| 4-4 | 정책 튜닝 | 운영 관측 기반 가중치/임계 보정, 진동/오라우팅 점검 |

## 6. 통합 관리("일체형") 산출물
- **단일 상태**: `/api/imac-link/status` + `/api/orchestrator/nodes` (콘솔 카드 1곳)
- **단일 제어**: `/api/orchestrator/policy` 무재배포 정책 변경
- **단일 배포**: 기존 `scripts/deploy_imac.sh` 계승(워커/터널/토큰 일괄)
- **단일 관측**: Prometheus 메트릭 `orchestrator_*{node,capability,decision}`

## 7. 리스크 및 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 동적 스케줄러 복잡도 | 오라우팅·디버깅 난이도 | 초기 단순 점수(EWMA+이진 게이트), 결정 로그 필수, 정책 `gpu_only`로 즉시 무력화 가능 |
| 피드백 진동(노드 왕복) | 지연 변동 | hysteresis + breaker + 최소 체류시간 |
| 발열 throttle iMac에 heavy 배정 | 성능 붕괴 | thermal/mem_pressure 하드 게이트(후보 제외) |
| 터널 단일 장애점 | 위임 실패 빈발 | 폴백 항상 + breaker open 시 즉시 로컬, launchd/watchdog 복구(기존) |
| 관측성 없이 자동신뢰 | 무근거 라우팅 | Phase 1에 관측성 포함, 자동 라우팅은 데이터 확인 후 단계 확대 |
| LLM 라우팅 품질차(3b iMac) | 응답 품질 | `llm_light`만 후보화, heavy는 gpu_only 고정 |
| 임베딩 모델 불일치 | 벡터 오염 | 컬렉션-모델 constraint 강제(워커 검증) |
| 기존 동작 회귀 | 운영 영향 | 노드 0개=무변경 불변식 + 폴백 테스트 + Zero Script QA |

## 8. 공수 산정

| Phase | 내용 | 공수 |
|-------|------|------|
| 1 | 코어(레지스트리/헬스/스케줄러/디스패처) + detect 이전 + 관측성 | 3일 |
| 2 | 워커 generic + mark/stt/embed 편입 | 2.5일 |
| 3 | LLM/큐 통합 + 메모리 상호배제 | 2일 |
| 4 | 제어판 + 콘솔 + 정책 튜닝 | 2일 |
| 검증/문서 | QA + 릴리스 노트 + ARCHITECTURE/CROSS_ARCH 갱신 | 1.5일 |
| **합계** | | **11일** |

## 9. 의사결정 기록

| 결정 | 선택 | 근거 |
|------|------|------|
| 범위 | 범용 작업 전체 | 사용자 결정(2026-06-08) — capability 레지스트리로 확장형 설계 |
| 라우팅 | 완전 동적 스케줄링 | 사용자 결정 — 실시간 부하/발열/지연 기반, 단 하드 게이트로 안전 |
| 착수 | 중앙 오케스트레이터부터 | 사용자 결정 — 추상화 먼저, 기존 detect로 검증 |
| 폴백 계약 | None→local 유지·중앙화 | 기존 apple_worker 계약 계승 = 무위험 점진 이전 |
| 상태 저장 | GPU 인메모리(초기) | 무상태 워커 원칙, DB는 관측 영속화 필요 시 후속 |
| 노드 모델 | 레지스트리 기반 N-노드 | iMac 1대지만 확장 대비 |

## 10. 제외 범위
- iMac 단독 전체 API Failover(별도 기획)
- 멀티 iMac 물리 증설(설계는 N-노드 대비, 실증은 장비 확보 후)
- 외부 공인 노출(모드 B-②, gpu-imac-link L6) — opt-in 보류 유지
- 이미지 생성 이관(ComfyUI GPU 전담 유지)

## 11. 선행 조건
- gpu-imac-link 터널(L1~L5) 활성화 + GPU `config.json` 위임 키 설정(현재 휴면 해제)
- apple-silicon-worker Phase 2/3(stt/embed) 구현은 node-orchestrator Phase 2-3와 일정 동기

## 12. 배포 모델 — 노드 독립 배포 / 버전 스큐 안전성

GPU와 iMac은 **완전 독립 배포**다. 같은 레포를 각자 pull하고, 각자의 환경/실행 단위로 배포한다.

| | GPU 서버 | iMac |
|---|---|---|
| 가상환경 | `venv` + `requirements.txt` | `venv-mac` + `requirements-mac.txt` |
| 배포 | `git pull` + systemctl / SIGHUP reload | `scripts/deploy_imac.sh` + launchd |
| 실행 | `app.py` (오케스트레이터 호스트) | `worker_app.py` :8587 (워커) |

### 버전 스큐 안전성 (배포 순서 무관)
1. **Capability manifest가 진실 공급원**: GPU는 iMac이 무엇을 할 수 있는지 `/worker/health`의
   `capabilities`로 매 폴링 확인 → 워커에 없는 cap은 스케줄러가 후보에서 제외하고 GPU 로컬 폴백.
2. **버전 스큐 무해**: GPU가 iMac에 없는 라우트를 호출해도 404 → 폴백. 어느 쪽을 먼저 배포해도 안전.
3. **노드 0개 = 기존 동작 100%**: iMac이 꺼져 있어도 전부 로컬 처리(불변식).

### 별도 배포라도 동기화 필요 2가지 (배포 순서와 무관)
- **`WORKER_TOKEN` 일치**: iMac env ↔ GPU `config.json:apple_worker_token` (gpu-imac-link L7)
- **터널 가동**: 위임은 SSH 터널(L3) 위에서만. 없으면 자동 로컬 폴백(무오류)

### 권장 배포 순서 (안전하지만 매끄러운 순서)
1. **iMac 먼저** — 새 capability 배포 → manifest에 노출
2. **GPU 나중** — 오케스트레이터가 그 cap을 `run()`으로 호출

> 반대 순서여도 폴백으로 안전(잠깐 로컬 처리가 섞일 뿐).

### iMac 가동 시 활성화 도구
`scripts/activate_imac_delegation.sh` — GPU 서버에서 1커맨드로 위임 **활성화/검증/해제**:
config.json 안전 편집(백업+JSON 파서, 수동 편집 실수 방지) + 터널·워커 헬스 + 위임 스모크 + 폴백
검증 + 롤백(`--disable`). iMac이 올라오면 토큰만 주면 즉시 가동. (오케스트레이터 Phase 1 도입
후에는 동일 스크립트가 `--delegate`로 capability 목록을 확장.)
```
