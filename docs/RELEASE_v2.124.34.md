# v2.124.34 — 대시보드 성능 카드에 GPU 상주 비율(CPU 폴백) 타일

2026-07-25 | 계기: v2.124.33 CPU-폴백 경보의 상시 가시화 (백로그 #2)

v2.124.33이 stats_history에 박제하는 `model_vram_ratio`(상주 모델 GPU 비율)를
Admin 대시보드 "성능 모니터링" 카드에 상시 노출. 경보(6h 쿨다운) 발화 전에도
현재 GPU 상주 상태를 한눈에 확인.

## 변경 (프론트 전용, 신규 폴링 0)

- `templates/admin.html` 성능 카드 그리드에 **"GPU 상주"** 타일 추가.
- `static/js/main.js` `loadDashboardCharts`에 렌더: `last.model_vram_ratio` →
  **1.0=`GPU 100%`**(정상)·**0=`CPU 폴백!`**(적색)·중간=`GPU N%`(<50% 적색·그외 황색).
- `/api/stats/history`는 `SELECT *`라 컬럼 자동 포함 — 엔드포인트/폴링 변경 없음
  (기존 60s 대시보드 타이머 재사용).

## 검증

- 라이브: 대시보드 로드 시 "GPU 상주 GPU 100%"(현재 ratio 1.0) 표시. CPU 폴백 시
  적색 "CPU 폴백!"로 즉시 식별.
