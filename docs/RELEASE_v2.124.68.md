# v2.124.68 — heavy tier 활성화 (qwen3.6:35b-a3b) · 대형 모델 서빙 3트랙 판정

> 2026-08-03 · 코드 변경 1파일(주석·기본값) · config 활성화 · 문서 정정 4건

## 헤드라인

**대형 모델 서빙 경로 3개를 하루 동안 실측으로 파본 끝에, 이미 갖고 있던 heavy tier가 답이었다.**
`heavy_enabled=true` · `heavy_model=qwen3.6:35b-a3b` 활성화(라이브 검증 완료).

## 1. 채택 근거 — grounded 2회 독립 재현

| 측정 | qwen2.5:14b(상주) | **qwen3.6:35b-a3b** | Δ |
|------|-----------------:|-------------------:|---:|
| v2.124.4 (N=40, 2026-07) | 0.908 | 0.931 | +0.023 |
| **2026-08-03 (N=20, 다른 하네스)** | 0.878 | **0.946** | **+0.068** |
| 품질(G-Eval, 오늘) | 4.138 | **4.338** | +0.20 |
| 지연 | 5.8초 | 25.4초 | **4.4배** |

3개월 간격·다른 하네스로 같은 방향. 지연 4.4배는 heavy tier 본래 용도(비실시간 심층 자문)에서 수용 가능.

**라이브 검증**: `POST /api/chat/heavy` → 13.2초 · 상주 자동 복원(`running:false`) · health true.
답변 *"의료법 제21조 및 동법 시행령 제33조에 따라 진료기록부(전자문서 포함)는 그 기록 완료일로부터 5년간 보존하여야 합니다."* — 조문·시행령·기산점·기간 정확.

## 2. 탈락한 3트랙 (전 과정 `docs/01-plan/features/airllm-integration.plan.md` 23절)

| 트랙 | 판정 | 결정적 근거 |
|------|------|------------|
| **AirLLM 72B** | 종료 | KorMedMCQA **78.25%**(상주 대비 +10.5%p, p=0.00001)로 품질은 최고였으나 **0.05 tok/s**(200토큰 64분)·**`ollama-admin` 중지 필요**(배치마다 서비스 중단) |
| **llama.cpp MoE** | 종료 | Ollama가 **이미 58.4 tok/s**(동일 모델). `-ncmoe 48`은 40.6 tok/s로 더 느리고, **`qwen3.6`·`gpt-oss` 로드 불가**(Ollama 자체 지원 아키텍처) |
| **qwen3:14b 상주 교체** | NO-GO | MCQA **+5.0%p**(p=0.048)였으나 **grounded 충실도 −0.034** · 지연 3배(thinking) |

## 3. 🔑 교훈 — MCQA가 두 번 다 틀렸다

MCQA에서 이긴 두 모델(qwen3:14b·Qwen2.5-72B)이 **전부 탈락**하고, MCQA를 재지 않은 qwen3.6이 채택됐다.
v2.112.0의 *"MCQA=파라메트릭 지식이고 프로덕션은 RAG-grounded라 즉시 모델교체 금지"*가 같은 날 두 번 실증.
**파라메트릭 벤치는 후보 선별용이지 판정 근거가 아니다.**

또 하나: 제약 디코딩에서 **생성 첫 토큰을 정답 토큰으로 강제**했더니 72B가 51%, 챗 템플릿 + `{"answer": ` 리드인으로 **숫자가 올 자연스러운 위치**를 만들자 78% — **같은 모델·같은 문항에서 27%p**. 14B는 우연히 강건해 "하네스 동등"이라는 잘못된 안심을 줬다. **한 모델에서 검증한 하네스를 다른 모델에 그대로 믿지 말 것.**

## 4. 변경

- `routes/heavy.py` — `heavy_model` 기본값 `qwen3.6:35b-a3b` 재확정 + **왜 이 모델인지**(재현 수치·타 트랙 탈락 사유·thinking 주의) 주석
- config(라이브) — `heavy_enabled=true`
- `docs/DESIGN_MODEL_SERVING.md` — 🔴 **디스크 대역 정정**: 기준선 3.1GB/s는 `O_DIRECT` 단일 스레드 값이고 **버퍼드 순차 읽기는 9.9GB/s**(PCIe 5.0 SK hynix P51). 대역 기반 추정에 3.1을 쓰면 3배 과소평가
- `LICENSE` — Qwen 계열 라이선스 정정: **Qwen2.5의 72B·3B는 Apache-2.0이 아니라 Qwen LICENSE**(MAU 1억 초과 시 별도 계약·"Built with Qwen" 표기). 계열 단위로 등급을 매기면 안 된다
- `README.md`·`CLAUDE.md` — 모델 목록·수 정합(디스크 정리 103GB 회수 반영)

## 5. 🔴 잔여 과제

**RAG 경로에서 qwen3.6을 못 쓴다.** `/api/medical-law/ask`에 `model=qwen3.6:35b-a3b`를 주면 **빈 응답(길이 0)** — `routes/medical_law.py:194` `_generate`가 `think`를 전달하지 않아 thinking 모델이 추론만 하고 끝난다.

| 경로 | 상태 |
|------|------|
| `/api/chat/heavy` | ✅ `think=false` 주입돼 있음 — 즉시 사용 가능 |
| RAG 경로(`/api/medical-law/ask`·`enhanced`) | 🔴 **코드 변경 필요**(v2.124.59 패턴: `/api/show` capabilities 확인 후 주입) |

그 외: `core/cache.py:70` 서로게이트 `UnicodeEncodeError` 500(**1,726건** 실측) — `.encode("utf-8","surrogatepass")` 1줄 + 회귀 테스트.

## 6. 보존 자산

- `docs/01-plan/references/airllm-72b-measurement-report.md` — AirLLM 측정 리포트(게이트·구동 시간 비교)
- 서버 `~/airllm_bench/` — 문항별 정오 벡터·타이밍·자원 원자료
- **기준선**: KorMedMCQA qwen2.5:14b **67.75%** · qwen3:14b **72.75%** · Qwen2.5-72B **78.25%**
- 디스크 정리 **103GB 회수**(여유 220→316GB): Ollama 모델 3종 42GB + aux 비전 R&D 잔재 64GB
