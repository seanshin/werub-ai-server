# Model Card — werub-medical:7b (자체 파인튜닝 v1)

**v1.1** 2026-07-19 | 식약처 LLM 가이드라인(안내서-1511-01) 유형① 심사 대비 문서
| Ollama digest `f440e49dd806` | 상태: v2(werubmedv2)로 대체됐으나 일부 경로에서 계속 서빙
| 변경 이력: v1.0(초판) → v1.1(서빙 파라미터·위험 완화 참조·정비 경로 구체화)

## 1. 개요

| 항목 | 값 |
|---|---|
| 기반 모델 | Qwen2.5-7B-Instruct (Alibaba, Apache 2.0) — 아키텍처 qwen2·7.6B·컨텍스트 32,768 |
| 방법 | QLoRA 4bit(v2와 동일 하이퍼파라미터 계열 — r32/α32/dropout 0.05/seq 1024) → LoRA merge(CPU) → GGUF Q4_K_M → Ollama |
| 학습 규모 | **53건**, 2026-05-20경 (v2와 동일 파이프라인 `training/`) |
| 서빙 파라미터(Modelfile) | temperature 0.3 · top_p 0.9 · num_ctx 4096 |
| 서빙 위치 | `/api/chat/enhanced` medical 파이프라인(1·3단계) · diagnosis 도메인 1순위(`/api/medical/reasoning/*`) · SOAP 도메인 1순위 — **트윈 허가 대상 기능 경로 미관여**(인벤토리 §2) |
| 위험·완화 | v2 카드 §6과 동일(환각=RAG grounding+야간 평가 · 라우팅 드리프트=`medical_pin_public_models` 가드 · 면책 강제 · digest 판본 고정) |

## 2. 학습 데이터 구성

v2 카드(`werubmedv2.md`) §2의 v1 항목과 동일: 서버 `medical_consultations` 추출분
(학습 시점 구성=데모·시드, 2026-06-23 보안 감사 라이브 PHI 0 확인) + 수동 골드
(medical_gold.jsonl) + 합성. **세부 태스크별 건수 스냅샷은 미보존**(파이프라인
스크립트·설정은 형상관리돼 재현 가능) — 심사에 상세 구성이 필요하면 v2 전환을 권장
(v2는 train.jsonl 스냅샷 보존).

## 3. 인구통계 구성

**해당 없음** — v2 카드 §3과 동일(실환자 기록 아닌 지식·형식 Q&A, 식별정보·인구통계
속성 미포함).

## 4. 의도된 용도 / 범위 외 / 한계

- v2 카드 §4·§5와 동일하며 추가 한계: ①약물 JSON 구조화 실패 이력(v2에서 해소 —
  커밋 42bf678) ②데이터 스냅샷 미보존(§2). **트윈 허가 대상 기능 경로 미사용** 동일.
- **정비 권고**: 규제 관점에선 v1 서빙 경로(enhanced medical·diagnosis·SOAP)를
  v2 또는 공개 모델로 통일해 자체 모델을 1종으로 줄이는 것이 변경 관리·카드 유지
  부담을 낮춤 — `single_model:"qwen2.5:14b"` 전환 권고(기존 A/B 실측 근거)와 연동.

## 5. 변경 이력

2026-05-20 학습·배포 → 2026-05-21 heavy 슬롯은 v2로 전환(v1은 위 경로 잔존) →
이후 변경 없음(2026-07-19 기준).
