# RELEASE v2.118.5 — HIRA 동기화 다중소스 확장 (대체조제 자동화 + 치료재료 준비)

**날짜**: 2026-07-06
**헤드라인**: HIRA 자동 동기화를 다중 소스로 일반화 — ①**저가약 대체조제 자동 동기화**(월별
게시판→clear 스냅샷 교체) ②**치료재료 API 동기화 준비**(청구 코드 3축 완성 대비, 활용신청
대기 휴면) ③의료기관 운영 데이터 확장 후보 정리.

---

## ① 저가약 대체조제 자동 동기화 (`core/hira_drug_sync.py` 다중소스 일반화)

- **소스 레지스트리** `_SOURCES`: 약제급여목록(v2.118.3)에 **저가약대체조제**
  (게시판 pgmid=HIRAA030015000000 「저가약 대체조제 장려금 지급대상 의약품 현황」 월별 게시
  → `drug_substitution` clear 스냅샷 교체, min 8,000) 추가. 소스별 독립 `_sync_due`(7일).
- **게시글 게이트 개선**: post_no+제목 비교를 **다운로드 전** 수행 — 동일 게시글이면 요청
  1건으로 skip(기존 edition 게이트는 다운로드 후 판정이었음). 첨부 **키워드 선택**(청구요령
  hwp 제외, "지급대상 의약품 목록" xlsx 선택)+**xlsx(PK) 매직 검증**(에러페이지 오적재 방지).
- **API**: `GET /api/notice/hira-drug/sync-status`(소스별 메타 배열),
  `POST /api/notice/hira-drug/sync`(`{"source":"저가약대체조제"}` 단일 소스·force·409).
- `SUB_HEADERS`를 `core/drug_substitution`으로 이동(ingest 스크립트·sync 공용).

**라이브 E2E(GPU)**: 수동 트리거 → '26.6월 기준 게시글 자동 발견→목록 xlsx 다운로드→
**10,980건 교체 적재**(eligible 10,420 — v2.118.4 수동 적재와 동일=멱등 입증)·메타 ok.
약제급여목록은 동일 게시글 **skip**(다운로드 0건).

## ② 치료재료 동기화 준비 (`core/hira_mcat_sync.py` 신설, 활용신청 대기)

- **목적**: 청구 코드 해석 3축 완성 — 수가(TARIFF 7,138)·약가(DRUG 21,959)는 있고
  **치료재료(MATERIAL)만 공백**. ERP 사전심사·코드 조회의 치료재료 커버리지.
- **소스**: data.go.kr **치료재료정보조회서비스**(`B551182/mcatInfoService1.2/
  getPaymentNonPaymentList1.2`, REST/XML) — 재료대코드·품목명·규격·상한단가·급여구분.
  HIRA 게시판의 치료재료 고시는 개정분(별지)만 첨부라 전량 스냅샷은 API가 정본 경로
  (파일데이터 15067463은 폐기됨 확인).
- **안전 갱신**: 전량 fetch+건수 sane(≥10,000) 후에만 MATERIAL 전량 교체(DRUG/TARIFF 불변).
- **⚠️ 휴면 상태**: 현 data.go.kr 키가 심평원(B551182) 서비스 **미신청 → 403=denied**로
  기록(기존 데이터 불변·last_check 갱신=주기 재시도만, 워커 churn마다 403 안 때림).
  **활성화 = data.go.kr 로그인 → "치료재료정보조회서비스" 활용신청(자동승인, ~2분) →**
  `POST /api/notice/hira-drug/sync {"source":"치료재료"}` 1회.
- 스케줄러는 기존 hira-sync 루프에 합류(소스별 due). 라이브 denied 경로 E2E 검증 완료.

## ③ 의료기관 운영 데이터 확장 후보(조사 결과)

| 후보 | 소스 | 상태 |
|---|---|---|
| 치료재료 목록 | data.go.kr API | **구현 완료·활용신청 대기**(②) |
| 의·치과/공상 수가파일(EDI) | HIRA 공지사항 게시판(월별) | 후보 — TARIFF 갱신 경로, 요청 시 |
| 질병군(DRG) 치료재료 별도보상 코드 | HIRA 공지사항(월별) | 후보 — DRG 청구 보조, 요청 시 |
| 비급여 진료비용 정보 | 심평원 비급여 포털/API | 후보 — 비급여 고지 의무 보조, 요청 시 |
| 약가마스터 크로스워크 갱신 | 사용자 CSV(공개본은 구판) | 최신판 확보 시 기존 스크립트 |

## 검증

- 회귀: test_hira_drug_sync 12→**17**(다중소스·첨부 키워드·PK 매직·다운로드 전 skip)+
  **test_hira_mcat_sync 7**(XML 파싱·교체·denied 휴면)+test_drug_substitution 9. GPU 33 passed.
- 배포: graceful HUP·NRestarts 0·health 200. 라이브 E2E 3소스 상태(ok/skip/denied) 전부 확인.
- 테스트 격리 버그 교훈: `import_substitution`에 conn 미전달 시 모듈 import-시점 바인딩으로
  모킹 우회 → 실 DB 오염. sync 경로 전부 명시적 conn 전달로 수정(로컬 dev DB 정리 완료).

## 영향 / 하위호환

- 대체조제: 이후 월별 신판 자동 반영(주 1회 확인). 조회 API 계약 불변.
- sync-status 응답 구조: 단일 meta → **sources 배열**(v2.118.3 당일 릴리즈라 외부 소비자 0).
- 치료재료: 활용신청 전까지 완전 휴면(기존 동작 100% 불변).
