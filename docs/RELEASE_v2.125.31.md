# v2.125.31 — `responses` 131건 전량 보강 + 고빈도 경로 설명

날짜: 2026-08-31 | 선행: v2.125.29 전체 API 감사가 남긴 잔여

## 1. `responses` 없음 131 → 0

감사가 남긴 최대 결손이었다. **지어내지 않기 위해 AST로 실제 반환을 읽었다** —
각 핸들러의 `return ..., <code>` 를 전수 수집하고, `jsonify({"error": "..."})`의
**리터럴 문구까지 추출**해 그대로 예시로 실었다.

추출된 상태코드 분포(131 핸들러):

| 코드 | 건수 | | 코드 | 건수 |
|---|---|---|---|---|
| 200 | 127 | | 404 | 34 |
| 400 | 64 | | 403 · 409 | 3 · 3 |
| 401 | 38 | | 429 · 500 · 503 | 2 · 2 · 2 |
| | | | 202 · 413 | 1 · 1 |

파일별: game 39 · medical 19 · ollama 16 · notice 12 · orchestrator 8 · writing 7 ·
medical_cli 6 · medical_law 6 · medical_training 5 · hr_law 4 · system 4 · triage 3 · medical_advanced 2.

생성 예(실제 문구가 그대로 들어간다):

```yaml
    responses:
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "name and class required"}}
      401:
        description: "인증 실패 — 유효한 키/토큰 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "X-Game-Key required"}}
```

🔑 **연동자에게 가장 값진 것은 "어떤 상태코드가 날 수 있는가"**이고, 그건 코드에서 기계적으로
확정할 수 있다. 응답 본문 스키마(성공 시 필드 구조)는 여전히 지어내지 않았다.

## 2. 고빈도 경로 설명 — 목록을 추측하지 않았다

우선순위를 `api_activity` **실측 호출량**에서 뽑았다(감으로 고르지 않았다):

```
/api/translate 28 · /api/medical-law/ask 20 · /api/medical/dur/check 16
/api/counseling/chat 12 · /api/writing/translate 12 · /api/writing/draft 11
/api/korean/spellcheck 10 · /api/generate·/api/chat (외부 키 appServer)
```

이 중 설명이 없던 5건 보강. 모두 **코드로 확인한 사실만** 적었다:

- `/api/generate` — 🔑 **raw 통과 계약**을 명시했다. X6에서 "한국어 필터가 소실됐다"는 오인이
  나왔던 지점이라, 문서에 계약과 근거 회귀(`tests/test_generate_raw_passthrough.py`)를 적어
  다음 사람이 같은 오인을 하지 않게 했다.
- `/api/counseling/chat` — 🔴 **`is_crisis` 플래그와 1393 자동 안내**를 명시. 연동 화면이 이
  플래그를 처리하지 않으면 위기 안내가 그냥 텍스트로 렌더된다.
- `/api/translate` — 다중 `targets` 동시 번역·`source: auto`·메타코멘트 제거 가드.
- `/api/korean/spellcheck` — **외부 API 미사용**(외부 의존 0)임을 명시.
- `/api/writing/draft` — 후속 경로 연결.

## 3. 검증

- 전체 회귀 **2049 passed · 0 failed**(responses 삽입 후).
- 신규 회귀 3건: 고빈도 경로 산문 설명 전수 · `counseling`의 `is_crisis`/1393 문서화 ·
  `/api/generate` raw 계약 문서화. 관련 56 passed.

## 4. 부수 확인 — 경로 순회 프로브

`api_activity`에 `/admin-console/../../config.json`·`.secret_key`가 **200으로 9건씩** 보여
확진했다. 라이브 재현 결과 **405 · 본문 유출 없음**(v2.125.17 처치 유효). 기록된 200은
같은 DB를 쓰는 **회귀 스위트**(`tests/test_path_traversal_guard.py`)가 남긴 것이며 실제
외부 프로브가 아니다. ⚠️ `api_activity`를 볼 때 **테스트 트래픽이 섞인다**는 점을 기록한다.

## 5. ⚠️ 지표가 움직인 방향에 대한 정직한 기록

배포 후 재감사에서 **"응답 schema 전무"가 174 → 216으로 늘었다**. 퇴행이 아니라
**가려져 있던 결손이 셀 수 있게 된 것**이다 — 종전에는 `responses` 자체가 없는 144건이
이 축의 분모에서 **아예 빠져 있었다**(스키마 유무를 물으려면 responses가 먼저 있어야 한다).

이 레포가 반복해서 배운 함정과 같은 모양이다: **결측을 세지 않으면 점수가 좋아 보인다.**
그러니 이 숫자의 증가는 개선의 부작용으로 기록하고, 감소를 목표로 삼는 것은 다음 단계다.

| 축 | 감사 시작(v2.125.29 전) | 지금 |
|---|---|---|
| 미선언 태그 | 381 | **0** |
| `security` 누락(@require_auth 대비) | 17 | **0** |
| `responses` 없음 | 144 | **0** |
| 응답 schema 전무 | 174 *(단, 144건은 미집계)* | 216 *(전수 집계)* |
| `description` 보유 | 31 | **50** |

## 6. 남긴 것

- 응답 **본문 스키마**(성공 시 필드 구조) 216건 — 라우트마다 실제 반환 dict를 읽어야 한다.
  상태코드와 달리 기계적으로 확정되지 않으므로 도메인별 수작업.
- `description` 잔여 ≈541건 — 호출 실적이 붙는 대로 우선순위를 다시 뽑는다.
- medlaw 중복 색인 139청크 삭제(운영자 판단·되돌릴 수 없음).
