# Medical AI API Guide

> WeRUBLLMManager v2.40.0 | 24 Medical + 6 Vision + 6 Training + 7 CLI = 43 APIs | Multi-tenant
> Last Updated: 2026-05-20

## Quick Start

### 1. 테넌트 등록 (관리자)

```bash
curl -X POST https://example.org/ollama/api/medical/tenant/register \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-xxx" \
  -d '{
    "name": "서울내과의원",
    "plan": "standard",
    "specialty": "내과",
    "contact_name": "김의사",
    "contact_email": "dr.kim@hospital.example.org"
  }'
# → {"tenant_id": "abc123", "api_key": "CHANGE_ME", "plan": "standard"}
```

### 2. API 호출 (테넌트 키)

모든 진료 API는 `X-Medical-Key` 헤더 필수:

> **인증 정책 (2026-07-29, 보안 P2 M4)** — 의료 Blueprint는 **fail-closed**다.
> **로컬 · Bearer(관리자) · `X-Medical-Key`(테넌트) 중 아무것도 없으면 401**이다.
> 종전에는 `/api/medical/tenant*` 변경 요청만 인증을 강제해 임상 라우트 다수가 무인증으로
> 열려 있었다. **기존 호출자(HIS)는 영향 없다** — `X-Medical-Key`를 보내면 그대로 통과하고,
> 테넌트 검증은 각 라우트가 수행한다. CORS preflight(OPTIONS)는 면제.

```bash
curl -X POST https://example.org/ollama/api/medical/summarize \
  -H "Content-Type: application/json" \
  -H "X-Medical-Key: med-xxxxxxxx" \
  -d '{"text": "55세 남성, 흉통 3일...", "specialty": "내과"}'
```

---

## v2.39.0 신규 — 구조화 의료 질의 (`/api/medical/ask`)

> P2-2 Medical AI Quality (Match Rate: 96.9%)로 추가된 기능

### POST `/api/medical/ask`

구조화된 증상 입력으로 의료 AI에 질문. 4단계 분류 + 모델 자동 선택 + 면책 조항 자동 포함.

**Request:**
```json
{
  "symptoms": ["두통", "발열", "기침"],
  "duration": "3일",
  "severity": "중간",
  "patient_age": 35,
  "context": "최근 해외여행 다녀옴"
}
```

| Parameter | Type | Required | Description |
|-----------|------|:--------:|-------------|
| symptoms | array[string] | ✅ | 증상 목록 |
| duration | string | ❌ | 증상 지속 기간 |
| severity | string | ❌ | 심각도 (경미/중간/심각/응급) |
| patient_age | integer | ❌ | 환자 나이 |
| context | string | ❌ | 추가 맥락 (병력, 여행력 등) |
| question | string | ❌ | 하위 호환용 (기존 단순 질문 형식) |

**Response:**
```json
{
  "answer": "발열과 기침이 동반된 두통은 상기도 감염(감기) 가능성이 높습니다...",
  "category": "diagnosis",
  "model_used": "meditron:7b",
  "disclaimer": "⚠️ 이 정보는 AI가 제공하는 참고용 의학 정보입니다. 정확한 진단과 치료를 위해 반드시 의사와 상담하세요. 응급 상황 시 119에 연락하세요.",
  "structured_input": {
    "symptoms": ["두통", "발열", "기침"],
    "duration": "3일",
    "severity": "중간",
    "patient_age": 35,
    "context": "최근 해외여행 다녀옴"
  },
  "quality_check": {
    "korean_ratio": 0.92,
    "validated": true,
    "threshold": 0.70,
    "attempts": 1
  }
}
```

### 질문 분류 (4단계)

| 카테고리 | 키워드 예시 | 선택 모델 | 한국어 기준 |
|---------|-----------|----------|:----------:|
| `emergency` | 응급, 심정지, 쇼크, 대량출혈 | qwen2.5:14b (빠름) | 70% |
| `diagnosis` | 진단, 증상, 감별, 소견 | meditron:7b (전문) | 70% |
| `clinical` | 임상, 치료, 처방, 수술 | medllama2:7b (임상) | 70% |
| `general` | 건강, 운동, 식이, 예방 | qwen2.5:14b (범용) | 70% |

