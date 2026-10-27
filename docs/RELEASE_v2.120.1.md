# RELEASE v2.120.1 — 품질 카드 세밀 지표 표시 (교차검증·체크리스트·질문별 진단)

**날짜**: 2026-07-06
**헤드라인**: v2.120.0에서 신설한 세밀 지표(MiniCheck 교차검증·체크리스트 12항목)를
**Admin "LLM 품질 평가" 카드에 표시** — 집계 타일·개선 지점 라인·질문별 세부 진단 테이블.

---

## ① `/api/eval/status` 확장 (`routes/activity.py`)

- **faithfulness**: `mc_agree`(교차검증 일치율)·`mc_conflicts`(리뷰 트리거 합)·`strict`
  (보수 충실도) + `questions[]`(질문별 충실도·주장 수·contradicted·교차검증).
- **quality**: `checks_rate`(체크리스트 통과율)·`checks_failed_top`(실패 항목 빈도순
  = 개선 지점 랭킹) + `questions[]`(질문별 overall·checks_failed).
- 구 스키마/미측정 배치 graceful(필드 생략) — 기존 소비자 하위호환.

## ② Admin 카드 확장 (`templates/admin.html`+`static/js/main.js`)

- **세밀 지표 타일 4종**(2행): 교차검증 일치 · 리뷰 트리거(⚠>0 강조) · strict 충실도 ·
  체크리스트 통과. 미측정 배치면 자동 숨김(하위호환).
- **"개선 지점" 라인**: 실패 체크 항목 빈도순(예: `accuracy.conditions_kept ×3`).
- **질문별 세부 진단 토글 테이블**: 질문 × 충실도·주장 수·교차검증(⚠conflicts)·품질·
  체크 실패 — 문제 문항이 행 단위로 특정됨. XSS escape. 로드 1회 패턴 유지(신규 폴링 0).

## 라이브 실측 (첫 세밀 배치)

- 집계: faith 1.000 · **mc_agree 75%** · 리뷰 트리거 4건 · strict 0.75 · 품질 4.45/5 ·
  **체크리스트 73%**.
- **두 세밀 축의 신호 일관**: Q5(사본 발급 거부)가 교차검증 conflicts 3 **그리고**
  체크리스트 5/12로 동시 지목 — 기존 집계(faith 1.000·overall 4.2)에서는 안 보이던 문항.
- **체계적 개선 지점 표면화**: `accuracy.conditions_kept`(한정조건 유지) 3/5 문항 실패 ·
  `fluency.consistent_register` 3/5 — 다음 폐루프(프롬프트/생성 개선) 대상 특정.

## 검증

- node --check·py_compile·graceful HUP(NRestarts 0)·health 200. `/api/eval/status`
  신규 필드 전체 라이브 확인. eval_quality 체크리스트 실경로 5질의 통과.
