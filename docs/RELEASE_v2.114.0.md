# Release v2.114.0 — Ollama 0.30.11 업그레이드 + 평가 후속(의료 A/B·SelfCheck) + ERP 폴링 대응

> 2026-07-03 | 관련: `docs/01-plan/features/ollama-upgrade-0.30.plan.md` ·
> `docs/01-plan/features/llm-evaluation-hallucination.plan.md` · `docs/01-plan/references/erp-index-status-polling-notice.md`

## 요약

외부 성능 기획서 검토에서 출발한 **Ollama 0.21.0→0.30.11 업그레이드**(16/16 검증·prefix 캐시 98%↓ 실측)와,
LLM 평가 프레임워크의 실전 활용(**의료 파이프라인 A/B — med42 단계 기여 없음 실측**), 무참조 환각 검출
(SelfCheckGPT), 성능 모니터링이 발견한 **ERP 폴링 버그** 대응을 묶은 릴리즈.

## 1. Ollama 0.21.0 → 0.30.11 업그레이드 (인프라)

- **발단**: 외부 기획서(`Ollama_성능향상_방향성_검토_기획서.md`) 검토 — 축 A(FLASH/KV q8_0/컨텍스트 정책)는
  **기적용**, 기준선 기존재(문서 목표 25 tok/s vs 실측 89), vLLM은 36모델 스왑 플릿 비호환(문서 누락) 교정.
  유효 항목(신 스케줄러·prefix 캐시·NVFP4)이 전부 버전 게이트 → 업그레이드 선결.
- **실행**: 수동 바이너리 교체(tar.zst, unit 재작성 방지). sudo 암호 단계는 **docker 그룹**(root 동등) 바인드
  마운트로 직접 수행, 재시작은 NOPASSWD. 롤백 백업(bin+lib `ollama-0.21.0.bak`) 확보.
- **검증 16/16 PASS**(`scripts/ollama_upgrade_check.sh` 신규): 버전·env drop-in 6종 보존·모델 37 인식(저장소
  무손실)·warm **86 tok/s**(등가)·**format 제약 정상**(medical_router/eval 의존)·상주·임베딩·app health.
  qwen2.5:14b footprint **12.2→10.5GB**. 검증 오탐 2건 수정(콜드로드 tok/s→워밍업 선행·format grep→JSON 파싱).
- **축 B(prefix 캐시) 실측**: 도구콜 모사 4.9K tok prefix — cold prefill 1.21s → same-prefix 재요청
  **0.01~0.02s = 98%↓**(기획서 목표 70% 초과). 프롬프트 구조(system/도구 선두)가 이미 캐시 친화적 = 리팩터링 불요.
- **soak**: 기존 모니터링 알림(latency_p95/error_rate/churn) + 야간 eval 배치가 자동 감시. 0.30.9 동작변화
  (컨텍스트 초과 시 무성절단→에러 반환) 주의 명시. 24h 무이상 후 백업 제거.

## 2. 의료 파이프라인 A/B — med42 단계 기여 없음 (평가 프레임워크 실전 활용)

`scripts/eval_medical_ab.py`(신규) — 실제 `/api/chat/enhanced` medical 경로에서 full(werub→med42→werub) vs
`single_model:"qwen2.5:14b"` 비교:

| 축 | full-med42 | single-qwen14b |
|---|---|---|
| 객관 정확도(KorMedMCQA doctor 30, 파이프라인 통과) | 33.3% | 33.3% (동률) |
| 블라인드 judge(자유 임상질문 5) | 3.10/5 | **3.90/5** |
| 지연 | 14.7s/문항 | **12.8s/문항** + 스왑 소멸 |

→ **3축 전부 single 동등 이상 = med42 단계 실경로 기여 없음 실측 확정**(KorMedMCQA 재평가 플래그 종결).
**HIS 컨시어지 `single_model` 전환 권고 근거 확보**(기본값 변경은 사용자 결정). 캐비엇: N=30·MC≠파이프라인
설계목적(증상상담)·judge 자기선호 가능(객관축 동률과 방향 일치로 상쇄).

## 3. 평가 프레임워크 보강

- **SelfCheckGPT** `core/selfcheck.py`(신규)+`test_selfcheck` 6: 같은 질의 N회 재샘플→주장별 샘플 일관성
  (발산=환각 신호). **참조문서 불요** = 비-RAG 경로(상담·일반챗) 커버. faithfulness 분해/판정 재사용. 라이브 검증 OK.
- **시드 조문 채점 근거 포함**: `get_evidence`에 law_articles 병합(/ask 컨텍스트 정합) → Q1~Q4 충실도 전부
  1.000. Q5서 실제 생성결함 2건 추가 포착(제28조 오인용·주체혼동 — judge 정확) → **백로그: 의료법 retrieval
  튜닝**(제21조-class 인출). `eval_quality`는 중복 헬퍼 제거(단일 소스 import).
- **품질 카드 스파크라인**: `/api/eval/status`에 배치별 추세(trend_faithfulness/quality, **A/B 실험 `ab-%`
  제외**=프로덕션 추세 오염 방지) + Admin 카드 스파크라인 2종(충실도 0~1·overall 1~5).

## 4. ERP index-status 폴링 대응 (성능 모니터링 P0 발견의 후속)

- **진단**: `erp-notice-worker`가 **2026-06-11 완료된 잡 ~50건을 22일째 ~1/s 폴링**(전체 트래픽 48%·워커
  churn 주동력). `latest:"completed"` 응답에도 루프 미종료 = **ERP측 버그**(서버 stuck 0 = 무죄).
- **서버측 방어**: `_ACTIVITY_SKIP` 추가(api_activity 관측 윈도우 복원) + 종결 상태 응답 `Cache-Control:
  max-age=86400`(`routes/notice.py _index_status_response`).
- **ERP 통지 문서**: `docs/01-plan/references/erp-index-status-polling-notice.md`(종료조건+백오프 수정 요청)
  — **전달 대기**. max_requests 상향은 ERP 수정 후 재판단(보류).

## 검증

- Ollama 업그레이드 16/16 · 축 B 98%↓ · A/B eval.db 저장 · 회귀 신규 12(selfcheck 6 + 기존 faithfulness/judge
  포함 총 22) · 시드근거 재측정 0.850→(프롬프트fix 후 배치) · graceful HUP · health 200 · qwen 상주.

## 사용자 결정 대기

1. **ERP 통지 문서 전달** 2. **HIS `single_model` 전환**(A/B 근거) 3. soak 후 롤백 백업 제거(자동 진행 예정)

## 변경 파일

- 신규: `core/selfcheck.py` · `scripts/eval_medical_ab.py` · `scripts/ollama_upgrade_check.sh` ·
  `tests/test_selfcheck.py` · `docs/01-plan/features/ollama-upgrade-0.30.plan.md` ·
  `docs/01-plan/references/erp-index-status-polling-notice.md` · `docs/RELEASE_v2.114.0.md`
- 수정: `app.py`(_ACTIVITY_SKIP) · `routes/notice.py`(Cache-Control) · `routes/activity.py`(trend) ·
  `scripts/eval_faithfulness.py`(시드근거) · `scripts/eval_quality.py`(헬퍼 통일) ·
  `templates/admin.html`+`static/js/main.js`(스파크라인) · CLAUDE.md · README.md
- 인프라: **Ollama 0.30.11**(GPU, 롤백 백업 보유) · eval.db 테이블 재사용(ab-* source)