### 한국어 품질 검증 (의료 특화)

- **임계값**: 70% (일반 Chat 30%보다 엄격)
- **의료 용어 제외**: 25+ 의학 전문 용어는 한국어 비율 계산에서 제외
  - 예: ICD-10 코드, 약물명 (Aspirin), 해부학 용어 (ECG, SpO2)
- **재시도**: 최대 2회 자동 재시도 (RETRY_SUFFIX 프롬프트 강화)
- **면책 조항**: 모든 응답에 119 응급 연락처 포함

### 하위 호환

기존 `question` 파라미터도 지원:
```json
{"question": "두통이 3일째 계속됩니다"}
```
→ 내부적으로 `symptoms: ["두통"], duration: "3일"`로 파싱

---

## v2.39.0 신규 — 약물 상호작용 검사 (`/api/medical/drug-interactions`)

### POST `/api/medical/drug-interactions`

약물 목록에서 상호작용 검출. drug_interactions DB (61건) 대조.

**Request:**
```json
{"drugs": ["Warfarin", "Aspirin", "Amlodipine"]}
```

**Response:**
```json
{
  "interactions": [
    {
      "drug_a": "Warfarin",
      "drug_b": "Aspirin",
      "severity": "major",
      "description": "와파린+아스피린: 출혈 위험 현저 증가",
      "mechanism": "항혈소판+항응고 중복",
      "clinical_effect": "심각한 출혈",
      "management": "병용 시 INR 면밀 모니터링, 출혈 증상 관찰",
      "evidence_level": "A"
    }
  ],
  "drugs_checked": 3,
  "interaction_count": 1,
  "has_major": true,
  "disclaimer": "약물 상호작용 정보는 참고용이며, 최종 처방 판단은 의사가 수행합니다."
}
```

| severity | 의미 | 대응 |
|---------|------|------|
| `major` | 심각한 위험 | 병용 금기 또는 면밀 모니터링 |
| `moderate` | 주의 필요 | 용량 조절 또는 모니터링 |
| `minor` | 경미 | 관찰 |

---

## v2.91~95 신규 — 약물 데이터 스택 (약가·대체조제·DUR·코드 크로스워크)

HIRA/식약처 정본 데이터로 구성한 약물 통합 레이어. **코드 종류를 몰라도** 크로스워크로
해석되며(바코드·보험코드·성분코드 상호 변환), DUR(7유형)은 식약처 정본 API를 사용한다.
모두 **비식별**(코드/성분만) · 자문 보조(실시간 차단 아님, 최종 판단 처방의·약사).

| Method | Endpoint | 설명 |
|--------|----------|------|
| POST | `/api/medical/dur/check` | **DUR 7유형 점검**(식약처 정본) — `{"drugs":[성분명/코드], "age":?, "pregnant":?}` → 병용금기+임부/연령/노인/효능군중복/용량·기간 |
| POST | `/api/medical/dur/drug` · GET `/dur/drug/<code>` | 단일 약물 병용금기 상대 성분 |
| GET | `/api/medical/dur/stats` | DUR 적재 현황 |
| POST | `/api/notice/drug-master/resolve` · GET `/<code>` | **코드 크로스워크** — 표준코드(바코드)↔제품코드↔품목기준코드↔성분코드↔ATC |
| GET | `/api/notice/fee-codes/<code>` | 청구/약가 코드 → notice_type(약가목록 21,898 + 수가 7,138) |
| GET | `/api/notice/drug-substitution/<code>` | 저가약 대체조제 장려금 대상 + 저가 대체 후보 |
| GET | `/api/notice/drug-data/status` | 약물 데이터 스택 통합 현황(Admin) |

