# Release v2.113.0 — LLM 평가 프레임워크 완결 (충실도·품질 judge + 야간배치 + 폐루프 실증)

> 2026-07-03 | 가이드 `docs/EVAL_GUIDE.md` · 기획 `docs/01-plan/features/llm-evaluation-hallucination.plan.md`
> v2.112.0(조사+P0+Phase 1 벤치)의 후속 — Phase 2~5 완결. 외부 API 0 · judge=상주 qwen2.5:14b.

## 요약

3축 평가(성능·할루시네이션·가치)를 완결하고, **측정→오류 포착→근본수정→재측정 폐루프를 라이브로
실증**했다: 충실도 측정이 의료법 답변의 실오류를 잡았고, 프롬프트 수정 후 충실도 0.600→0.933.

## Phase 2 — RAG 충실도 (`core/faithfulness.py`)

- RAGAS-style 2단계: 답변→원자적 주장 분해 → 근거 대비 판정(supported/unsupported/contradicted)
  → faithfulness(지지율)·hallucination 플래그. judge=상주 qwen2.5:14b+`format` 제약 디코딩.
- **수용 게이트(통제 페어) 4/4** — P0서 HHEM-2.1-Open이 2/4(한국어 와파린 페어 반전)였던 변별을
  LLM-judge는 faith 1.0/0.0으로 완벽 분리 → HHEM NO-GO 대체 정당성 실측 입증.
- 실측(의료법 RAG 5질의): 평균 0.600·환각 플래그 4/5 → 특성화(실오류 1·정밀도 2·커버리지 1).
- 회귀 `test_faithfulness` 10.

## 🎯 폐루프 실증 — 포착 오류의 근본수정

- 추적: RAG 근거는 정확("수술기록: 10년" 명시)한데 **생성이 열거를 재그룹하며 수치 오배치**
  ("수술기록 등 5년") = 생성 시 요약 압축 오류.
- 수정: `MEDICAL_LAW_SYSTEM` 규칙 3 추가 — *근거에 열거된 항목별 수치(보존기간·금액·조문 번호)는
  절대 재그룹·병합하지 말고 원문 그대로 항목별 표기*.
- 재측정: **평균 충실도 0.600→0.933 · 환각 플래그 4/5→0/5 · contradicted 0** — 규칙 하나가
  오류 클래스 전체(보존기간·심의 의무·조번호) 봉합. **평가 프레임워크의 실사용 가치 완결 입증.**

## Phase 3 — 가치/품질 judge (`core/llm_judge.py`)

- G-Eval 루브릭 4차원(정확성·도움됨·안전성·한국어 자연성) 1~5 평점+사유, 의료 자문보조 맥락 고정.
  `core.korean` 형식 메트릭과 결합(`scripts/eval_quality.py`).
- 실측: **평균 overall 4.10/5**(accuracy 5/5 ×4문항 — 충실도 수정과 정합. 최저 Q5=충실도 축과
  동일 약점 지목 → 두 측정축 신호 일관). 회귀 `test_llm_judge` 6.

## Phase 4 — 야간 배치 + 품질 카드

- **GPU cron `30 3 * * *`**(03:30 KST): faithfulness+quality 배치 → `logs/eval_nightly.log`.
  인앱 스케줄러 대신 cron = 워커 recycle 재발화 클래스 버그(DUR 교훈) 회피.
- **`GET /api/eval/status`**(routes/activity.py, 인증 게이트): eval.db 최신 배치 요약
  (충실도·품질·벤치 모델별). eval.db 부재 시 graceful.
- **Admin "LLM 품질 평가" 카드**: 로드 시 1회(폴링 0 — SSE 고갈 교훈).

## Phase 5 — 문서

- `docs/EVAL_GUIDE.md`: 3축 호출법·판독(플래그=검토 트리거, contradicted vs unsupported 구분)·
  야간배치·한계(HHEM 사용 금지·벤치 단독 모델교체 금지).

## 검증 (GPU 라이브)

- 회귀 test_faithfulness 10 + test_llm_judge 6(GPU venv). 통제 게이트 4/4.
- 충실도 재측정 0.933·환각 0/5 · 품질 4.10/5 · `/api/eval/status` 라이브 정상 · cron 등록 확인 ·
  graceful HUP NRestarts=0 · health 200.

## 비범위 / 후속

- 시드 조문(law_articles) 채점 근거 포함(Q5류 unsupported 해소) · SelfCheckGPT(비-RAG 개방형) ·
  K-HALU/KMMLU 추가 벤치 — 요청 시.
- 실시간(요청별) 충실도 채점 — 배치 중심 유지.

## 변경 파일

- 신규: `core/faithfulness.py` · `core/llm_judge.py` · `scripts/eval_faithfulness.py` ·
  `scripts/eval_quality.py` · `tests/test_faithfulness.py`(10) · `tests/test_llm_judge.py`(6) ·
  `docs/EVAL_GUIDE.md` · `docs/RELEASE_v2.113.0.md`
- 수정: `routes/medical_law.py`(수치 재그룹 금지 규칙) · `routes/activity.py`(/api/eval/status) ·
  `templates/admin.html`+`static/js/main.js`(품질 카드) · 기획서
- 인프라: GPU cron 야간배치 · `eval.db`(gitignored) 테이블 2종 추가
