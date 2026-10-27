# RELEASE v2.119.0 — 수집 로그 + 주기 수집보고서 (외부 정본 수집 관측/관리)

**날짜**: 2026-07-06
**헤드라인**: HIRA 게시판·data.go.kr 외부 정본 수집(공지사항 동기화)의 **전 시도 이력 로그** +
**주기 수집보고서 자동 생성** + **로그 관리(조회·retention)** — 기존 `*_sync_meta`(소스별
최신 상태만)로는 없던 이력·보고 축 신설.

---

## ① 수집 이력 로그 (`core/collect_log.py` — `collect_log` 테이블)

- **커버 소스(12)**: HIRA 게시판(약제급여목록·저가약대체조제) + data.go.kr API(치료재료·
  DUR 8유형·e약은요) — `hira_drug_sync._record`·`dur_sync._record`에 best-effort 훅
  (수집 경로에 예외 전파 0).
- 기록 항목: ts·source·kind(hira-board/datago-api)·status(ok/skip/error/denied)·건수·
  edition·게시글 제목·소요시간·에러. 인덱스(ts·source).
- **조회**: `GET /api/notice/collect/logs?source=&status=&since_days=&limit=`(최신순, 최대 1000).

## ② 주기 수집보고서 (`collect_reports` 테이블)

- **내용**: 기간 내 소스별 시도/적재/skip/오류/미신청 집계 + **신규판 적재 내역**(edition·
  건수) + 최근 오류 + **현재 정본 규모 스냅샷**(fee_codes·대체조제·DUR) + 한 줄 요약문
  (문제 소스 자동 플래그 "⚠️ 점검 필요: …").
- **주기 생성**: hira-sync 스케줄러 루프에 `report_due` 게이트 합류(기본 7일, DB 기준이라
  워커 재생성 가로질러 cadence 보장 — DUR 과다동기화 v2.110.4 교훈 일관).
- **API**: `GET /api/notice/collect/report`(최신 저장본, 없으면 404) ·
  `POST /api/notice/collect/report {"period_days":7}`(즉시 생성·저장) ·
  `GET /api/notice/collect/reports`(목록 요약).

## ③ 로그 관리

- **retention**: 로그 기본 365일(config `collect_log_retention_days` 무재배포 튜닝),
  보고서는 4배 보존(집계 산출물). 보고서 생성 시 자동 정리 + `POST /api/notice/collect/purge`.
- **Admin 카드 "수집 로그·보고서"**: HIRA 소스 상태(✅/⏸/⚠️)·최근 7일 오류 건수·최신 보고서
  요약·"보고서 즉시 생성" 버튼(5분 폴링 기존 패턴 재사용 — 신규 고빈도 폴링 0).

## 검증

- 회귀: 신규 `test_collect_log` 6(적재·필터·never-raise·retention·집계/요약·due 게이트)+
  test_dur_sync에 collect_log 무력화 autouse 픽스처(**실 DB 오염 방지** — 모듈 import-시점
  바인딩이 모킹을 우회하는 클래스, 당일 2번째 발견이라 격리 원칙으로 정착). GPU 39 passed.
- 라이브 E2E: 동기화 트리거→로그 3건(skip 2·denied 1, kind 자동 분류)→보고서 생성
  ("최근 7일 수집 시도 3건 … ⚠️ 점검 필요: 치료재료")→최신 조회 ok. NRestarts 0·health 200.

## 영향 / 하위호환

- 수집 경로 동작 불변(훅은 best-effort·실패 무시). 신규 테이블 2개(notice.db)·API 4종·
  Admin 카드 1. 치료재료 활용신청 등 수집 이슈가 이제 보고서/카드에서 자동 표면화.