```bash
# 병용금기 (정본)
curl -X POST /api/medical/dur/check -d '{"drugs":["파록세틴","셀레길린염산염"]}'
# 환자 컨텍스트(임부·고령) — age/pregnant로 임부금기·연령금기·노인주의 게이팅
curl -X POST /api/medical/dur/check -d '{"drugs":["프로게스테론"],"pregnant":true,"age":70}'
# → {"has_contraindication":true,"contraindications":[{"reason":"세로토닌성 증후군...",...}]}

# 스캔 바코드(표준코드)로 약가·대체조제 조회 (크로스워크 자동 해석)
curl /api/notice/drug-master/8806519003301   # → 제품코드 651900330, 성분코드, ATC
curl /api/notice/drug-substitution/8806519003301   # → resolved_via 크로스워크 → 대체조제 정보
```

- **통합 동작**: `drug-substitution`·`fee-codes`는 입력이 제품코드가 아니어도(바코드/품목기준코드)
  `drug_master` 크로스워크로 자동 해석(`resolved_via` 표기). `medical/drug-analysis`는 LLM 분석에
  **DUR 정본 병용금기를 교차검증**해 응답 `dur_authoritative`로 동봉(환각 방지).
- **오케스트레이터 도구**: `dur_check`·`drug_resolve`·`drug_substitution`·`notice_diagnose`로
  자연어 질의("이 약들 같이 돼?", "이 바코드 무슨 약?") 자동 라우팅.
- 범위: DUR **7유형 정본**(병용금기·임부금기·연령금기·노인주의·용량주의·투여기간주의·효능군중복).
  식약처 DUR성분정보 오픈 API(`data.go.kr 1471000/DURIrdntInfoService03`)로 적재, 서비스키는 운영 시크릿.
  `age`/`pregnant` 비식별 컨텍스트로 임부·연령·노인 점검 게이팅(미제공 시 생략).

---

## v2.39.0 개선 — ICD-10 자동 검증

`/api/medical/summarize` 및 `/api/medical/analyze` 응답에 `icd_validation` 필드 자동 추가:

```json
{
  "icd_codes": ["J06.9", "I10"],
  "icd_validation": {
    "valid": [
      {"code": "J06.9", "name": "급성 상기도감염"},
      {"code": "I10", "name": "본태성 고혈압"}
    ],
    "invalid": [],
    "total": 2,
    "valid_count": 2,
    "accuracy": 1.0
  }
}
```

- KCD DB 469건 대조 (정확 매칭 + fuzzy prefix)
- 잘못된 코드 자동 감지, `invalid` 배열로 반환

---

## API Reference

### Tenant Management (관리자, Bearer 인증)

#### POST `/api/medical/tenant/register`
병원/의원 등록.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| name | string | ✅ | 병원명 |
| plan | string | | basic / standard / enterprise (기본: basic) |
| specialty | string | | 전문과목 |
| contact_name | string | | 담당자명 |
| contact_email | string | | 이메일 |
| contact_phone | string | | 전화번호 |

**Plans:**
| Plan | 일일 한도 | 월간 한도 | RAG 문서 |
|------|----------|----------|---------|
| basic | 100 | 2,000 | 100 |
| standard | 500 | 10,000 | 500 |
| enterprise | 무제한 | 무제한 | 무제한 |

#### GET `/api/medical/tenants`
전체 병원 목록.

#### PUT `/api/medical/tenant/<tenant_id>`
병원 정보 수정. (name, plan, specialty, contact_*, max_queries_*, is_active)

#### DELETE `/api/medical/tenant/<tenant_id>`
병원 및 모든 관련 데이터 삭제.

#### POST `/api/medical/tenant/<tenant_id>/regenerate-key`
API 키 재발급.

#### GET `/api/medical/tenant/<tenant_id>/stats`
병원별 사용 통계 (오늘/월간 쿼리, 액션별 분포).

---

### Clinical Support — Korean (X-Medical-Key 인증)

#### POST `/api/medical/summarize`
진료 기록 → SOAP 형식 요약.

**Request:**
```json
{
  "text": "55세 남성, 3일 전부터 좌측 흉통...(진료 기록)",
  "format": "soap",
  "specialty": "내과",
  "model": "qwen2.5:14b"
}
```

