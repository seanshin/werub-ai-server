---
name: P5-1 Structured Output Integration - Plan
description: generate_with_validation 재시도 루프를 medical/triage/orchestrator/chat에 통합 적용
type: plan
---

# P5-1: 구조화 출력 통합 — 기획서

**Status**: Plan Phase  
**Feature**: P5-1-Structured-Output-Integration  
**Target Duration**: 2-3 days  
**Date**: 2026-05-18

---

## Context

`core/quality.py`에 구조화 출력 유틸리티 7개 함수가 이미 구현되어 있으나,
**실제 LLM 호출에서 검증+재시도 루프(`generate_with_validation`)가 어디서도 사용되지 않음**.
`force_json`은 응답 후 파싱 보조로만 사용 중.

---

## 현재 상황

### core/quality.py (구현 완료, 미적용)

| 함수 | 역할 | 사용 현황 |
|------|------|----------|
| `force_json()` | JSON 강제 추출 (4단계 폴백) | ✅ medical, coding에서 사용 |
| `force_json_array()` | JSON 배열 추출 | ✅ medical, coding에서 사용 |
| `validate_fields()` | 필수 필드 존재 확인 | ❌ 미사용 |
| `generate_with_validation()` | **LLM 생성 + 검증 + 재시도** | ❌ import만, 호출 없음 |
| `validate_json_response()` | JSON 유효성 검증기 | ❌ 미사용 |
| `validate_json_array_response()` | 배열 유효성 검증기 | ❌ 미사용 |
| `validate_soap_fields()` | SOAP 필드 검증기 | ❌ import만, 호출 없음 |

### 라우트별 품질 현황

| 라우트 | force_json | generate_with_validation | 검증기 | 재시도 |
|--------|:----------:|:------------------------:|:------:|:------:|
| routes/medical.py | ✅ | ❌ | ❌ | ❌ |
| routes/coding.py | ✅ | ❌ | ❌ | ❌ |
| routes/triage.py | ❌ | ❌ | ❌ | ❌ |
| routes/orchestrator.py | ❌ | ❌ | ❌ | ❌ |
| routes/ollama.py (chat) | ❌ | ❌ | ❌ | 한국어만 |

---

## 목표 (Goals)

| 목표 | 설명 |
|------|------|
| 검증 루프 적용 | `generate_with_validation()`을 실제 LLM 호출 경로에 통합 |
| SOAP 검증 | medical/summarize에 `validate_soap_fields` 적용 → 재시도 |
| JSON 검증 | medical/analyze, coding/review에 `validate_json_response` 적용 |
| Triage 통합 | triage에 force_json + 검증기 추가 |
| Orchestrator 통합 | orchestrator에 force_json 추가 |
| 메트릭 수집 | 재시도 횟수, 검증 통과율을 응답에 포함 |

---

## 핵심 문제: generate_with_validation 연동 패턴

### 현재 패턴 (medical/summarize 예시)

```python
# 현재: LLM 호출 → 파싱만 (검증/재시도 없음)
response = _call_ollama(model, prompt, system)
result = force_json(response, "raw")  # 파싱만
# → JSON 실패 시 {"raw": "텍스트..."} 반환 → 클라이언트가 처리
```

### 목표 패턴

```python
# 목표: LLM 호출 + 검증 + 자동 재시도
response, tokens, duration, retries = generate_with_validation(
    generate_fn=lambda p, s, o: _call_ollama_with_meta(model, p, s),
    prompt=prompt,
    system=system,
    validators=[validate_soap_fields],  # SOAP 필드 검증
    max_retries=2,
)
result = force_json(response, "raw")  # 파싱 (이미 검증됨)
# → 실패 시 2회 재시도 후 최선의 결과 반환
```

### 필요한 어댑터 함수

```python
def _call_ollama_with_meta(model, prompt, system, options=None):
    """generate_with_validation 호환 래퍼
    Returns: (response_text, tokens, duration_ms)
    """
    start = time.time()
    response = _call_ollama(model, prompt, system)
    duration = int((time.time() - start) * 1000)
    # 토큰 수는 Ollama 응답에서 추출 또는 추정
    tokens = len(response) // 4  # 대략적 추정
    return response, tokens, duration
```

---

## 적용 대상 상세

### 1. routes/medical.py — SOAP 요약 (summarize)

**현재**: LLM 호출 → force_json → SOAP 필드 누락 시 그대로 반환
**개선**: generate_with_validation + validate_soap_fields → 2회 재시도

**효과**: SOAP 형식 일관성 100% 목표 (현재 ~90%)

