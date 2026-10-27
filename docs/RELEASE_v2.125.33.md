# v2.125.33 — 응답 스키마 2차: 호출 함수 추적 + Korean 도메인

날짜: 2026-08-31 | 선행: v2.125.32가 남긴 "응답 schema 전무 132건"

## 1. 자동 추적을 한 단계 더 — 그리고 한계를 인정했다

남은 132건 중 121건은 `jsonify(변수)` 형태라 **호출되는 함수를 한 단계 더 따라가는** 추적기를
만들었다. 지원한 패턴:

- `return {dict 리터럴}` (직접)
- `out = {리터럴}` → `out["k"] = …` → `return out` (누적 dict — 실제로 흔하다)
- `jsonify(f())`에서 `f`가 **모듈 전체에서 유일**할 때만 해석(동명이인이면 중단)

**결과는 8건뿐이었다.** 나머지는 추적기가 **추측을 거부**하고 중단했다 — 변수가 조건 분기로
갈라지거나, 여러 함수를 합성하거나, 동명 함수가 여럿인 경우다. 이건 실패가 아니라 설계다:
확정할 수 없는 것을 그럴듯하게 적으면 문서-코드 드리프트가 된다.

⇒ **자동화의 한계선을 여기로 긋고, 나머지는 도메인별 수작업**으로 전환했다.

## 2. Korean 도메인 9건 — 코어 함수의 실제 반환을 읽었다

우선순위는 호출 실적(`/api/korean/spellcheck`가 실호출 상위)과 표면 크기(24 API)로 정했다.
각 핸들러가 부르는 `core/korean_humanize.py`·`core/korean_spellcheck.py` 함수의 반환을
직접 읽어 필드를 적었다:

| 경로 | 근거 함수 | 주요 필드 |
|---|---|---|
| `/api/korean/analyze` | `compute_korean_quality` | `risk_band`·`risk_score`·`metrics`·`z_scores`·`v2_metrics`·`evidence` |
| `/api/korean/spellcheck` | `spellcheck` / `spellcheck_long` | `checked`·`corrections`·`errors` / **500자 초과 시 `chunks`·`total_errors`** |
| `/api/korean/stats` | `compute_text_stats` | 14필드(문장 유형·어휘 풍부도·대사 비율…) |
| `/api/korean/sensory` | `analyze_sensory` | `senses`·`show_dont_tell_score`·`missing` |
| `/api/korean/readability` · `cohesion` · `repetition` · `honorific` · `pov` | 각 대응 함수 | `score`/`grade`/`metrics` 등 |

🔑 `spellcheck`는 **입력 길이에 따라 응답 형태가 갈린다**(500자 초과면 청크 분할 경로).
스펙에 둘 다 적어 연동자가 `chunks` 유무로 놀라지 않게 했다.

## 3. 검증

- 전체 회귀: 배포 로그 참조.
- 라이브 스펙 재감사로 "응답 schema 전무" 감소분 확인.

## 4. 남긴 것 — 값의 기울기를 밝힌다

잔여 스키마는 대부분 **내부·창작 도메인**(novel·story·game·training)과 대시보드다.
외부/내부 시스템이 실제로 붙는 표면(의료 카탈로그 23경로·고빈도 7경로·Korean)은 덮였고,
여기서부터는 **문서 1건당 연동 가치가 급격히 떨어진다**.

권고: 나머지는 **일괄 처리하지 말고**, 해당 도메인에 실제 연동 요청이 생길 때
그 경로만 코드를 읽어 채운다. 그게 드리프트를 만들지 않는 유일한 방법이다.
