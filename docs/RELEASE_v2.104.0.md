# Release v2.104.0 — 컨시어지 응답속도 보완

**날짜:** 2026-06-17
**대상:** hospital.example.org AI 컨시어지(홈페이지 예약 챗봇) 응답속도 저하 진단·보완
**GPU HEAD:** 60837dc (본 릴리즈 문서 커밋 별도)

---

## 배경

hospital.example.org 홈페이지 AI 컨시어지가 기존 운영 대비 응답속도가 매우 떨어진다는 보고. AI 서버측 검토·보완.

**호출 체인(HIS `Hospital RUN` 코드 추적으로 확정):**
홈페이지 `booking/page.tsx` → `/api` 프록시 → HIS booking(`@Controller('api/v1/booking')`, agent-session) → AI 서버.

| 컨시어지 단계 | HIS 메서드 | 실제 AI 서버 엔드포인트 |
|---|---|---|
| message (매 메시지 1차응답) | `orchestrate`/`chatWithSession` | `/api/chat` (`stream:false`+`quality_check:true`) |
| enhanced (증상성 정밀분석) | `enhancedChat` | **`/api/chat/enhanced`** (domain=medical) |

> ⚠️ 컨시어지는 `/api/orchestrator/chat`·`/api/medical/reasoning/ensemble`을 **사용하지 않음**. (초기 추정 오류 정정 — 응답 형식만 보고 매핑했다가 빗나감. 호출부 코드 확인 후 정정.)

## 근본 원인

`/api/chat/enhanced`(medical)는 **werub-medical:7b → med42-8b → werub 3단계 파이프라인**:
- 단계마다 **모델 스왑 2회**. 16GB VRAM에 상주 채팅모델 qwen2.5:14b(~12GB) 존재 → 매 호출마다 **상주 모델 축출**.
- 비스트림(3단계 완료까지 블롭 대기).
- enhanced가 돌 때마다 상주 채팅모델이 밀려나 → **message 경로(/api/chat 동일 qwen)·동시 트래픽까지 같이 느려짐**.

## 변경 사항

### 1. `/api/chat/enhanced` — single_model (모델 스왑 제거)
응답 shape 보존, 기본값=현행 무변경(opt-in).
- `single_model: "<모델명>"` → 전 단계 단일 모델. 예 `"qwen2.5:14b"` = 상주모델 재사용 → **로드 0·축출 0**.
- `single_model: true` → 파이프라인 1단계 모델로 전 단계(medical=werub-medical:7b, 로드 1회).
- 미지정/false → 단계별 전문모델(기존 스왑 동작).

### 2. `/api/chat/enhanced` — depth=fast (단계 축소)
- `depth: "fast"` → `ENHANCED_CHAT_PIPELINES_FAST`. medical 3→**2단계**(영어 med42 제거, werub 증상+감별진단 통합 → 한국어 종합+면책, 간결 프롬프트). coding/legal/writing 1단계. 미정의 도메인 full 폴백.
- `depth: "full"`(기본) → 기존 파이프라인.
- `single_model`과 조합 가능.

### 3. 부수 — 임상의용 web 화면 대상(컨시어지 미사용 엔드포인트)
- `/api/medical/reasoning/ensemble` `chain_mode="single"`(스왑 제거)·`depth="triage"`(5→3단계) + **`/api/medical/reasoning/ensemble/stream` 신설**(단계별 SSE).
- `/api/orchestrator/chat/stream` tool-calling 폴백 **증분 스트리밍 결함 수정**(`list()` materialize 제거 → TTFT 6.5s→0.0016s).
- `apps/web`의 emergency/encounter/AiChatBar가 orchestrator·reasoning을 사용하므로 그쪽엔 유효.

## 라이브 실측 (`/api/chat/enhanced`, domain=medical, `ollama ps` 전후 검증)

| 구성 | wall | 단계 | 스왑 | 상주 qwen 축출 |
|---|---|---|---|---|
| full default (werub→med42→werub) | 17.6s | 3 | 2 | 매 호출 |
| single_model="qwen2.5:14b" | ~17s | 3 | 0 | 없음 |
| depth=fast | 6.2s | 2 | 0 | 있음(werub) |
| **depth=fast + single_model="qwen2.5:14b"** | **4.9s** | 2 | 0 | **없음** |

→ **17.6s → 4.9s (~72%↓) + 스왑0 + 상주모델 축출0**.

## 핵심 결론

- **단축 레버는 단계 축소**(depth=fast). 스왑 제거 자체는 wall time을 거의 줄이지 못함(3단계 순차 생성이 시간 지배). 단 **스왑/축출 제거는 "서버 전체가 같이 느려짐" 해소에 결정적**(상주 채팅모델 유지 → message·동시 트래픽 무영향).
- ⚠️ trade-off: fast=2단계·간결(answer 짧음·영어 differential 생략) → 컨시어지 트리아지엔 적합, 상세 임상분석은 full. 용도 기준 의료팀 결정.

## HIS측 적용 (이게 있어야 효과 — 잔여, AI 서버 작업 아님)

`Hospital RUN` `apps/api/src/modules/medical-ai/medical-ai.service.ts`:
- `enhancedChat` body에 `depth:"fast", single_model:"qwen2.5:14b"` 추가 → **4.9s·축출0**.
- message: `/api/chat`의 `quality_check:true`(비스트림+재시도)·모델 미지정 시 medical 도메인→werub 스왑. `model:"qwen2.5:14b"` 지정(스왑0)+가능하면 `stream:true`(TTFT↓).

## 호환성 / 위험

- 전부 **opt-in 파라미터(기본값=기존 동작)**. multi/consensus/full 무변경.
- 응답 shape 동일(`answer`·`stages`·신규 `depth`·`single_model` 에코) → HIS 파싱 변경 불요.
- 문법훅 212 통과. 라이브 배포·재기동(HUP) 후 health 200·NRestarts 0.

## 커밋

| 커밋 | 내용 |
|---|---|
| b9b17f5 | ensemble/stream 신설 + chain_mode=single |
| 24ede97 | ensemble depth=triage |
| d87f973 | /chat/stream tool-calling 증분스트림(list() 결함 수정) |
| 8225fce | **/api/chat/enhanced single_model** |
| 60837dc | **/api/chat/enhanced depth=fast** |

## 참고

- 하드웨어: RTX 5090(32GB) 도입 시 — 생성속도 ~1.8~1.9x(대역폭) + 32GB로 멀티모델 동시상주 가능 → 스왑/축출 병목 자체 소멸(우회책 불요, full 멀티모델도 ~8~9s·서버 경합 없음). 추정치, 실측 시 동일 스크립트로 확인.
