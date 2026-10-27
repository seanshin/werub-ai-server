# Korean Chat Processing Fix — Plan (v2)

## 문제 정의

채팅 및 LLM 응답의 한국어 후처리가 **비대칭·불완전**하게 적용되어 있음.

### 핵심 문제 3가지

1. **force_korean 미적용**: quality_check(비율 체크)만 하고 실제 필터링(`force_korean`)을 하지 않는 엔드포인트 다수
2. **스트리밍 후처리 누락**: 스트리밍은 토큰 레벨 `force_korean_token`(일본어만 제거)만 적용, 최종 조립 텍스트에 `force_korean` 미적용
3. **캐시/세션 오염**: 스트리밍 응답이 필터링 없이 캐시·세션에 저장 → 오염 전파

## 코드 검증 기반 현황

### 한국어 처리 함수 차이

| 함수 | 위치 | 기능 | 비고 |
|------|------|------|------|
| `force_korean(text)` | core/korean.py:17 | CJK/일본어 + 영문 액션 + LaTeX 제거 + 한국어 없는 줄 제거 | **실제 필터링** |
| `force_korean_token(token)` | core/korean.py:49 | 일본어만 제거 (`_JAPANESE.sub("", token)`) | 스트리밍 토큰용, 불완전 |
| `korean_quality_check(text)` | core/korean.py:56 | 한국어 비율 30% 이상 확인 | **비율 검사만**, 필터링 안 함 |
| `validate_korean_quality(text)` | core/korean.py:56 | `korean_quality_check`의 alias | chat.py에서 import |
| `generate_korean(...)` | core/korean.py:81 | force_korean + quality_check + retry 통합 | hr_law/game/medical이 사용 |
| `compute_korean_quality(text)` | core/korean_humanize.py | AI 패턴 22메트릭 분석 | 선택적 적용 |

### 엔드포인트별 현황 (코드 검증 완료)

#### CRITICAL — 스트리밍 필터링 + 캐시/세션 오염

| 엔드포인트 | 파일:줄 | force_korean | 캐시 오염 | 세션 오염 |
|-----------|---------|:---:|:---:|:---:|
| **`/api/chat` (streaming)** | ollama.py:2636 | ❌ raw 저장 | ✅ line 2664 | ✅ line 2677 |
| **`/api/generate`** | chat.py:366 | ❌ raw 스트림 | N/A | N/A |
| **`/api/chat` (streaming)** | chat.py:449 | ❌ raw 스트림 | N/A | N/A |

**ollama.py:2636 stream() 함수가 가장 심각**:
```python
# line 2661: 원문 그대로 캐시
full_text = "".join(_full_response)
_llm_response_cache.set(_ck, full_text)  # ← 필터 없음!
# line 2677: 원문 그대로 세션
append_chat_message(sess["id"], "assistant", full_text, None)  # ← korean_ratio=None!
```

#### CRITICAL — Non-streaming에서 force_korean 누락

| 엔드포인트 | 파일:줄 | quality_check | force_korean | 문제 |
|-----------|---------|:---:|:---:|------|
| **`/api/chat` (non-stream)** | ollama.py:2487 | ✅ 비율만 | ❌ | 비율 체크만, 필터링 안 함 |
| **`/api/chat` (non-stream)** | chat.py:291 | ✅ 비율만 | ❌ | 동일 |
| `/api/chat/enhanced` | ollama.py:2251 | ✅ | ✅ line 2288 | OK |

**핵심 발견**: ollama.py의 `/api/chat` non-streaming은 `korean_quality_check`로 **비율만 확인**하고 `force_korean`을 **적용하지 않음**. 비율이 30% 이상이면 통과하지만, CJK/LaTeX/영문 액션이 섞여 있어도 그대로 반환.

#### WARNING — `_llm_generate_korean` 경유 (부분 적용)

`generate_korean()` 함수를 사용하는 엔드포인트는 내부에서 `force_korean + quality_check + retry`를 적용하나, **humanize(AI 패턴 탐지)는 미적용**:

| 엔드포인트 | 파일 | generate_korean | humanize |
|-----------|------|:---:|:---:|
| `/api/game/dialogue` (일부) | game.py:680 | ✅ | ❌ |
| `/api/game/npc/heroine/interact` | game.py:1768 | ✅ | ❌ |
| `/api/hr-law/ask` | hr_law.py:366 | ✅ | ❌ |

