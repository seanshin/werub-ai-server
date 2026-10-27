# v2.125.71 — 예측 3종 JSON 계약 강제: 구조 미충족이면 200이 아니라 502

기준 v2.125.70 · 발견 경위: 2026-10-07 HIS 회신 문의("complication 200인데 생성 토큰 0 — 502로 줄 수 있나")

## 결함

`/api/medical/predict/{readmission,complication,adverse-drug}`는 Swagger상 `prediction` 객체(위험도·점수 등)를 약속하는데,
모델 출력이 JSON이 아니면 `force_json`이 `{"response": 원문}`으로 감싸 **200**으로 냈다(반복 클래스 #1).
- HIS 실측: 2026-10-07 13:51:14 complication **200 + 구조 미충족** — 직전 로그 `캐시 보류 — 구조 계약 미충족 action=predict_complication`.
- 14일 로그: 같은 경로가 **8건**(readmission 7·complication 1) — 예측 호출 약 400건 중. 캐시는 v2.124.x부터 막혀 있었지만(깨진 출력 비캐시) **응답은 성공으로 나갔다**.
- ⚠️ 그 시각 내 판정 모델 측정(격리 인스턴스·CPU)이 RAM 12.4GB를 써 운영 Ollama가 `host memory pressure`로 med42를 mmap 없이 적재했다.
  Ollama 쪽 생성은 정상 완료(3.17s·잘림 없음)라 **인과는 미확정** — 측정은 즉시 정지(`references/decision-models-l2-2026-10-07.md` §6-0).

## 처치

- `routes/medical_advanced._parse_prediction` — 캐시 수용과 **같은 판정**(`_json_acceptable`, force_json 폴백을 센티넬로 가름)이 거짓이면
  `MedicalGenerationError(status=502)` → 기존 전역 핸들러(v2.125.44)가 `{"code":"generation_failed"}` 502.
- 로그 `[medical] <action> 구조 미충족 → 502: model=… len=…` — **응답 내용은 남기지 않는다**(예측 출력은 환자 정보를 되풀이할 수 있다·HIS PHI 비저장 합의).
- 재시도는 넣지 않았다(효과 미확인 — 발생률 2% 수준·필요하면 사전 등록 후).

## 범위 밖(같은 클래스·후속 후보)

`force_json` 폴백을 그대로 200으로 내는 경로가 더 있다: `auto-record`(NER·SOAP)·`drug-analysis`·`vision/medical/structured-report`·`cross-validate`·`deep-analysis`·
`medical.py` 3곳·`writing` 2곳·`orchestrator`. 일부는 `raw`·`analysis` 키로 **의도된 저하**일 수 있고 최근 14일 호출이 없다 — 호출자 계약 확인 후 별도 처치.

## 연동자 영향

- 예측 3종: JSON을 못 만든 응답이 200 `{"prediction":{"response":"…"}}` → **502 `generation_failed`**. HIS 회신 정책상 502는 재시도하지 않는다.
- 정상 JSON(코드펜스 포함) 응답은 그대로 200.

## 검증

- 신규 `tests/test_prediction_json_contract.py` **12건**(3 엔드포인트 × 비JSON 502·로그에 내용 미노출 / 순수 JSON·펜스 JSON 200 / 빈 출력 502).
- 구 코드: **6 실패 / 6 통과**(통과 6 = 정상 JSON 200 불변식). 전체 **2466 passed**(GPU 격리 worktree).
