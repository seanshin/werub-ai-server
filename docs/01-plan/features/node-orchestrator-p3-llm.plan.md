# Node Orchestrator Phase 3 — LLM/큐 오케스트레이션 일원화

> **Feature**: node-orchestrator-p3-llm | **Created**: 2026-06-08
> **상태**: 기획 (Phase 2 STT 활성화 대기 중 작성)
> **선행**: gateway 루프 가드(§4, 필수) / Phase 2 패턴 확립(완료)
> **Related**: node-orchestrator(Phase1), -p2-stt, ORCHESTRATOR_GATEWAY_BOUNDARY

## 1. 목적

현재 **분리된 2개 오프로드 경로를 오케스트레이터로 일원화**해 정책·관측·폴백·콘솔을 통합한다.

| 경로 | 현재 | 문제 |
|---|---|---|
| LLM offload | 큐 워커가 `LIGHTWEIGHT_MODELS={llama3.2:3b}` → iMac **Ollama :8586** 직접 (`app.py:4673,4702`) | 모델명 하드코딩, orchestrator 비경유, breaker/메트릭/콘솔 없음 |
| 특화 capability | orchestrator → iMac **worker :8587** (detect/stt) | LLM과 별개 |

→ LLM 추론을 **capability(`llm_light`/`llm_heavy`)로 흡수**, 큐가 `dispatch.run`을 쓰게 한다.

## 2. 현황 분석 (코드 실측)

- `app.py:3675` `IMAC_OLLAMA_BASE=http://localhost:8586` (iMac Ollama, **워커 8587과 다른 포트/엔드포인트**)
- `app.py:3676` `LIGHTWEIGHT_MODELS={"llama3.2:3b"}` — 큐 잡 모델이 이 집합이면 iMac Ollama로
- `app.py:4673/4702` 큐 단건/배치 워커: `if model in LIGHTWEIGHT_MODELS and _is_imac_available(): _route_base=IMAC_OLLAMA_BASE`
- orchestrator 노드 모델: 현재 `imac-worker`(:8587) 1개. **Ollama(:8586)는 별도 노드/엔드포인트 필요**
- LLM은 **스트리밍(NDJSON/SSE)** 흔함 — `/api/generate`, `/api/chat`. orchestrator `dispatch.run`은 동기 dict.

## 3. 핵심 설계 결정

### 3-1. 노드 모델 — 노드당 다중 엔드포인트 OR 노드 분리
LLM은 iMac **Ollama :8586**, detect/stt는 iMac **worker :8587**. 두 가지 안:
| 안 | 내용 | 평가 |
|---|---|---|
| **A. 노드 분리** | `imac-worker`(:8587) + **`imac-ollama`(:8586)** 별도 노드 | 명확. capability가 노드를 지정. health도 각각. **권장** |
| B. capability별 endpoint | 한 노드에 cap별 url 오버라이드 | 노드 헬스/부하 모델이 흐려짐 |

→ **A 채택**: `config/orchestrator.json`에 `imac-ollama` 노드 추가(url=8586, health=Ollama `/api/tags`). llm_* capability는 후보 노드로 imac-ollama 사용. 단 메모리 상호배제는 **두 노드가 같은 물리 iMac**임을 스케줄러가 인지해야 함(§3-4).

### 3-2. capability 설계
| capability | 대상 모델 | policy | 비고 |
|---|---|---|---|
| `llm_light` | 3b/2b/1.5b급 (config `llm_light_models`) | auto/prefer_imac | iMac Metal 22 tok/s 실용(벤치 §7-0) |
| `llm_heavy` | 7b+/reasoning | **gpu_only** | M1 7b 11 tok/s — 배치 외 비권장 |
- 모델→capability 매핑은 config(`LIGHTWEIGHT_MODELS` 대체). worker_route = Ollama 호환(`/api/generate`,`/api/chat`).

### 3-3. ⚠️ Gateway 루프 가드 (선행 필수 — BOUNDARY §4)
Smart Gateway(iMac→GPU 복잡 chat) + llm_light(GPU→iMac) = **iMac→GPU→iMac 루프**.
- gateway가 GPU 포워딩 시 헤더 `X-Routed-By: gateway` 부여
- orchestrator: 해당 origin 요청의 `llm_*`는 **gpu_only 강제**(재위임 차단) — `dispatch.run(..., hints={"origin":"gateway"})` → scheduler가 gpu_only 처리
- detect/stt/embed는 chat 아님 → 무영향

