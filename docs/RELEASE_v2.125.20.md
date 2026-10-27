# v2.125.20 — 잊힌 작업 3건 마감(X7 meeting language·X9 numbers_match 규명·X11 문서-코드 불일치)

날짜: 2026-08-26 | 선행: v2.125.19(X1·X5·X6·X2·X8) — §9 잊힌 작업 11건 중 이번 3건으로 **X10만 잔여** | 규율: 병렬 조사 3대→중앙 구현·판정은 직접 재검증

## 1. X7 — meeting `language` : "auto" 오작동의 실체는 더 컸다

주장은 "auto가 무검증으로 whisper에 직결"이었는데, 조사 결과 **세 겹**이었다:

- ①로컬 경로: `"auto"`는 faster-whisper 유효 코드가 아니라 `tokenizer` **ValueError** —
  단 다운로드·오디오 추출을 다 마친 transcribe 단계에서야 잡이 failed(늦은 실패).
- 🔴②**위임 경로(더 큰 발견)**: `dispatch.run("stt", hints={"language": lang})`의 hints를
  `client.call`이 **아예 전송하지 않는다** → 워커가 form 기본 `"ko"`로 낙착 =
  **auto뿐 아니라 모든 비-ko 값이 조용히 ko로 전사**(무성 소실). prompt/hotwords는
  같은 이유로 이미 로컬 강제 처치(v2.124.42 계열)가 있었는데 **language만 누락**돼 있었다.
- ③`/api/stt/chunk`(실시간)의 `X-Lang`도 동일 클래스 — 무효 코드가 ValueError→
  **500 "transcribe_failed"로 뭉개짐**(상세는 로그에만).

**사실 정정**: `"auto"`는 확약된 계약값이 아니다 — 정본 문서(API_REFERENCE·설계서·대외
회신문)는 전부 `ko`만 안내하고, "auto"는 기획서(meeting-phase2 §29·§149)의 **미구현 작업
항목** 문법이었다(구현 이력 git 0건·테스트 0건).

처치(`core/meeting_stt.py`·`core/meeting_pipeline.py`·`routes/meeting.py`·`routes/system.py`):
- `normalize_language()` 신설 — `"auto"`/`""`→**None(자동 감지)**·유효 코드 통과·무효는
  오류. 코드 목록은 `faster_whisper.tokenizer._LANGUAGE_CODES` lazy 캐시, **미가용 시
  fail-open**(현행 무검증과 동일 동작).
- meeting 접수(`/api/meeting/analyze`)에서 **400 조기 거절**(종전엔 다운로드·추출 낭비 후
  실패). `options` 비-dict도 400.
- `stt_options`는 auto→None만 매핑, **무효 값은 그대로 통과** — 접수 검증 이전에 저장된
  구 잡을 조용히 ko로 저하시키지 않는다(종전대로 transcribe에서 크게 실패).
- `_stt_local_only()` — prompt/hotwords 기존 처치에 **비-ko(자동 감지 None 포함) 합류**:
  위임하면 소실되므로 로컬 faster-whisper 강제. ko(기본·실사용 대부분)의 위임은 불변.
- `/api/stt/chunk`: 무효 X-Lang·`"auto"` → **400 표면화**. auto 지원은 안 함 — 실시간
  트랙은 언어충실도 가드가 요청 언어를 전제하는 계약 경로라 거절만(지원은 별건 결정).

## 2. X9 — `numbers_match` 8→11 원인규명 완료: H2 확정, 축 자체가 노이즈였다

- ⚠️ **N=16 A/B 원자료는 존재하지 않는다** — 레포(JSON·커밋)·GPU `eval.db`(전 배치 조회)
  모두 부재. 8·11의 문항별 판독은 영구 불가(사실대로 기록).
- **H2 직접 재검증**(eval.db SELECT만·LLM 0): 같은 시기·같은 judge 기준의 08-05 02:24
  전수 배치(N=40)에서 `numbers_match` 실패 **25/40** 중 **13건은 답변에 단위 수치가
  아예 없었다**(기준상 true여야 함). 사유엔 완전성·관련성·시점 불명 불평 혼입(중국어
  사유 1건 포함). judge-only 25·결정적-only 0 = v2.124.87 기록과 DB 일치.
- **H1 소멸**(교체된 결정적 `numbers_grounded`는 조·항·호 제외) · H3=이 축 소관 아님.
- **판정**: 8도 11도 위양성 지배 축의 변동 — "악화" 근거 아님. Phase 2 간결 규칙 채택은
  이 축과 무관하게 §7 다른 기준으로. 게이트가 미이행으로 남은 원인=v2.124.87 결과의
  기획서 반영 누락 → `35b-baseline-hardening.plan.md` §7-b에 판정 절 추가.

## 3. X11 — 문서-코드 불일치 4건 전수 정정

| # | 주장 | 판정·처치 |
|---|---|---|
| stdio | 부분 사실 — 가이드는 다이어그램 1줄, **광고 주범은 기획서**(§4-4·mcp_stdio.py·성공기준) | 가이드 다이어그램 HTTP로 정정+기획서 stdio 컷 명시+`❌ 미구현` 행 as-built. 부수: 도구 수 **20→28 정정 2곳**(tools.json 실측 28) |
| P5-1 | 사실 — triage=파싱만(검증기 0)·chat=core.quality 0 | 기획서에 **as-built 절**(3/5 완전+1 부분·KPI 실적·v2.38.0 "4개"는 당시에도 과소계상 — orchestrator가 같은 커밋에 있었음. 역사 문자열 보존·정본에서 정정) |
| CROPPED | 사실·완전 데드(생성처 0·`.get` 소비 1곳) | `routes/vision.py` 상수 삭제(영향 0 — `.get(i,10)` 기본값). `INVALID_COLORSPACE`는 **상수조차 없음**(X11 누락분) — 기획서 표에 둘 다 미구현(컷) 표기 |
| tune_connection | 미구현은 사실이나 **분류 오류** — 광고한 문서 없음(미래형 제안뿐). 진짜 결함=✅완료 기획서에서 **P0-2만 컷 목록 누락**(증발) | status·§8-2에 "미구현 잔여" 복원+이름 `_tune_conn`↔`tune_connection` 통일 |

## 4. 검증

- 신규 회귀 **12건**(`tests/test_meeting_language.py`): normalize 6(기본 ko·auto/빈→None·
  유효·무효·비문자열·**fail-open**)+stt_options 배선 3(auto→None·기본/유효 불변·**무효
  passthrough**=구 잡 조용한 저하 금지)+로컬 강제 조건 5종.
- 기존 meeting 계약 30 포함 42 passed(로컬)·전체 회귀·게이트는 배포 시.

## 5. 남긴 것

- §9 잔여 = **X10 하나**(위임 검증 게이트: detect cpu↔mps 100장·STT WER ≤2%p — iMac 실측
  필요·별도 세션).
- 별건: 위임 hints 미전송의 구조 처치(client.call — 지금은 로컬 강제로 회피) ·
  `/api/stt/chunk` auto 지원 여부(언어충실도 가드 검토 선행) · v2.125.19 이월분.