### 2. routes/medical.py — 감별진단 (analyze)

**현재**: LLM 호출 → force_json_array → 빈 배열 시 그대로 반환
**개선**: generate_with_validation + validate_json_array_response → 2회 재시도

**효과**: JSON 배열 파싱 성공률 향상

### 3. routes/medical.py — 환자설명 (explain)

**현재**: LLM 호출 → force_json → fallback
**개선**: generate_with_validation + validate_json_response

### 4. routes/coding.py — 코드 리뷰 (review)

**현재**: LLM 호출 → force_json_array → issues 추출
**개선**: generate_with_validation + validate_json_array_response

### 5. routes/triage.py — 증상 분류

**현재**: quality.py 미사용
**개선**: force_json + validate_json_response 추가

### 6. routes/orchestrator.py — 워크플로우 결과 파싱

**현재**: quality.py 미사용
**개선**: force_json 추가 (JSON 출력 단계에서)

---

## 수정 파일 목록

| 파일 | 변경 | 라인 수 |
|------|------|--------|
| `core/quality.py` | 어댑터 함수 추가, 검증기 개선 | ~30 |
| `routes/medical.py` | summarize/analyze/explain에 검증 루프 적용 | ~60 |
| `routes/coding.py` | review에 검증 루프 적용 | ~20 |
| `routes/triage.py` | force_json + 검증기 추가 | ~15 |
| `routes/orchestrator.py` | force_json 추가 | ~10 |
| `core/__init__.py` | quality exports 추가 | ~5 |
| **Total** | | **~140** |

---

## 구현 순서 (6 단계)

```
Step 1: core/quality.py 어댑터 함수 추가
  ↓
Step 2: routes/medical.py — summarize에 validate_soap + 재시도 적용
  ↓
Step 3: routes/medical.py — analyze/explain에 JSON 검증 + 재시도 적용
  ↓
Step 4: routes/coding.py — review에 JSON 배열 검증 + 재시도 적용
  ↓
Step 5: routes/triage.py + routes/orchestrator.py — force_json 통합
  ↓
Step 6: 문법 검증 + 통합 테스트
```

---

## 재사용 함수

| 함수 | 위치 | 용도 |
|------|------|------|
| `generate_with_validation()` | core/quality.py | 핵심 검증+재시도 루프 |
| `force_json()` | core/quality.py | JSON 강제 추출 |
| `force_json_array()` | core/quality.py | JSON 배열 추출 |
| `validate_soap_fields()` | core/quality.py | SOAP 필드 검증기 |
| `validate_json_response()` | core/quality.py | JSON 유효성 검증기 |
| `validate_json_array_response()` | core/quality.py | 배열 유효성 검증기 |

---

## KPI 목표

| 지표 | 현재 | 목표 |
|------|------|------|
| SOAP 형식 일관성 | ~90% | 99%+ |
| JSON 파싱 성공률 | ~95% | 99%+ |
| 감별진단 배열 반환율 | ~92% | 99%+ |
| 코드 리뷰 issues 파싱 | ~93% | 99%+ |
| 재시도 발생률 | 0% (미적용) | <10% (대부분 1회 통과) |
| generate_with_validation 적용 라우트 | 0/5 | 5/5 |

---

## 위험 요소

| 위험 | 확률 | 대응 |
|------|------|------|
| 재시도로 응답 시간 증가 | 중 | max_retries=2 제한, 타임아웃 설정 |
| 기존 API 응답 구조 변경 | 낮 | 응답 필드 추가만, 기존 필드 유지 |
| Ollama 호출 래퍼 호환성 | 낮 | 어댑터 패턴으로 기존 함수 감싸기 |
| triage/orchestrator 부수효과 | 낮 | force_json만 추가, 기존 로직 유지 |

---

## 검증 체크리스트

```
[ ] 1. core/quality.py 어댑터
    [ ] _make_generate_fn() 또는 래퍼 함수 추가
    [ ] 기존 7 함수 변경 없음 확인

[ ] 2. routes/medical.py
    [ ] summarize: validate_soap_fields 적용
    [ ] analyze: validate_json_array_response 적용
    [ ] explain: validate_json_response 적용
    [ ] 기존 응답 구조 유지 (하위 호환)

[ ] 3. routes/coding.py
    [ ] review: validate_json_array_response 적용

[ ] 4. routes/triage.py
    [ ] force_json 파싱 추가
    [ ] JSON 검증기 추가

[ ] 5. routes/orchestrator.py
    [ ] force_json 파싱 추가

[ ] 6. 문법 검증
    [ ] python3 -m py_compile (모든 파일)

[ ] 7. 통합 테스트
    [ ] medical/summarize SOAP 형식 확인
    [ ] medical/analyze 배열 반환 확인
    [ ] coding/review issues 파싱 확인
    [ ] 재시도 로그 확인
```

