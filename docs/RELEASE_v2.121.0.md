# RELEASE v2.121.0 — 질문별 드릴다운 상세 검토 (품질 모니터)

**날짜**: 2026-07-06
**헤드라인**: 품질 모니터의 질문별 세부 진단 리스트를 **확대 검토 가능**하게 — 행 클릭
드릴다운 모달에서 **답변 전문·주장별 판정(judge vs MiniCheck)·차원별 점수+사유·체크
12항목·근거 발췌·배치 이력**까지 질문 단위 상세 품질 데이터를 열람.

---

## ① 상세 영속화 (러너 — 기존엔 집계만 저장, 상세는 휘발)

- `faithfulness_results` +`answer`(답변 전문)·`detail`(주장별 판정+MiniCheck 교차검증
  결과+근거 발췌 4,000자 JSON).
- `quality_results` +`answer`·`detail`(차원별 점수·사유·체크 12항목 개별 결과 JSON).
- ALTER 멱등 마이그레이션. 야간 배치(5행/일)라 용량 영향 미미.

## ② 상세·이력 API (`routes/activity.py`)

- **`GET /api/eval/detail?kind=faithfulness|quality&id=`** — 단건 전체(답변·detail JSON 파싱).
- **`GET /api/eval/history?question=&limit=`** — 같은 질문의 최근 배치 이력(충실도·환각·
  불일치·overall·checks — 리스트 확대 검토 축).
- `/api/eval/status`의 질문별 배열에 `id` 추가(행→상세 연결 키).

## ③ 모니터 드릴다운 모달 (`templates/eval_monitor.html`)

질문 행 클릭 → 모달:
- **요약 타일**(충실도·교차 일치·불일치·overall·체크리스트)
- **답변 전문**(스크롤 박스)
- **주장별 판정 테이블**: 주장 원문 × judge 판정(supported/unsupported/contradicted) ×
  MiniCheck(yes/no) × **⚠ 불일치(리뷰)** 강조 — 교차검증 충돌 지점이 주장 단위로 특정
- **차원별 점수·사유·체크 칩**: 각 차원 1~5 + judge 사유 원문 + 체크 12항목 ✓/✗
- **근거 발췌**(충실도 채점 기준, 접기)
- **배치 이력 테이블**: 이 질문의 최근 15배치 추이(충실도·환각·불일치·overall·체크 실패)
- ESC/오버레이 닫기·XSS escape. **상세 미기록 구배치는 정직 안내**("v2.121.0 이후 기록").

## 검증

- 회귀 26(faithfulness·judge) 통과·JS/py 문법. graceful HUP·NRestarts 0·health 200.
- 라이브: 배포 직후 평가 2종 실행→detail API가 답변 전문·주장별 판정·차원 사유·체크
  항목 반환 확인(아래 실측), history API 질문별 다배치 이력 반환.

## 영향 / 하위호환

- 기존 API·표 형태 불변(행 클릭·id 필드 추가만). 신규 컬럼은 러너 마이그레이션.
- 이제 리뷰 트리거(불일치·체크 실패)가 뜨면 모니터에서 **원문 수준까지** 바로 검토 가능
  — 측정→검토→수정 폐루프의 검토 단계가 화면으로 완결.