**Response:**
```json
{
  "summary": {
    "subjective": "3일 전부터 좌측 흉통, 운동 시 악화...",
    "objective": "BP 150/95, HR 88, ECG ST depression V4-V6...",
    "assessment": "불안정 협심증 의심 (I20.0), 고혈압 (I10)",
    "plan": "심장내과 협진, 관상동맥 CT, Aspirin 시작"
  },
  "icd_codes": ["I20.0", "I10"],
  "key_findings": ["ST depression V4-V6", "Troponin-I 0.08"],
  "urgency": "high",
  "disclaimer": "[면책] ...",
  "consultation_id": "uuid"
}
```

#### POST `/api/medical/summarize/stream`
스트리밍 버전. NDJSON으로 토큰 단위 실시간 반환.

#### POST `/api/medical/analyze`
증상/검사 → 감별진단 Top 3.

**Request:**
```json
{
  "symptoms": ["chest pain", "dyspnea"],
  "vitals": {"BP": "150/95", "HR": 88, "SpO2": 97},
  "labs": {"Troponin-I": 0.08, "BNP": 250},
  "history": "HTN 10yr, DM2 5yr",
  "age": 55,
  "sex": "M"
}
```

**Response:**
```json
{
  "differential_diagnosis": [
    {
      "diagnosis": "Unstable Angina",
      "diagnosis_ko": "불안정 협심증",
      "icd10": "I20.0",
      "probability": "high",
      "supporting": ["ST depression", "Troponin 상승", "HTN 병력"],
      "against": [],
      "recommended_tests": ["관상동맥 조영술", "Serial Troponin"],
      "urgency": "high"
    }
  ]
}
```

#### POST `/api/medical/reference`
의학 참고 (RAG 벡터 검색 + LLM 답변).

**Request:**
```json
{
  "question": "메트포르민 금기인 경우 대안 약물은?",
  "category": "drug",
  "n_context": 5,
  "model": "qwen2.5:14b"
}
```

**Response:**
```json
{
  "answer": "메트포르민 금기 시 SGLT2i, GLP-1RA를 고려...",
  "sources": [
    {"id": "drug-metformin", "title": "메트포르민 (Metformin)", "relevance": 0.728, "category": "drug"},
    {"id": "guide-당뇨", "title": "제2형 당뇨병 진료지침", "relevance": 0.645, "category": "guideline"}
  ]
}
```

Categories: `guideline`, `drug`, `disease`, `procedure`, `icd10`

#### POST `/api/medical/explain`
환자 설명문 생성.

**Request:**
```json
{
  "diagnosis": "불안정 협심증 (I20.0)",
  "treatment_plan": "관상동맥 CT + Aspirin 100mg",
  "patient_level": "general"
}
```

patient_level: `general` / `educated` / `elderly` / `child`

#### POST `/api/medical/draft`
의무기록 초안.

**Request:**
```json
{
  "type": "progress_note",
  "data": {
    "chief_complaint": "두통 3일",
    "findings": "신경학적 검사 정상",
    "assessment": "긴장성 두통",
    "plan": "Acetaminophen 500mg tid"
  }
}
```

type: `progress_note` / `discharge_summary` / `referral` / `consultation`

#### GET `/api/medical/history`
상담 이력 조회.

| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | 최대 200 (기본 50) |
| action | string | 필터: summarize/analyze/reference/explain/draft |

---

### Clinical Support — English (의료진 전용)

영어 의료 모델(medllama2/meditron)로 처리. `translate: true` 시 한국어 번역 추가.

#### POST `/api/medical/clinical/analyze`
감별진단 (영어 의료 모델 + 한국어 번역 옵션).

**Request:**
```json
{
  "symptoms": ["chest pain", "dyspnea on exertion"],
  "vitals": {"BP": "150/95", "HR": 88},
  "labs": {"Troponin-I": 0.08},
  "age": 55, "sex": "M",
  "translate": true
}
```

**Response:**
```json
{
  "analysis": {
    "en": "Based on the presentation of chest pain with ST depression...",
    "ko": "ST 하강을 동반한 흉통 소견에 근거하여..."
  },
  "model": "medllama2",
  "translated_by": "qwen2.5:14b"
}
```

#### POST `/api/medical/clinical/drug`
약물 정보/상호작용 (meditron).