#### WARNING — 직접 Ollama 호출 (한국어 처리 없음)

| 엔드포인트 | 파일:줄 | 비고 |
|-----------|---------|------|
| `/api/game/npc/generate` | game.py:413 | JSON 출력이라 force_korean 불필요하나 text 필드에 오염 가능 |
| `/api/game/dialogue` (group) | game.py:579 | 직접 ollama 호출, JSON 파싱 → text 필드 미필터링 |
| `/api/medical/triage` | triage.py:557 | JSON 응답 내 한국어 필드 미필터링 |
| `/api/medical/reasoning/chain` | medical_advanced.py:100 | `_medical_generate` 경유 (별도 체크 필요) |

#### OK — 완전 적용

| 엔드포인트 | 파일 | 비고 |
|-----------|------|------|
| `/api/counseling/chat` | counseling.py | ✅ force_korean + quality_check + humanize + retry |
| `/api/counseling/chat/stream` | counseling.py | ✅ 조립 텍스트에 force_korean + quality_check |
| `/api/writing/draft` | writing.py | ✅ force_korean + humanize + retry |
| `/api/chat/enhanced` | ollama.py | ✅ force_korean + quality_check |
| `/api/orchestrator/run` | ollama.py:1444 | ✅ force_korean 적용 |

## 근본 원인 분석

### 원인 1: validate vs filter 혼동

`korean_quality_check()`는 **검증만** 하고 텍스트를 **변경하지 않음**.
많은 엔드포인트가 이것만 호출하고 "한국어 처리 완료"로 간주 → 실제 필터링(`force_korean`) 누락.

```python
# ❌ 현재 패턴 (ollama.py:2493)
is_valid, korean_ratio, _ = validate_korean_quality(response_text)
# → 비율 체크만, 텍스트 변경 없음, CJK/LaTeX 그대로 반환

# ✅ 올바른 패턴
response_text = force_korean(response_text)  # 먼저 필터링
is_valid, korean_ratio, _ = korean_quality_check(response_text)  # 그 다음 검증
```

### 원인 2: 스트리밍에서 후처리 불가능 구조

스트리밍은 토큰을 즉시 yield → 최종 텍스트를 필터링할 타이밍이 `finally` 블록뿐.
현재 `finally`에서 캐시/세션 저장만 하고 **필터링은 하지 않음**.

### 원인 3: `force_korean_token`의 한계

```python
def force_korean_token(token):
    return _JAPANESE.sub("", token)  # 일본어만 제거
```
영문 액션(`*sighs*`), LaTeX(`\frac{}`), CJK 구두점, 한국어 없는 줄 → 모두 통과.

## 해결 전략 (수정)

### Phase 1: core/korean.py에 통합 후처리 함수 추가

**신규 파일 생성 대신 기존 core/korean.py에 추가** (import 일관성 유지):

```python
def postprocess_korean(text, *, genre="chat", validate=True, humanize=False):
    """통합 한국어 후처리 — force_korean + 품질검증 + humanize
    
    Returns:
        (filtered_text, korean_ratio, quality_summary)
    """
    if not text or not text.strip():
        return text, 0.0, None
    
    # 1. force_korean (실제 필터링)
    text = force_korean(text)
    
    # 2. korean_quality_check (비율 검증)
    korean_ratio = 0.0
    if validate:
        is_valid, korean_ratio, status = korean_quality_check(text)
    
    # 3. compute_korean_quality (AI 패턴 탐지, 선택)
    quality_summary = None
    if humanize and len(text) >= 50:
        from core.korean_humanize import compute_korean_quality, get_quality_summary
        quality_summary = get_quality_summary(compute_korean_quality(text, genre=genre))
    
    return text, korean_ratio, quality_summary
```

### Phase 2: P0 핵심 수정 (가장 빈번한 3개 경로)

#### 2-1. ollama.py `/api/chat` non-streaming (line 2487)
```python
# BEFORE: 비율 체크만
response_text = resp.json().get("message", {}).get("content", "")
is_valid, korean_ratio, _ = validate_korean_quality(response_text)

# AFTER: 필터링 → 검증
from core.korean import postprocess_korean
response_text = resp.json().get("message", {}).get("content", "")
if not skip_korean:
    response_text, korean_ratio, _ = postprocess_korean(response_text)
    is_valid = korean_ratio >= 0.3
```

