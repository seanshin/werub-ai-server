# Node Orchestrator 가이드 — GPU↔iMac 자동 분산 (v2.85.0)

> 설계: `docs/02-design/features/node-orchestrator.design.md` | Core: `core/orchestrator/`

## 1. 한 줄 요약

**호출자는 GPU 서버 하나만 호출한다.** 오케스트레이터가 작업 capability와 노드 실시간 상태를 보고
최적 노드(GPU 로컬 / iMac 워커)에 자동 배정하고, 실패 시 GPU 로컬로 폴백한다. iMac을 알 필요 없고
호출 코드/URL은 기존과 동일하다.

## 2. 호출 방법 (변경 없음)

```bash
# 내부/localhost (인증 불필요)
curl -X POST http://localhost:8585/api/detect/image -F 'image=@x.png'

# 외부 (Bearer 키)
curl -H "Authorization: Bearer <API_KEY>" \
  https://example.org/ollama/api/detect/image -F 'image=@x.png'
```

응답의 `ml.engine` 으로 어디서 처리됐는지 확인:
- `"imac-worker"` → iMac 위임 처리
- `"gpu-local"` → GPU 로컬 처리(폴백 또는 미위임)

그 외 응답 스키마는 동일. 호출자는 engine 필드를 무시해도 된다.

## 3. 자동 분산 범위 (현재)

| capability | 상태 | 비고 |
|---|---|---|
| `detect` (AI 판별) | 🟢 **자동 위임 LIVE** | engine=imac-worker, 폴백 보장 |
| `mark` | policy=prefer_imac, **미배선** | 마킹은 ~ms라 네트워크 오프로드 손해 → 보류 |
| `stt` | 🟢 **자동 위임 LIVE**(v2.86.0) | 회의 분석 STT가 iMac으로(engine=imac-worker)·v2.124.40이 위임 키 불일치 버그 봉합 |
| `embed` | ⬜ Phase 3 | iMac 워커 embed capability 구현 후. 스케줄러 가드 `_model_ok`는 **fail-closed**(v2.125.19) — 지금 켜도 GPU 로컬로만 감·위임하려면 실검증(worker `embed_models`·컬렉션-모델 맵) 구현이 선행 |
| LLM/이미지생성/기타 543 API | GPU 직접 처리 | 오케스트레이터 비경유(단일 진입점은 동일) |

## 4. 라우팅 상태 조회

```bash
curl http://localhost:8585/api/orchestrator/nodes
```
```json
{
  "capabilities": {"detect": {"resource":"light","policy":"prefer_imac","worker_route":"/worker/detect/image"}, ...},
  "nodes": {
    "gpu-local":   {"kind":"local","reachable":true,"breaker":"closed","inflight":0, ...},
    "imac-worker": {"kind":"remote","reachable":true,"capabilities":["detect","mark"],
                    "mem_pressure":"normal","thermal":"nominal","breaker":"closed",
                    "latency_ewma_ms":{"detect":868}, ...}
  },
  "metrics": {"counts": {"detect|imac-worker|delegated": 12, "detect|gpu-local|local": 3}, "recent":[...]},
  "darwin_guard": false
}
```
Admin 대시보드 **오케스트레이터 카드**에서도 동일 정보를 30s 폴링으로 시각화(신호등/지연/실적).

## 5. 토폴로지

```
호출자 ─→ GPU :8585 (오케스트레이터/메인)
              │ dispatch.run("detect")  [prefer_imac]
              ▼
   GPU localhost:8587 ─(ollama-worker-bridge.service, -L 8587)→ prod:8587
                          ─(iMac 워커 -R 8587)→ iMac worker :8587 (CPU 추론)
   역방향(콜백): iMac -L 11435 → GPU :8585
```
- **메인 = GPU 서버** (위임 OUT). **iMac = 워커**(위임 IN, Darwin 가드로 자기 위임 안 함).
- 허브 = prod(192.0.2.108). GPU·iMac 모두 prod로 터널.

## 6. 활성화 (iMac 워커 가동 후, GPU에서)

```bash
# GPU 서버에서
cd /home/deploy/WeRUBLLMManager
scripts/activate_imac_delegation.sh --token-file ~/.werub_worker_token   # 또는 --token <값>
#   → config.json apple_worker_enabled/url/token/delegate 설정 + 워커 헬스·스모크 검증
#   → 재시작 불필요 (config는 호출마다 동적 로드)

# 상태 점검 / 롤백
scripts/activate_imac_delegation.sh --verify
scripts/activate_imac_delegation.sh --disable     # 즉시 로컬 경로 복귀
```

전제: iMac 워커(:8587) 가동 + 영구 브릿지(`ollama-worker-bridge.service`) + iMac→prod 역터널.

## 7. 정책 (config/orchestrator.json)

```json
"detect": { "resource":"light", "worker_route":"/worker/detect/image",
            "payload_kind":"image", "policy":"prefer_imac" }
```
- `policy`: `auto`(동적, 동률 시 로컬 우선) | `prefer_imac`(offload 의도) | `prefer_gpu` | `gpu_only`(위임 봉쇄 킬스위치)
- 변경 후 `POST /api/orchestrator/registry/reload` (localhost, 무재배포). mtime 기반 자동 리로드도 동작.

## 8. 안전장치 (불변식)

- **노드 0개 = 기존 동작 100%** (config 미설정/미도달 → 전부 gpu-local)
- **폴백 예외 전파 금지** — 원격 실패(타임아웃/5xx/breaker)·로컬 예외 모두 가드
- **하드 게이트**: 미도달 / capability 없음 / breaker open / mem critical / thermal throttled / heavy lock → 후보 제외
- **detector.available 게이팅**: 워커 ML 다운 시 detect를 caps에서 제외 → breaker 트립 churn 방지
- **circuit breaker**: 연속 실패 시 open(30s) → 자동 half-open 프로브 → 성공 시 close
- **Darwin 가드**: iMac은 remote 위임 개시 안 함(자기 호출 루프 방지)

## 9. 운영 노트

- **GPU 코드 반영 = 전체 재기동** (`preload_app=True`라 SIGHUP은 코드 reload 안 함):
  마스터 graceful TERM → systemd `Restart=always` 재기동, 또는 `sudo systemctl restart ollama-admin`.
  재기동 전 `venv/bin/python -c "import app"` import dry-run 권장(preload 크래시 방지).
- **iMac 워커 운영**(워커측): `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`(gunicorn fork ObjC 크래시 방지),
  `DETECTOR_DEVICE=cpu`(Ollama가 wired 메모리 점유 시 MPS jetsam SIGKILL 회피; Ollama 언로드 시 mps 가능).
- **메트릭**: `metrics.snapshot()` / `/api/orchestrator/nodes` / Admin 콘솔 카드.

## 10. 로드맵

| Phase | 내용 | 버전 |
|---|---|---|
| 1 (완료·가동) | 코어 + detect 자동 위임 + 콘솔 + 하드닝 | v2.84~2.85 |
| 2 | 워커 `/worker/run/<cap>` generic + mark/stt/embed 편입 | v2.86 |
| 3 | LLM/큐 일원화 + 메모리 상호배제 (⚠️ 착수 전 Smart Gateway 루프 가드 필수 — `docs/ORCHESTRATOR_GATEWAY_BOUNDARY.md` §4) | — |
| 4 | 제어판(정책 토글/drain) + 동적 정책 튜닝 | — |
