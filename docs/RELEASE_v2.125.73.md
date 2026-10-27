# v2.125.73 — `quality.validated` 표식 정정: 마지막 시도 성공을 실패로 표시하던 모호성

기준 v2.125.72 · 발견 경위: v2.125.72 force_json 전수 분류 중 탐색 에이전트 보고 → **코드에서 직접 확인** 후 처치

## 결함

`core/quality.generate_with_validation`(생성 → validator → 재시도)은 **마지막 시도에서 통과**해도, **전 시도가 실패**해도
똑같이 `retries=max_retries`(2)를 돌려줬다. 호출자 **7곳**(medical summarize·analyze·explain · coding generate·review · 워크플로 summarize·analyze)이
`"quality": {"validated": retries < 2}`로 표시해:
- 마지막 시도 성공 → **`validated: false`**(거짓 음성) — 진짜 실패와 구분 불가
- 최종 실패 → 로그 **0줄**(경고는 재시도할 때만 찍혔다)

## 처치

- `generate_with_validation_ex` 신설 — `(response, tokens, duration_ms, retries, passed)`. 최종 실패 시 경고 로그 한 줄.
- `generate_with_validation`은 **호환 래퍼**(종전 4-튜플 그대로) + "retries로 성공을 판단하지 말 것" 주석.
- 7곳 `validated = passed`. validator가 없는 분기(coding generate 비-Python·summarize split)는 종전처럼 `True`.
- 바뀌는 관측값: **마지막 시도 성공 → `validated: true`** 하나뿐. 실패는 그대로 `false`.

## 검증

- 신규 `tests/test_validated_marker.py` **10건**(단위 7 · `validated = retries <` 패턴 재발 가드 · explain 라우트 마지막 시도 성공 · coding generate 무-validator 분기 무오류).
- 구 라우트(신 `quality.py` 유지): **2 실패 / 8 통과**(통과 8 = 단위·무-validator 불변식). ⚠️ 구현 중 내 결함 1건: coding generate의 validator 없는 `else` 분기가
  `passed`를 정의하지 않아 **비-Python 코드 생성이 NameError**로 죽을 뻔했다 — 코드 리뷰로 발견·회귀 추가.
- 전체 **2502 passed**(GPU 격리 worktree).