```json
{"query": "metformin contraindications with contrast dye", "translate": true}
```

#### POST `/api/medical/clinical/translate`
영문 의학 텍스트 → 한국어 번역.

```json
{"text": "Hashimoto thyroiditis is the most common cause of hypothyroidism."}
```

---

### Knowledge Management

#### POST `/api/medical/knowledge/index`
의학 문서 색인 (SQLite + ChromaDB 자동 벡터 임베딩).

```json
{
  "title": "당뇨병성 신경병증 가이드라인",
  "content": "당뇨병성 신경병증은...",
  "category": "guideline",
  "source": "대한당뇨병학회",
  "shared": true
}
```

#### POST `/api/medical/knowledge/search`
지식 검색 (ChromaDB 벡터 유사도).

```json
{"query": "고혈압 약물", "category": "drug", "limit": 10}
```

#### DELETE `/api/medical/knowledge/<doc_id>`
문서 삭제.

#### GET `/api/medical/knowledge/stats`
지식 베이스 통계 (카테고리별 건수).

---

### Usage & Audit

#### GET `/api/medical/usage`
최근 30일 일별 사용량 (쿼리 수, 토큰 수).

#### GET `/api/medical/audit`
감사 로그 (action, endpoint, IP, 요청 요약, 상태 코드).

---

## Models

| 용도 | 모델 | 언어 |
|------|------|------|
| 환자 대면 (요약/설명/의무기록) | qwen2.5:14b | 한국어 |
| 의료진 임상 분석 | medllama2 | 영어 |
| 약물/가이드라인 | meditron | 영어 |
| RAG 임베딩 | bge-m3 (1024d) | 다국어 |

## Knowledge Base (156건)

| Category | Count | Examples |
|----------|-------|---------|
| icd10 | 50 | I10 본태성 고혈압, E11 제2형 당뇨병 |
| drug | 50 | 메트포르민, 아스피린, 아토르바스타틴 |
| guideline | 20 | 고혈압/당뇨/ACS/폐렴 진료지침 |
| disease | 16 | 고혈압, 당뇨, 심부전, 뇌졸중 설명 |
| procedure | 20 | CBC, BUN, Cr, HbA1c 참고치 |

## Security

- 테넌트 인증: `X-Medical-Key` 헤더 — **키는 sha256 해시로 저장**된다(2026-07-29·평문 미저장).
  기존 키는 첫 인증 시 자동 전환되므로 호출자 작업은 없다. 목록 API는 접두만 노출한다.
- Blueprint 가드: **fail-closed**(로컬·Bearer·X-Medical-Key 중 무엇도 없으면 401)
- 관리자 인증: `Authorization: Bearer sk-xxx`
- 감사 로그: 모든 API 호출 자동 기록 (입력 원문 제외)
- 의료 면책: 모든 응답에 자동 포함
- 일일/월간 쿼리 제한: 플랜별 차등


---

## Symptom Triage (Public API, 인증 불필요)

### POST `/api/medical/triage`
증상 -> 진료과 추천 (Top 3 + 응급 감지).

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| symptom | string | Y | 증상 (자연어, 최대 500자) |
| age | integer | | 나이 |
| sex | string | | M/F |
| duration | string | | 기간 ("3일", "1주") |
| severity | string | | mild/moderate/severe |

Response: `recommendations[]` (department, confidence, urgency) + `emergency_warning` + `guidance`

### POST `/api/medical/triage/follow-up`
대화형 추가 질문으로 증상 상세화.

### GET `/api/medical/triage/departments`
18개 진료과 목록 + 설명.

### GET `/api/medical/triage/common-symptoms`
14개 자주 묻는 증상 목록.


---

## Orchestrator

### POST `/api/orchestrator/run`
워크플로우 실행. input의 데이터가 각 단계에 자동 전달됩니다.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| workflow | string | Y | 워크플로우 이름 |
| input | object | Y | 입력 데이터 |

### POST `/api/orchestrator/chat`
자연어 → 워크플로우 자동 선택 + 실행.

