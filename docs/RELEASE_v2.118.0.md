# RELEASE v2.118.0 — Heavy Tier (하이브리드 대형 모델 on-demand 구동)

**날짜**: 2026-07-05
**헤드라인**: 16GB VRAM에 안 들어가는 **대형 모델을 GPU VRAM + 시스템 RAM 하이브리드(부분 오프로드)로
on-demand 구동**하는 Heavy Tier 신설. 상주 fast-lane(qwen2.5:14b)과 **완전 분리**된 별도 경로.
기본 opt-in(`heavy_enabled=false`=기존 동작 100% 불변).

---

## 배경

하이브리드 대형 모델 기획(`docs/01-plan/features/hybrid-large-model-inference.plan.md`)의 PoC에서
**메커니즘은 검증**됐다(MoE >16GB를 Ollama 자동 레이어 오프로드로 56 tok/s 구동, 무증설). 품질은
현 후보(30B급)에서 상주 14B 대비 이득이 없었으나, **구동 기능 자체**는 향후 모델·RAM 증설 시
즉시 활용할 인프라로서 가치가 있다(사용자 요청). 이를 프로덕션 기능으로 구현한다.

## 설계

- **상주 fast-lane 불변**: `config.default_model`(qwen2.5:14b, keep_alive=-1)은 그대로. Heavy는
  MAX_LOADED=1 제약상 상주를 **일시 축출** → 응답 후 **상주 자동 복원**(fast-lane 보호).
- **하이브리드 오프로드**: 16GB 초과 모델은 Ollama가 레이어를 GPU/CPU RAM에 자동 분할(검증됨).
  `heavy_num_gpu`로 GPU 레이어수 수동 고정(수동 오프로드)도 가능.
- **품질 우선·속도 비이슈 tier**: 하이브리드는 느림(대형 dense 50~88s/문항). on-demand 전용.
- **single-flight**: 동시 heavy 2개 = 16GB 초과 OOM/RAM 스래싱 → 락으로 1개만 실행(409).

## 신규 API (`routes/heavy.py`)

| # | Method | Endpoint | 설명 |
|---|--------|----------|------|
| 1 | POST | `/api/chat/heavy` | 하이브리드 대형 모델 채팅(스트림/비스트림). 응답 후 상주 복원 |
| 2 | GET | `/api/heavy/status` | config + 로드 모델(VRAM/CPU 오프로드%) + 실행중 여부 |
| 3 | POST | `/api/heavy/settings` | 토글/모델/keep_alive/num_gpu (admin) |

```bash
# heavy 활성화(로컬)
curl -X POST http://localhost:8585/api/heavy/settings \
  -H "Content-Type: application/json" -d '{"heavy_enabled":true,"heavy_model":"qwen3:30b-a3b"}'

# 대형 모델로 질의(상주 축출→응답→상주 복원)
curl -N -X POST http://localhost:8585/api/chat/heavy \
  -H "Content-Type: application/json" \
  -d '{"prompt":"복잡한 추론 질문","stream":true}'

# 상태
curl http://localhost:8585/api/heavy/status
```

## Config 키 (전부 opt-in)

- `heavy_enabled` (bool, 기본 **false**) — 비활성 시 `/api/chat/heavy` 400, 기존 동작 불변
- `heavy_model` (str, 기본 `qwen3:30b-a3b`) — per-request `model`로 오버라이드 가능(medgemma:27b 등)
- `heavy_keep_alive` (str, 기본 `"0"`) — 응답 후 언로드→상주 복원. `"5m"`=warm 유지(연속 질의)
- `heavy_num_gpu` (int|null, 기본 null=자동 분할) — GPU 레이어수 수동 고정(하이브리드 제어)

## Admin
- Dashboard "Heavy Tier" 카드 — 활성·heavy 모델·실행중/상주·로드 모델 VRAM/CPU 오프로드%. 5분 폴링.

## 검증
- 로컬 py_compile(heavy.py·app.py)·GPU pytest `tests/test_heavy.py` **10 passed**(게이트·입력검증·
  비스트림+상주복원·스트림 패스스루·single-flight 409·num_gpu 주입·status·settings).
- graceful HUP(NRestarts 0)·health 200·라이브 E2E(heavy 활성→대형 모델 질의→상주 복원).

## 영향 / 하위호환
- 신규 엔드포인트 3 + Admin 카드 + 테스트 10. 기존 API·상주 fast-lane 무변경.
- `heavy_enabled=false`(기본) 시 `/api/chat/heavy`는 400만 반환 — 프로덕션 동작 100% 불변.
- ⚠️ heavy 실행 중엔 상주 fast-lane이 일시 축출됨(하이브리드 tier의 본질). 응답 후 자동 복원.
