---
name: P2-3 HR Law AI Quality - Design
description: HR Law AI 보안·품질 개선 상세 설계 — 4 Step 구현 명세
type: design
---

# P2-3: HR Law AI 품질 개선 — 설계서

**Status**: Design Phase  
**Feature**: P2-3-HR-Law-AI-Quality  
**Date**: 2026-05-20  
**Plan Reference**: docs/01-plan/features/P2-3-HR-Law-AI-Quality.plan.md

---

## Step 1: Critical 버그 수정

### 1-1. hr_law_ask() — UnboundLocalError 수정

**현재 코드 (L310-332):**
```python
try:
    conn = _get_hr_law_db()
    rule_rows = conn.execute(...)
    for rr in rule_rows:
        context_parts.append(...)  # ← 미초기화!
        sources.append(...)         # ← 미초기화!
    conn.close()
except:
    db_rows = []

context_parts = []  # ← 여기서 초기화 (늦음)
sources = []
```

**수정 설계:**
```python
context_parts = []  # 먼저 초기화
sources = []
try:
    conn = _get_hr_law_db()
    rule_rows = conn.execute(...)
    for rr in rule_rows:
        context_parts.append(...)
        sources.append(...)
except Exception as e:
    logger.warning("HR Law DB query failed: %s", e)
finally:
    try:
        conn.close()
    except Exception:
        pass
```

### 1-2. hr_law_ask_stream() — 동일 패턴 수정 (L412-434)
- 동일한 초기화 순서 수정 적용

---

## Step 2: 인증 강화

### 2-1. require_auth 전체 적용

**변경 파일:** `routes/hr_law.py`

```python
from core.security import require_auth
```

**적용 대상 (23개 전체):**

| 라우트 | 데코레이터 | admin 체크 |
|--------|-----------|:----------:|
| POST /ask | @require_auth | - |
| POST /ask/stream | @require_auth | - |
| POST /search | @require_auth | - |
| GET /sources | @require_auth | - |
| GET /stats | @require_auth | - |
| GET /history | @require_auth | - |
| POST /tenant/register | @require_auth | ✅ admin |
| GET /tenants | @require_auth | ✅ admin |
| PUT /tenant/<id> | @require_auth | ✅ admin |
| DELETE /tenant/<id> | @require_auth | ✅ admin (기존) |
| POST /tenant/<id>/regenerate-key | @require_auth | ✅ admin (기존) |
| GET /tenant/<id>/stats | @require_auth | ✅ admin |
| POST /rules | @require_auth | - |
| GET /rules | @require_auth | - |
| DELETE /rules/<id> | @require_auth | - |
| GET /rules/template | @require_auth | - |
| GET /rules/completeness | @require_auth | - |
| POST /rules/generate | @require_auth | - |
| POST /rules/review | @require_auth | - |
| GET /tenant/usage | @require_auth | - |
| POST /calculate/annual-leave | @require_auth | - |
| POST /calculate/overtime | @require_auth | - |
| POST /calculate/severance | @require_auth | - |

### 2-2. admin 체크 추가 (3개)

기존 `is_admin_request()` 함수 사용:
- `/api/hr-law/tenants` (list) — admin만 전체 목록 조회
- `/api/hr-law/tenant/<id>` (update) — admin만 수정
- `/api/hr-law/tenant/<id>/stats` — admin만 통계 조회

### 2-3. tenant stats API key 노출 방지

```python
# SELECT * → 명시적 컬럼 (api_key 제외)
t = conn.execute(
    "SELECT id, name, plan, max_queries_day, is_active, created_at FROM tenants WHERE id=?",
    (tenant_id,)).fetchone()
```

---

## Step 3: 에러 처리 개선

### 3-1. bare except → except Exception + 로깅

**16개소 일괄 변경 패턴:**

```python
# Before
except:
    rag_results = []

# After
except Exception as e:
    logger.warning("HR Law RAG search failed: %s", e)
    rag_results = []
```

### 3-2. conn.close() 안전성

DB 접근 패턴을 try/finally로 통일:

```python
conn = _get_hr_law_db()
try:
    # DB 작업
    result = conn.execute(...)
finally:
    try:
        conn.close()
    except Exception:
        pass
```

---

## Step 4: 코드 정리

### 4-1. datetime 통일
- `datetime.utcnow().isoformat() + "Z"` → `datetime.now(timezone.utc).isoformat()`로 통일
- 또는 SQLite `datetime('now')` 함수로 통일

### 4-2. 미사용 import 정리
- 중복 `secrets` import 제거

---

## 검증 기준 (Match Rate 항목)

| # | 검증 항목 | 기준 |
|---|----------|------|
| 1 | context_parts/sources 초기화 순서 | try 블록 전 초기화 |
| 2 | ask_stream 동일 수정 | try 블록 전 초기화 |
| 3 | require_auth import | 파일 상단 존재 |
| 4 | @require_auth 적용 수 | 23개 라우트 전체 |
| 5 | admin 체크 추가 | tenants list, update, stats 3개 |
| 6 | tenant stats api_key 제외 | SELECT에서 api_key 없음 |
| 7 | bare except 제거 | grep "except:" 결과 0건 |
| 8 | 에러 로깅 추가 | except 블록에 logger 호출 |
| 9 | conn.close() 안전성 | try/finally 패턴 |
| 10 | 구문 검증 통과 | py_compile 성공 |
