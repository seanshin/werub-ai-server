# RELEASE v2.123.0 — 품질 종합 지수 (0~100 · 등급 A~D)

**날짜**: 2026-07-07
**헤드라인**: 흩어진 세밀 지표(충실도·환각·교차검증·G-Eval·체크리스트·한국어)를 **단일 관리
수치**로 결정적 합성 — 라이브 **94.0점(A)**, 배치별 시계열 16포인트.

---

## ① 산식 (`core/eval_score.py`, qi-1.0 — 공개·결정적·버전 표기)

| 구성 | 가중 | 정규화 |
|---|---|---|
| 충실도(faithfulness) | **35%** | 0~1 그대로 (의료 안전 핵심) |
| 무환각률(1−플래그/n) | 15% | 안전 KPI |
| strict 충실도(judge+MiniCheck 양측) | 10% | 이중 검증 보수 |
| 품질 overall(G-Eval) | **25%** | (x−1)/4 |
| 체크리스트 통과율 | 10% | 0~1 |
| 한국어 비율 | 5% | 0~1 |

- **미측정 구성요소는 분모 제외(재정규화)** — 세밀 지표 도입 전 배치와 호환.
- 등급: A≥90 · B≥80 · C≥70 · D<70. 가중치 변경 시 INDEX_VERSION 상향(추세 단절 명시).
- ⚠️ **내부 관리지표**(추세·회귀 감시·상대 비교용) — 대외 인증 수치 아님(N=질의 풀 40·
  LLM-judge 기반 한계, `docs/EVAL_GUIDE.md`·llm-eval-fine-grained.plan.md 참조).

## ② API·표시

- `/api/eval/status`: `quality_index`(최신 배치 — score·grade·components·weights_used·
  version) + `trend_index`(배치별 — faith×quality 시간버킷 조인, 미조인=faith 구성 재정규화).
- **모니터 히어로**: 대형 점수+등급(등급색 A녹/B청/C황/D적)+산식 캡션+지수 추세 차트(0~100).
- **Admin 카드**: "품질 지수" 타일(점수+등급, 등급색).

## 검증

- 회귀 `test_eval_score` 8(가중 합·정규화·재정규화·등급·클램프·실측 대역)+eval_pool 7.
- 라이브: **quality_index 94.0(A)** — components 6종 전부 산출(faith 1.0·무환각 1.0·
  strict 1.0·overall 0.867·checks 0.758·korean 0.95), trend_index 16포인트
  (82.1 B→96.7 A→94.0 A). graceful HUP·NRestarts 0·health 200.

## 영향 / 하위호환

- API 필드 추가만(기존 소비자 무영향). 지수는 기존 저장 데이터에서 소급 계산되므로
  과거 배치 시계열도 즉시 표시. 산식 변경은 버전 상향으로만(qi-1.0 고정).