---

## 출력 경로 (PDCA)

- Plan: `docs/01-plan/features/P5-1-Structured-Output-Integration.plan.md` ✅
- Design: `docs/02-design/features/P5-1-Structured-Output-Integration.design.md`
- Implementation: `core/quality.py` + `routes/*.py`
- Analysis: `docs/03-analysis/P5-1-Structured-Output-Integration.analysis.md`
- Report: `docs/04-report/P5-1-Structured-Output-Integration.report.md`

---

## as-built 정정 (2026-08-26 · X11)

목표 5개 라우트 중 실적은 **3/5 완전 + 1 부분**:

| 라우트 | force_json | generate_with_validation + 검증기 |
|---|---|---|
| `routes/medical.py` | ✅ | ✅ 3곳(soap·array·json) |
| `routes/coding.py` | ✅ | ✅ 2곳(generate·review array) |
| `routes/orchestrator.py` | ✅ | ✅ 2곳(soap·array) |
| `routes/triage.py` | ⚠️ `force_json_array` 파싱만 | ❌ 검증기·재시도 0 (실패 시 쉼표 분리→`["기타증상"]` 폴백) |
| `routes/ollama.py` (chat) | ❌ | ❌ (`core.quality` import 0 — 한국어 비율 검증만) |

- KPI "적용 라우트 0/5→5/5"의 실적 = **3/5**. triage 검증기·chat 적용은 미이행 잔여(착수 시 동작 변경이므로 별건).
- v2.38.0 변경 기록(README·app.py CHANGELOG)의 "generate_with_validation **4개** 적용(medical/coding)"은 당시에도 과소계상 — orchestrator 적용이 같은 P5-1 커밋(`0c11f04`)에 있었다. 실제 호출부 **7곳/3파일**. 역사 문자열은 보존하고 여기서 정정한다.

---

## triage 잔여 — 측정 후 **기각**(2026-08-26)

as-built가 "triage 검증기·재시도 미이행 잔여"로 남긴 항목을 착수해 **사전 등록 후 실측**했고,
등록한 기준에 따라 **재시도는 적용하지 않기로 판정**했다.

- 사전 등록: `references/triage-json-retry-preregistration-2026-08-26.md`(기준을 측정 **전**에 확정)
- 원자료 전문: `references/triage-json-retry-rawdata-2026-08-26.json`(N=30 + 반복 통제 5)

| 지표 | 실측 | 등록 기준 | 판정 |
|---|---|---|---|
| a1 JSON 준수 실패율 | 10% (3/30) | >5%면 전환율을 본다 | — |
| 재시도 전환율 | **0/8** (0%) | <50% → 미적용 | **기각** |
| 반복 통제(같은 문장 5회) | 5/5 실패·재시도 5/5 실패 | — | 폭주가 **결정적** |

기각 사유: 실패 모드가 형식 실수가 아니라 **배열을 쓰다 말고 중국어 잡담으로 폭주**하는
것이었고, `generate_with_validation`의 교정 system 접미사로는 한 건도 전환되지 않았다.
적용했다면 실패 건마다 최대 2회(각 timeout 15s)를 더 쓰고 결과는 같았을 것이다.
⇒ **기획서에 적혀 있다는 것은 이행 이유이지 효과의 증거가 아니다**(규율 #4).

🔴 대신 측정이 **다른 결함**을 드러냈고 그쪽을 처치했다 — 쉼표 분리 폴백이 폭주 잔해를
그대로 증상으로 내보내고 있었다(`'["손발冰冷"'`·중국어 문장이 `extracted_symptoms`에 실림).
처치=`routes/triage.py:_parse_symptom_list`(배열 조각 수집·잔재 제거·한자/가나 배제).
검증=원자료 35건을 회귀 입력으로 재사용(개선 8·퇴행 0·오염 8→0)·`tests/test_triage_symptom_parse.py`.

**KPI 정정**: "적용 라우트 5/5"는 달성되지 않으며 **달성 대상이 아니다** — triage는 측정 기각,
chat(`routes/ollama.py`)은 여전히 미착수 잔여(범용 프록시 계약이라 착수 시 별도 판단 필요).
실적은 **3/5 + triage 기각 1**로 확정한다.

---

**다음 단계**: `/pdca design P5-1-Structured-Output-Integration`
