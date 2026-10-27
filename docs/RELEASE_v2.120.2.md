# RELEASE v2.120.2 — 품질 관리 영역 확대 (세밀 지표 시계열 2×2)

**날짜**: 2026-07-06
**헤드라인**: LLM 품질 평가 카드를 **관리 영역**으로 확대 — v2.120.0~1의 세밀 지표 전부를
배치별 **시계열 차트 4종**으로 표시(스파크라인 2종 → 관리 차트 2×2).

---

## ① `/api/eval/status` 추세 확장 (`routes/activity.py`)

- `trend_faithfulness`(배치별): 기존 mean·hallucinations에 **strict·mc_agree·mc_conflicts** 합류.
- `trend_quality`(배치별): 기존 overall에 **accuracy·helpfulness·safety·fluency·checks_rate** 합류.
- 미측정 배치=NULL 유지(프론트가 결측 구간으로 표시 — 허위 연속성 방지). 구 스키마 폴백.

## ② 관리 차트 4종 (`templates/admin.html`+`static/js/main.js`)

| 차트 | 계열 | 축 |
|---|---|---|
| 신뢰성 추세 | 충실도(주 KPI)·strict·교차검증 일치 | 0~1 |
| 품질 차원 추세 | 정확성·도움됨·안전성·자연성 | 1~5 |
| 체크리스트 통과율 | 단일 계열 | 0~1 |
| 리뷰 트리거(건) | 환각 플래그·교차 불일치 — 그룹 막대 | 건수 |

- **팔레트 계산 검증**(dataviz 절차): `#16a34a·#a855f7·#0284c7·#f43f5e` — 다크 서피스
  `#1a1d27` 대비 밝기 밴드·채도·CVD 분리·대비 **4/4 PASS**(validate_palette.js 실행,
  초기 후보 2회 FAIL→스텝 조정).
- 규율: 단일 축(이중축 금지)·다계열 범례 상시(단일 계열은 제목이 명명)·hover 툴팁
  (index 모드)·`spanGaps:false`(세밀 지표 도입 전 배치는 선을 끊어 표시)·막대 rounded.
- 로드 1회 패턴 유지(신규 폴링 0). 배치 2개 미만이면 기존처럼 숨김.

## 검증

- 라이브 API: 12배치 시계열 + 최신 배치 세밀 지표(strict 0.75·mc_agree 0.75·conflicts 4·
  checks_rate 0.733·차원별 4.2/4.0/5.0/4.6) 산출 확인. graceful HUP·NRestarts 0·health 200.
- node --check·py_compile. 렌더 검증 페이지 생성(라이브 데이터 인라인) — 차트 코드는
  기존 렌더 실증된 스파크라인 패턴의 확장(Chart.js 4 동일).

## 영향 / 하위호환

- API 추세 필드는 추가만(기존 소비자 무영향). 카드는 세밀 데이터 축적에 따라 자동 충실화
  — 야간 배치(03:30)마다 4차트 포인트 1개씩 누적.