#### 2-2. ollama.py `/api/chat` streaming finally (line 2661)
```python
# BEFORE: raw 캐시/세션
full_text = "".join(_full_response)
_llm_response_cache.set(_ck, full_text)
append_chat_message(sess["id"], "assistant", full_text, None)

# AFTER: 필터링 후 저장
from core.korean import force_korean, korean_quality_check
full_text = force_korean("".join(_full_response))
_, ratio, _ = korean_quality_check(full_text) if full_text else (False, 0.0, "")
_llm_response_cache.set(_ck, full_text)
append_chat_message(sess["id"], "assistant", full_text, ratio)
```

#### 2-3. chat.py `/api/chat` non-streaming (line 291)
```python
# BEFORE: 비율 체크만 (validate_korean_quality)
response_text = _call_ollama(selected_model, messages, stream=False)
final_text, is_valid, korean_ratio, attempts = _validate_and_retry(...)

# AFTER: _validate_and_retry 내부에 force_korean 추가
```

#### 2-4. chat.py `/api/generate` (line 366)
```python
# BEFORE: raw 스트림
yield line.decode() + "\n"

# AFTER: 토큰 필터 적용 (최소한)
decoded = line.decode()
# JSON 파싱 → response 필드에 force_korean_token 적용 → 재직렬화
```

### Phase 3: 스트리밍 비대칭 수정 (5건)

| 엔드포인트 | 수정 내용 |
|-----------|----------|
| writing.py `/api/writing/draft/stream` | finally에 force_korean 적용 + 캐시 저장 |
| writing.py `/api/writing/craft/stream` | 동일 |
| story.py `/api/story/generate/stream` | 동일 |
| novel.py `/api/novel/chapter/write/stream` | 동일 |
| hr_law.py `/api/hr-law/ask/stream` | 동일 |

### Phase 4: Non-streaming 보강 (도메인별)

| 파일 | 대상 | 수정 |
|------|------|------|
| game.py | dialogue group (line 579) | JSON text 필드에 force_korean |
| triage.py | triage 응답 텍스트 | postprocess_korean |
| medical_advanced.py | reasoning/auto-record | postprocess_korean |
| hr_law.py | check/guide 5종 | postprocess_korean |

### Phase 5: 캐시/세션 오염 방지

- 모든 캐시 저장 전 `force_korean` 통과 의무화
- `append_chat_message`의 `korean_ratio` 파라미터 활용 (현재 None 전달 다수)
- 오케스트레이터 단계 간 전달 시 force_korean

## 수정 대상 파일 (우선순위순)

| # | 파일 | Phase | 수정 건수 | 영향도 |
|---|------|-------|----------|--------|
| 1 | core/korean.py | 1 | 1 (postprocess_korean 추가) | 전체 |
| 2 | routes/ollama.py | 2 | 2 (non-stream + stream) | **최빈도 호출** |
| 3 | routes/chat.py | 2 | 2 (non-stream + generate) | 빈도 높음 |
| 4 | routes/writing.py | 3 | 2 (stream 2건) | 중간 |
| 5 | routes/story.py | 3 | 1 (stream) | 중간 |
| 6 | routes/novel.py | 3 | 1 (stream) | 중간 |
| 7 | routes/hr_law.py | 3+4 | 2 (stream + check) | 낮음 |
| 8 | routes/game.py | 4 | 2 (dialogue) | 낮음 |
| 9 | routes/triage.py | 4 | 2 | 낮음 |
| 10 | routes/medical_advanced.py | 4 | 4 | 낮음 |

## 핵심 원칙

1. **필터링(force_korean) → 검증(quality_check)** 순서 엄수
2. **스트리밍**: 토큰은 `force_korean_token`, 최종 조립은 `force_korean` 이중 적용
3. **캐시/세션 저장 전** 반드시 `force_korean` 통과
4. **신규 파일 생성 안 함** — 기존 core/korean.py에 `postprocess_korean` 추가
5. **모범 사례**: routes/counseling.py 패턴 참조

## 참조

- 모범 패턴: `routes/counseling.py` (양방향 완전 적용)
- 기존 통합 함수: `core/korean.py:generate_korean()` (hr_law/game/medical 사용 중)
- 핵심 갭: ollama.py:2487 (force_korean 미적용), ollama.py:2664 (캐시 오염)
