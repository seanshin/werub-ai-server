# v2.125.72 — force_json 폴백 무성 실패 전수 처치 (v2.125.71 같은 클래스 쓸기)

기준 v2.125.71 · 발견 경위: v2.125.71(예측 3종 JSON 계약)의 "같은 클래스 잔여 ~10곳" 후속 · 전수 분류는 탐색 에이전트 → **대표 지점 직접 코드 확인 후** 처치

## 결함

`core/quality.force_json`은 실패해도 `{fallback_key: 원문}` dict를, `force_json_array`는 `[]`를 돌려준다 — 호출자가 성공/실패를 못 갈라
**200 + 원문 감싼 dict / 빈 목록**이 정상 결과처럼 나갔다(반복 클래스 #1). `routes/` 20곳 전수 분류:

| 분류 | 지점 | 종전 증상 |
|---|---|---|
| **A 주 결과** | auto-record(+stream) NER·SOAP · drug-analysis 1단계 · vision structured-report · summarize(단일) · analyze · 워크플로 단계 summarize/analyze · writing seo·evaluate · code/review | `{"response"/"raw": 원문}` 200 · analyze `[]`=**'감별진단 없음'**(저장·과금) · seo **지어낸 `readability.score: 50`** · code review `issues: []`=**'버그 없음'**을 **캐시**까지 · 워크플로는 실패가 '완료'로 다음 단계에 흐름 |
| **C 하위 단계** | drug-analysis 2단계 권고 · summarize(split) 메타 · batch 건별 · cross-validate 모델별 | 권고 자리에 원문 · **지어낸 `urgency: "medium"`** · 파싱 실패도 `completed` · 실패 모델이 `diagnoses: []`로 **유효 모델에 세져 일치도를 끌어내림** |
| **B 의도된 저하** | deep-analysis(`analysis: {}` 무타입) · explain(폴백 키가 프롬프트 필드와 같음) | 저하는 쓸모 있으나 성공과 구분 없음 |
| 이미 처리 | triage `_parse_symptom_list`(`if not items:`) | — |

## 처치

- `core/quality.parse_json_strict` / `parse_json_array_strict` — `force_json`과 같은 복구 규칙, **실패면 None**(센티넬). ⚠️ 정상 JSON `[]`·`{}`는 **성공**(코드 리뷰 '이슈 없음'은 정당한 결과).
- **A** → 의료: `routes/medical_advanced._require_json` → `MedicalGenerationError` 502(SSE는 기존 가드가 error 이벤트) · summarize·analyze도 502(**상담 이력 저장·과금 전에** 끊긴다) ·
  structured-report `MODEL_OUTPUT_UNPARSED` 502 · 워크플로 단계 `{"error": …}`(단계 실패) · writing·code review: `"Error:"`(생성 실패)는 `GENERATION_FAILED` 502, 구조 미충족은 `MODEL_OUTPUT_UNPARSED` 502 · **code review 실패는 캐시하지 않는다**.
- **C** → 엔드포인트는 살린다: `recommendations: null` + `recommendations_error` · `urgency: null` + `meta_error` · batch 건별 `status: failed` · cross-validate 모델별 `error`(→ valid_count·일치도에서 제외).
- **B** → 동작 유지 + **추가 필드 `parse_ok`**(기존 호출자 무영향) + 경고 로그.
- 모든 경고 로그는 **응답 내용 없이 길이만**(의료 출력은 환자 정보를 되풀이할 수 있다).

## 연동자 영향

- 최근 30일 외부 호출: 이 경로들은 HIS `/api/medical/explain` 2건(B — 200 유지·`parse_ok` 추가)뿐. 나머지는 배포 게이트·측정 스크립트 호출.
- 실패가 200 → 502로 바뀌는 경로: auto-record·drug-analysis·structured-report·summarize·analyze·writing seo/evaluate·code review.

## 범위 밖(기록)

- `generate_with_validation`이 마지막 시도 성공·전 시도 실패 모두 `retries=max_retries`를 돌려 `quality.validated = retries < 2`가 **모호한 표식**이다(에이전트 보고·미검증) — 별도.
- vision structured-report는 이미지 처리가 얽혀 회귀를 이번 파일에 넣지 않았다(코드 리뷰로 확인).

## 검증

- 신규 `tests/test_force_json_sweep.py` **26건**(판정 함수 9 · A 9 · C 2 · B 2 · 워크플로 2 · 쓰기 2 · 코드 리뷰 2 — 정상 빈 결과 200 불변식 포함).
- **구 라우트 코드: 16 실패 / 10 통과** — 통과 10 = 판정 함수 단위(신 `quality.py` 유지)·정상 빈 결과 200 불변식. GPU 격리 worktree.
- 기존 테스트 2건이 **옛 계약을 고정**하고 있어 갱신: `test_seo_response_schema`가 `"raw" in data`(실패 200)를 허용 —
  테스트 환경은 생성이 실패해 **늘 raw로 통과**하고 있었다(실서비스 경로는 정상 JSON 200 실측) · `test_json_endpoints_use_force_korean_json`의 소스 문자열 개수 검사(의도 불변).
- 전체 **2492 passed**.

## 배포 (2026-10-07 15:44)

- `deploy.sh` 게이트 **174/175 BLOCKED** — `story[story-uses-world-context]` 502·0자. 재기동은 게이트 전에 일어나 **v2.125.72는 이미 라이브**.
- 원인 판독: 로그 `[story] 한국어 후처리 후 본문 99자 < 100 — 502(저장 안 함)` — **기존 길이 가드**(v2.125.52 계열)가 짧은 생성 1회를 정직하게 막은 것. story 코드는 이번 변경에 없다.
- 확인: 같은 항목 단독 재실행 **3/3 통과**(602·457·419자). 총수 175 = 실패 항목이 첫 검사에서 멈춰 나머지 2검사가 빠진 것(177−2).
- 판단: **확률적 짧은 생성·변경 무관 → 배포 유지**(되돌림·베이스라인 등록 안 함 — 체계적 실패가 아니다). 같은 항목이 다음 게이트에서 또 막히면 길이 하한(100자)과 생성 길이 분포를 재측정할 것.