### 3-4. 메모리 상호배제 (iMac 16GB Unified)
STT(~3.5GB)/embed(~4.7GB)/7b(~5GB)/3b(~2GB) 동시 적재 시 jetsam 위험(실측: Ollama 점유 시 detect MPS SIGKILL → cpu 전환).
- worker_guard `heavy lock`: STT/embed가 heavy 점유 시, **llm_light(3b)도 스케줄러가 회피**하도록 게이트 확장 필요(현재 heavy cap만 게이트)
- 규칙: 같은 iMac에서 heavy(STT/embed/llm_heavy) 진행 중 → 신규 무거운 위임 거부, 3b는 mem_pressure 기반 소프트 회피
- `imac-ollama`와 `imac-worker`의 부하를 **합산**해 판단(같은 물리 노드) — 스케줄러에 노드 그룹(physical_host) 개념 추가

### 3-5. 스트리밍 — 단계 분할
- **Phase 3a (비스트리밍 우선)**: 큐 잡(배치, 동기 응답) LLM만 위임. `dispatch.run("llm_light", {model,prompt})` → 비스트림 dict. 기존 `LIGHTWEIGHT_MODELS` 큐 라우팅 대체.
- **Phase 3b (스트리밍, 후속)**: `/api/generate|chat` 스트림 위임 — 워커/Ollama 스트림을 GPU가 프록시→클라 중계. dispatch 스트리밍 확장 필요(제너레이터 반환). 복잡 → 별도.

### 3-6. 큐 통합
- 큐 워커(`app.py:4673/4702`)의 `LIGHTWEIGHT_MODELS` 분기 → `dispatch.run(cap, payload, local_fn=<기존 GPU Ollama 호출>)`로 치환
- cap 판정: 잡 model → `llm_light`/`llm_heavy` (config 맵). 휴면 시(노드 미가용) 기존 GPU 처리 100% 동일

## 4. 구현 범위 (Phase 3a)

| # | 항목 | 파일 |
|---|---|---|
| 3a-1 | `imac-ollama` 노드 + Ollama 헬스(`/api/tags`) + physical_host 그룹 | core/orchestrator/nodes.py, config |
| 3a-2 | `llm_light`/`llm_heavy` capability + 모델맵 | config/orchestrator.json |
| 3a-3 | 노드 그룹 메모리/heavy 합산 게이트 | scheduler.py |
| 3a-4 | 루프 가드(origin=gateway → gpu_only) | scheduler.py, dispatch hints |
| 3a-5 | 큐 워커 LIGHTWEIGHT_MODELS → dispatch.run 치환(휴면 호환) | app.py |
| 3a-6 | client: Ollama 비스트림 호출(payload_kind=ollama) | client.py |
| 3a-7 | 테스트 + 콘솔(이미 llm 카운트 표시됨) | tests/, 콘솔 |

## 5. 휴면/활성화
- 동일 패턴: capability/노드 선언 + 호출부 dispatch + 노드 미가용 시 기존 GPU 처리.
- 활성화: `imac-ollama` 노드 헬스 통과 + delegate에 llm_light + (gateway 가드 배포 후).

## 6. 리스크
| 리스크 | 대응 |
|---|---|
| **gateway 루프** | §3-3 origin 가드 — **Phase 3 머지 전 필수** |
| 스트리밍 복잡도 | 3a는 비스트림(큐)만, 3b 후속 |
| iMac 메모리 경합(LLM+STT+embed) | 노드그룹 합산 게이트 + heavy lock 확장 |
| 3b 모델 품질차 | llm_light는 경량모델만, heavy=gpu_only |
| 8586 터널/Ollama 가용성 | 폴백=GPU Ollama(기존), breaker |
| 기존 큐 동작 회귀 | 노드 미가용=기존 100% 불변식 + 휴면 머지 |

## 7. 불변식 (Phase 1~2 계승)
노드 미가용=기존 동작 100% / 폴백 무전파 / Darwin 가드 / heavy 상호배제 / **루프 차단**(신규).

## 8. 의사결정 기록
| 결정 | 선택 | 근거 |
|---|---|---|
| LLM 노드 | imac-ollama(:8586) 별도 노드 | worker(:8587)와 엔드포인트/프로토콜 상이 |
| 물리 호스트 인지 | 노드 그룹(physical_host=imac) | 8586/8587 부하 합산 — 16GB 공유 |
| 스트리밍 | 3a 비스트림 우선 | 위험·복잡 격리, 큐 효과부터 |
| 루프 | origin 헤더 가드 | gateway 양립 보장 |
| heavy 정책 | llm_heavy=gpu_only | M1 7b 비실용(벤치) |
