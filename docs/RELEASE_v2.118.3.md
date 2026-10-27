# RELEASE v2.118.3 — HIRA 약가목록 자동 동기화 + 의료법 생성 정밀도(Q5) fix

**날짜**: 2026-07-06
**헤드라인**: ①HIRA 「약제급여목록및급여상한금액표」 **자동 동기화 파이프라인** 신설(월별 게시판
최신판 주기 다운로드→fee_codes(DRUG) 안전 교체, 2026.7.1판 21,959건 적재 완료) ②의료법 RAG
**생성 정밀도 규칙 2종** 추가(Q5 친족 열람권 contradicted 봉합 — **5질의 충실도 전부 1.000·환각 0/5**).

---

## ① HIRA 약가목록 자동 동기화 (`core/hira_drug_sync.py`)

**배경**: 약가목록(fee_codes DRUG)은 v2.91.0에서 2026.6.1판 수동 적재 후 갱신 경로가 없었다.
HIRA 약제급여목록표 게시판(`hira.or.kr` pgmid=HIRAA030014050000)에 **매월 최신판이 게시**되므로
주기 자동 갱신이 필요(사용자 요청).

- **파이프라인**: 게시판 목록 스크랩→최신 「약제급여목록및급여상한금액표」 게시글→
  `downLoadBbs(...)` 첨부 파라미터 파싱→`/bbs/bbsCDownLoad.do` 다운로드→기존
  `core/hira_drug_parser`(순수 stdlib) 재사용 파싱.
- **안전 갱신(dur_sync 패턴)**: 다운로드+파싱 성공 + 건수 sane(**≥15,000**) + **edition 신규**일
  때만 교체 — 사이트 장애/파일 이상 시 기존 정본 보존. 동일판 재실행=skip(라이브 검증).
- **DRUG 카테고리 전량 교체**: 월별 완전 스냅샷이므로 upsert가 아닌 교체(급여 삭제 품목 잔존
  방지). TARIFF 등 타 카테고리 불변(라이브 7,138 유지 확인). 교체 후 ANALYZE.
- **메타** `hira_drug_sync_meta`(notice.db): status(ok/skip/error)·edition·건수·post_no·
  last_check_at(ok/skip)·last_sync_at(ok)·last_error.
- **스케줄러**: `_sync_due` 게이트(last_check_at 기준) — 워커 재생성(gunicorn max_requests)을
  가로질러 7일 cadence 보장(DUR 과다동기화 v2.110.4 교훈 선반영). error는 last_check 미갱신이라
  다음 기회 재시도. config `hira_drug_sync_enabled`(기본 **true**)·`hira_drug_sync_interval_days`(7).
- **API**: `GET /api/notice/hira-drug/sync-status` · `POST /api/notice/hira-drug/sync`
  (수동 202·`{"force":true}`=동일판 재적재·동시 실행 409).
- **예의**: robots.txt 확인(/co/만 차단 — 게시판·다운로드 경로 허용), 주 1회 요청 3건(경량).
  공개용(비인가자용) 자료.

**라이브 결과(GPU)**: 수동 sync 5.3s — post 1707 「2026.07.01. 현재 약제급여목록및급여상한금액표」
edition 2026-07-01 **21,959건** 적재(DRUG 21,898→21,959·TARIFF 불변). 재실행→**skip**(edition
게이트). 코드 조회(`645302132`→DRUG_MATERIAL/REVIEW_CRITERIA) 정상. 다운로드본은 사용자 제공
파일과 **sha256 동일** 검증.

## ② 의료법 생성 정밀도 규칙 2종 (Q5 친족 열람권 contradicted 봉합)

**배경**: v2.114.1 retrieval 튜닝 후 Q5("환자 진료기록 사본 발급 요청을 거부할 수 있는가?")에
contradicted 1건 잔여(생성 결함, retrieval 아님 — 백로그). 라이브 재현으로 결함 2종 확정:
1. **괄호 한정 조건 누락**: 원문 "형제·자매(배우자 및 직계존비속, 배우자의 직계존속이 **모두
   없는 경우에 한정**한다)"의 한정 조건을 드롭하고 무조건 나열.
2. **호 번호↔내용 오배치**: "제21조제3항제2호"를 인용하며 실제 2호(대리인 요건)가 아닌
   존재하지 않는 내용("생명·신체 중대 위해")을 창작.

**fix**(`routes/medical_law.py` `MEDICAL_LAW_SYSTEM`): 규칙 4(괄호 한정 조건·단서 "~에
한정한다"/"다만~" 보존)+규칙 5(호 번호 인용 시 그 호의 원문 내용만 사용·미확인 시 인용 생략)
추가. v2.113.0 규칙 3(수치 재그룹 금지)과 동일 계열의 **측정→포착→근본수정→재측정 폐루프**.

**재측정(scripts/eval_faithfulness.py, judge=상주 qwen2.5:14b)**:

| 지표 | 수정 전(v2.114.1) | 수정 후 |
|---|---|---|
| Q5 충실도 | 0.800 (contradicted 1) | **1.000 (c0)** |
| 5질의 평균 | 0.893 | **1.000** |
| 환각 플래그 | 1/5 | **0/5** |

## 부수

- **Ollama 0.21 롤백 백업**: 7/4 기제거 확인(0.30.11 정상·~5GB 기회수) — 잔여 조치 없음.

## 검증

- 회귀: 신규 `tests/test_hira_drug_sync.py` **12**(파서·건수 게이트·edition dedup·오류 보존·
  _sync_due) + test_hira_drug_parser 12 + test_dur_sync 9 통과. GPU서도 12 passed.
- 배포: 커밋 c941d04(의료법)+b48a35c(HIRA 동기화)→GPU pull→ast→graceful HUP×2(**NRestarts 0**)→
  health 200. 충실도 재측정 5/5 faith=1.000. HIRA 수동 sync E2E ok.

## 영향 / 하위호환

- fee_codes(DRUG): 2026.6.1판→**2026.7.1판**(+61품목). 조회 API 계약 불변.
- 의료법 /ask·/review: 시스템 프롬프트 규칙만 추가(응답 shape 불변) — 조건·호 인용 정밀도 개선.
- 신규 스케줄러는 주 1회 경량 스크랩(기본 on, `hira_drug_sync_enabled=false`로 무재배포 off).
