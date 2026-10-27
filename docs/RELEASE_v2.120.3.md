# RELEASE v2.120.3 — LLM 품질 실시간 모니터 새창 (/eval-monitor)

**날짜**: 2026-07-06
**헤드라인**: 품질 평가 카드의 그래프·세부 내용을 **별도 새창에서 실시간 모니터링** —
전용 페이지 `/eval-monitor` + 카드 "실시간 모니터 ↗" 버튼.

---

## 구성

- **`GET /eval-monitor`**(`templates/eval_monitor.html`, `_is_monitor_authorized` 가드 —
  `/monitor`와 동일: 로컬 bypass·외부 로그인): 독립 풀페이지 —
  - **KPI 타일 12종**(충실도·환각·교차 일치·리뷰 트리거·strict·overall·4차원·체크리스트·한국어)
  - **개선 지점 라인**(실패 체크 빈도 랭킹)
  - **시계열 4차트 대형**(신뢰성 0~1 · 품질 차원 1~5 · 체크리스트 통과율 · 리뷰 트리거 막대 —
    v2.120.2 검증 팔레트·단일축·범례·hover 툴팁·spanGaps 규율 동일)
  - **질문별 세부 진단 상시 테이블** + 모델 벤치 최신.
- **자동 갱신**: 30초/60초/5분 선택(기본 60초)+수동 갱신 버튼+마지막 갱신 시각 표시.
  차트는 인스턴스 유지 후 데이터만 `update('none')` — 갱신 시 깜빡임 0.
- **카드 연결**: Admin "LLM 품질 평가" 카드 헤더에 "실시간 모니터 ↗"(window.open 1320×920).
- **폴링 부하 설계**(SSE 고갈·폴링 과다 교훈): 새창 1개=분당 1회 eval.db 경량 읽기,
  창 닫으면 0 — 대시보드 폴링 과다 사고 클래스와 무관 수준.

## Hotfix (9557472, 같은 날)

- **외부 접속 데이터 미표시 fix**: `example.org/ollama/eval-monitor`는 앱이 `/ollama`
  프리픽스에 마운트 — 절대경로 `/api/eval/status` 호출이 프록시 밖으로 나가 "평가 데이터
  없음". `location.pathname` 프리픽스 자동 감지(BASE)로 수정(로컬/외부 겸용, 인증은
  `_blueprint_auth` 웹 세션 허용이라 경로만 문제).
- **갱신 주기 5초·10초 추가**(기본 10초로 변경 — 실시간성 요청).
- **호출 서비스 표기(034cae1)**: `/api/eval/status`에 source·judge_model 노출 →
  모니터 헤더에 평가 파이프라인(대상: 의료법 RAG `/api/medical-law/ask` · judge:
  qwen2.5:14b · 교차검증: MiniCheck) + 질문별 테이블 "서비스" 열(소스별 라벨 매핑).

## 검증

- py_compile·inline JS node --check. graceful HUP·NRestarts 0·health 200.
- 라이브: `/eval-monitor` 200(로컬)·타이틀/차트 4캔버스/세부 테이블/주기 선택기 서빙 확인.
  야간 배치·수동 평가 실행 시 60초 내 자동 반영.