### POST `/api/orchestrator/run/stream`
SSE 스트리밍. 단계별 실시간 진행.

### GET `/api/orchestrator/workflows`
사용 가능한 워크플로우 목록.

### GET `/api/orchestrator/status/<run_id>`
실행 상태 조회.

### GET `/api/orchestrator/history`
실행 이력.


---

## 의료 영상 판독 API (MedGemma 4B)

> v2.35.0 추가 | 현행 모델: dcarrascosa/medgemma-1.5-4b-it:Q8_0 (v2.107.0~, 폴백 medgemma:4b)

> ⚠️ **사용 조건 고지 (MedGemma HAI-DEF 약관 하류 전달 — 2026-07-20)**
> 본 API 계열(`/api/vision/medical/*`)의 기반 모델 MedGemma는 Google **Health AI
> Developer Foundations Terms**의 적용을 받으며, 호출자(HIS·트윈 등)에게도 다음 제한이
> 전달됩니다: ①출력을 **직접적인 환자 진단·치료 결정에 단독 사용 금지** — 임상 사용은
> 관할 규제기관의 승인(의료기기 허가 등)이 필요 ②HAI-DEF 금지 정책(Prohibited Use
> Policy) 준수 ③출력을 제3자에게 재제공할 경우 동일 제한을 전달할 것. 본 API의 모든
> 응답에 자문 보조 disclaimer가 강제되는 것은 이 조건의 이행 장치입니다.

### POST /api/vision/medical/analyze

의료 영상을 분석하여 구조화된 판독 보고서를 생성합니다.

**지원 모달리티:** xray, ct, mri, ultrasound, pathology, fundus, dermoscopy

```bash
curl -X POST https://example.org/ollama/api/vision/medical/analyze \
  -H "Authorization: Bearer sk-xxx" \
  -F "image=@chest_xray.jpg" \
  -F "modality=xray" \
  -F "body_part=chest" \
  -F "clinical_info=기침 2주, 발열 38.5도" \
  -F "language=ko"
```

**응답:**
```json
{
  "findings": "양측 폐야 하부에 경미한 침윤 소견...",
  "impression": "양측 하엽 폐렴 의심...",
  "recommendations": "임상 증상과 함께 종합 판단 필요. 항생제 치료 후 2주 후 추적 촬영 권고.",
  "model": "medgemma:4b",
  "modality": "xray",
  "body_part": "chest",
  "tokens": 412,
  "duration_ms": 2100,
  "disclaimer": "AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요."
}
```

### POST /api/vision/medical/report

비교 판독 — 현재 영상과 이전 영상을 비교하여 변화를 분석합니다.

```bash
curl -X POST https://example.org/ollama/api/vision/medical/report \
  -F "current_image=@current_cxr.jpg" \
  -F "previous_image=@previous_cxr.jpg" \
  -F "modality=xray" \
  -F "body_part=chest" \
  -F "clinical_info=폐렴 치료 2주 후 추적"
```

### GET /api/vision/medical/models

사용 가능한 의료 영상 분석 모델 목록을 반환합니다.

### POST /api/vision/medical/precheck (v2.39.2 신규)

영상을 AI 판독에 보내기 전 **의료 영상 적합성을 사전 검증**합니다.

```bash
curl -X POST https://example.org/ollama/api/vision/medical/precheck \
  -H "Authorization: Bearer sk-xxx" \
  -F "image=@image.jpg" \
  -F "expected_modality=xray"
```

**검증 항목:** 의료 영상 여부, 모달리티 자동 분류, 품질 점수(해상도/선명도/노출), 모달리티 불일치

**analyze 통합:** `precheck=strict` 파라미터로 부적합 영상 자동 거절 (422)

```bash
curl -X POST .../api/vision/medical/analyze \
  -F "image=@image.jpg" -F "precheck=strict" -F "modality=xray"
```

상세 사양: [PACS_INTEGRATION_GUIDE.md](PACS_INTEGRATION_GUIDE.md) 참조

> ⚠️ **면책 조항**: AI 의료 영상 분석은 참고용이며, 최종 판독은 반드시 영상의학과 전문의가 수행해야 합니다.
