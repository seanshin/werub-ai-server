# RELEASE v2.124.14 — 규제 라우팅 가드 활성화 + NC 잔존물 제거 + HAI-DEF 고지 (ops)

2026-07-20 | 이전: v2.124.13(교차검증 백엔드 교체) | 성격: **운영 전환(코드 무변경)** —
식약처 LLM 가이드라인 대응 트랙의 AI 서버측 이행 완결 릴리즈.

## 1. 규제 라우팅 가드 활성화 (운영자 지시)

- GPU config `medical_pin_public_models: true` — `load_config()`가 매 호출 파일을 읽어
  **무재기동 즉시 적용**.
- 라이브 검증(`GET /api/models/routing?fresh=1`): summarize·analyze·draft·reasoning →
  qwen2.5:14b, enhanced medical 파이프라인 1·3단계 → qwen2.5:14b, **전 자동 경로
  custom=false**. v2.124.12 라우팅 API가 이 전환을 즉시 노출함을 실증(설계 목적 달성).
- 효과: **자체 파인튜닝 모델(werub-medical:7b·werubmedv2)이 어떤 자동 라우팅 경로에도
  서빙되지 않는 구조 보장 완성** — 유형② 프레임 최강 상태, v1 모델 정비는 라우팅
  관점에서 해소(모델 파일은 카드·재현성 위해 보존="보유하되 미서빙").
- 롤백: config 한 줄(false) — 즉시. ⚠️관찰: summarize 계열 품질 특성 변화 가능
  (A/B 실측상 qwen2.5:14b 동등 이상 — v2.114.0·v2.104.0 근거), 야간 배치·사용자
  피드백으로 감시.

## 2. bespoke-minicheck 설치본 제거

- `ollama rm bespoke-minicheck` — **4.7GB 회수·NC(CC BY-NC 4.0) 잔존물 0**
  (디스크 여유 226G). v2.124.13에서 기본 백엔드가 qwen-entail로 교체돼 미사용 상태였음.
  레거시 opt-in(`FAITH_VERIFIER=minicheck`)은 이제 모델 부재로 자동 생략(비차단) —
  필요 시 `ollama pull bespoke-minicheck`로 복구 가능(단 NC 라이선스 재적용).

## 3. HAI-DEF 하류 전달 고지 (MedGemma 약관 의무 이행)

- `docs/MEDICAL_API_GUIDE.md` 의료 영상 절: `/api/vision/medical/*` 호출자에게 전달되는
  제한 명시 — ①출력의 직접 진단·치료 단독 사용 금지(임상 사용=규제기관 승인 필요)
  ②Prohibited Use Policy 준수 ③제3자 재제공 시 동일 제한 전달. 현행 모델 표기도
  medgemma-1.5 Q8로 정정(구 medgemma:4b 표기 잔존 제거).

## 4. 규제 트랙 최종 상태 (`mfds-llm-compliance-status.md` 정본)

- AI 서버측: **전 항목 이행 완료** — 회신 2건·모델 카드 2종(v1.1)·인벤토리(라이선스
  전수 확정)·R4 정책 문서(v1.1)·라우팅 조회 API·가드 활성화·NC 해소·HAI-DEF 고지.
- 잔여 1건(운영자 폼): MiniCPM-V 상업 등록(무료 questionnaire).
- 외부 대기: 트윈 질의서·R3 전환·FMEA, 식약처 유형 판정.

## 5. 검증

- 라우팅 API 라이브 스냅샷(§1)·health 200·NRestarts 0·`/api/version` 2.124.14.
- 코드 무변경 릴리즈라 회귀는 v2.124.13 상태 유지(medlaw 56·faithfulness 19·
  model_routing 4·model_pin 7).
