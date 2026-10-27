# WeRU.B Medical AI — HIS 연동 가이드

> Version: v2.43.0 | 2026-05-20  
> API: 24 Medical + 8 Advanced + 6 FHIR + 7 CLI + 6 Vision + 6 Training = 57 endpoints  
> Multi-tenant | @require_auth 전체 적용 | FHIR R4 지원 | HIS SDK  
> Base URL: `https://example.org/ollama`  
> Swagger: `https://example.org/ollama/docs` (Medical 태그 참조)
>
> **v2.41.0~v2.43.0 주요 변경사항 (Phase 3+4):**
> - **P4-1 추론 체인** — 5단계 감별진단 파이프라인 (증상→감별→순위→검사→평가)
> - **P4-3 자동 의무기록** — 진료 대화 NER → SOAP + ICD-10 + 처방 자동 생성
> - **P4-2 구조화 영상 판독** — BI-RADS/Lung-RADS/TI-RADS/LI-RADS 등급
> - **P4-4 예측 분석** — 재입원(HOSPITAL Score)/합병증/약물부작용 예측
> - **P3-1 GPU 스케줄링** — 시간대별 모델 전략 + VRAM 자동 관리
> - **P3-2 우선순위 큐** — 응급(0)~배경(4) 5단계 도메인 우선순위
> - **FHIR R4 래퍼** — DiagnosticReport, DocumentReference, RiskAssessment, Observation
> - **HIS 연동 SDK** — `client/his_client.py` (일반 API + FHIR API 통합)
> - **파인튜닝 모델** — `werub-medical:7b` (QLoRA + Q4_K_M, 의료 heavy 모델)
> - 이전 변경: ICD-10 검증, 약물 상호작용, SOAP 분리 생성, 의료 영상 Precheck
> - PACS 연동 가이드: [PACS_INTEGRATION_GUIDE.md](PACS_INTEGRATION_GUIDE.md)

---

## 1. 개요

WeRU.B AI Server가 제공하는 **병원/의원용 AI 진료 지원 API**입니다.

| 기능 | 설명 | 모델 |
|------|------|------|
| **진료 요약** | 진료 기록 → SOAP 형식 구조화 | werub-medical:7b |
| **감별진단** | 증상/검사 → 상위 3개 감별진단 + ICD-10 | werub-medical:7b |
| **5단계 추론 체인** | 증상→감별→순위→검사→평가 파이프라인 | werub-medical:7b |
| **자동 의무기록** | 진료 대화 NER → SOAP + ICD-10 + 처방 | werub-medical:7b |
| **예측 분석** | 재입원/합병증/약물부작용 위험도 | werub-medical:7b |
| **의학 참고** | 약물/가이드라인 RAG 벡터 검색 + 답변 | werub-medical:7b + bge-m3 |
| **환자 설명문** | 진단/치료 → 환자 이해 수준 설명 | qwen2.5:7b |
| **의료진 임상 분석** | 영어 의료 모델 분석 + 한국어 번역 | medllama2/meditron |
| **약물 상호작용** | 97건 DB 기반 상호작용 체크 | 내장 DB |
| **ICD-10 검증** | KCD 469건 코드 자동 검증 | 내장 DB |
| **FHIR R4** | DiagnosticReport/DocumentRef/RiskAssessment | FHIR 래퍼 |
| **구조화 영상 판독** | BI-RADS/Lung-RADS 등급 판독 보고서 | medgemma:4b |

### 의학 지식 베이스 (156건, 벡터 검색)
- ICD-10 코드 50건 (순환기/내분비/호흡기/소화기 등)
- 약물 정보 50건 (용법/금기/부작용/상호작용)
- 진료 가이드라인 20건 (대한의학회 각 학회)
- 질환 설명 16건 (환자 설명문 생성용)
- 검사 참고치 20건 (CBC, 간기능, 신기능 등)

---

## 2. 인증

### 2-1. 테넌트 등록 (최초 1회)

관리자가 API 키를 발급합니다.

```bash
POST /api/medical/tenant/register
Content-Type: application/json
Authorization: Bearer sk-xxx   # 관리자 API 키

{
  "name": "서울내과의원",
  "plan": "standard",
  "specialty": "내과",
  "contact_name": "김의사",
  "contact_email": "dr.kim@hospital.example.org"
}
```

응답:
```json
{
  "tenant_id": "abc12345-xx",
  "api_key": "CHANGE_ME",
  "name": "서울내과의원",
  "plan": "standard"
}
```

> **`api_key`는 이 시점에만 전문이 표시됩니다. 반드시 저장하세요.**

### 2-2. API 호출 인증

모든 진료 API는 `X-Medical-Key` 헤더로 인증합니다.

```
X-Medical-Key: med-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 2-3. 플랜별 한도

| Plan | 일일 쿼리 | 월간 쿼리 | RAG 문서 |
|------|----------|----------|---------|
| basic | 100 | 2,000 | 100 |
| **standard** | **500** | **10,000** | **500** |
| enterprise | 무제한 | 무제한 | 무제한 |

한도 초과 시 `429 Too Many Requests` 응답.

---

## 3. 환경 설정

### HIS 서버 측

```env
MEDICAL_AI_URL=https://example.org/ollama
MEDICAL_AI_KEY=CHANGE_ME
```

### 연결 테스트

```bash
curl -H "X-Medical-Key: $MEDICAL_AI_KEY" \
  $MEDICAL_AI_URL/api/medical/knowledge/stats
```

응답:
```json
{"total": 156, "by_category": {"drug": 50, "guideline": 20, "icd10": 50, ...}}
```

---

## 4. API 상세

### 4-1. 진료 기록 요약 (SOAP)

가장 많이 사용하는 API. 진료 기록 원문을 SOAP 형식으로 구조화합니다.

```
POST /api/medical/summarize
```

**Request:**
```json
{
  "text": "55세 남성, 3일 전부터 좌측 흉통 호소. 운동 시 악화, 안정 시 호전. BP 150/95, HR 88, SpO2 97%. ECG ST depression V4-V6. Troponin-I 0.08ng/mL. 기왕력: 고혈압 10년, 당뇨 5년.",
  "specialty": "내과",
  "format": "soap"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| text | string | ✅ | 진료 기록 원문 |
| specialty | string | | 전문과목 (기본: 내과) |
| format | string | | soap / brief / detailed (기본: soap) |
| model | string | | 모델 지정 (기본: qwen2.5:14b) |

**Response:**
```json
{
  "summary": {
    "subjective": "3일 전부터 좌측 흉통, 운동 시 악화, 안정 시 호전. 고혈압 10년, 당뇨 5년.",
    "objective": "BP 150/95, HR 88, SpO2 97%, ECG ST depression V4-V6, Troponin-I 0.08ng/mL.",
    "assessment": "불안정 협심증 의심 (I20.0), 고혈압 (I10), 제2형 당뇨 (E11)",
    "plan": "심장내과 협진, 관상동맥 CT, Aspirin 100mg + Clopidogrel 75mg 시작"
  },
  "icd_codes": ["I20.0", "I10", "E11"],
  "key_findings": ["ST depression V4-V6", "Troponin-I 0.08 경미 상승"],
  "urgency": "high",
  "model": "qwen2.5:14b",
  "tokens": 236,
  "duration_ms": 3011,
  "consultation_id": "073f9db0-xxxx",
  "disclaimer": "[면책] 이 정보는 AI가 생성한 참고 자료이며..."
}
```

### 4-2. 진료 요약 (스트리밍)

실시간 토큰 단위 출력. SSE/NDJSON 형식.

```
POST /api/medical/summarize/stream
```

Request: 동일. Response: NDJSON 스트림.

```javascript
// JavaScript 예시
const response = await fetch(`${MEDICAL_AI_URL}/api/medical/summarize/stream`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-Medical-Key': MEDICAL_AI_KEY,
  },
  body: JSON.stringify({ text: clinicalNote, specialty: '내과' }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const lines = decoder.decode(value).split('\n');
  for (const line of lines) {
    if (!line.trim()) continue;
    const chunk = JSON.parse(line);
    if (!chunk.done) {
      // chunk.message.content → 실시간 표시
    }
  }
}
```

### 4-3. 감별진단

증상, 활력징후, 검사 결과 → 상위 3개 감별진단.

```
POST /api/medical/analyze
```

**Request:**
```json
{
  "symptoms": ["chest pain", "dyspnea on exertion"],
  "vitals": {"BP": "150/95", "HR": 88, "SpO2": 97},
  "labs": {"Troponin-I": 0.08, "BNP": 250, "WBC": 9500},
  "history": "HTN 10yr, DM2 5yr",
  "age": 55,
  "sex": "M"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| symptoms | array | 최소 1개 | 증상 목록 |
| vitals | object | | 활력징후 |
| labs | object | | 검사 결과 |
| history | string | | 기왕력 |
| age | integer | | 나이 |
| sex | string | | M / F |

**Response:**
```json
{
  "differential_diagnosis": [
    {
      "diagnosis": "Unstable Angina",
      "diagnosis_ko": "불안정 협심증",
      "icd10": "I20.0",
      "probability": "high",
      "supporting": ["ST depression V4-V6", "Troponin-I 상승", "고혈압 병력"],
      "against": [],
      "recommended_tests": ["관상동맥 조영술", "Serial Troponin", "심초음파"],
      "urgency": "high"
    },
    {
      "diagnosis": "NSTEMI",
      "diagnosis_ko": "비ST상승 심근경색",
      "icd10": "I21.4",
      "probability": "medium",
      "supporting": ["Troponin 상승", "ST 변화"],
      "against": ["Troponin 경미 상승"],
      "recommended_tests": ["Serial Troponin 3-6h 간격"],
      "urgency": "high"
    }
  ],
  "consultation_id": "f9f755dd-xxxx"
}
```

### 4-4. 의학 참고 (RAG 벡터 검색)

의학 지식 베이스(156건)에서 관련 문서를 벡터 검색하고, LLM이 답변을 생성합니다.

```
POST /api/medical/reference
```

**Request:**
```json
{
  "question": "제2형 당뇨 환자에게 메트포르민 금기인 경우 대안 약물은?",
  "category": "drug",
  "n_context": 5
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| question | string | ✅ | 의학 질문 |
| category | string | | drug / guideline / disease / procedure / icd10 (전체: 생략) |
| n_context | integer | | 참조 문서 수 (기본 5, 최대 10) |

**Response:**
```json
{
  "answer": "메트포르민 금기 시 SGLT2i(엠파글리플로진, 다파글리플로진)를 1차 대안으로 고려합니다. 심부전이나 CKD가 동반된 경우 특히 유리합니다. GLP-1RA(리라글루타이드)는 비만이 동반된 경우...",
  "sources": [
    {"id": "drug-metformin", "title": "메트포르민 (Metformin)", "relevance": 0.728, "category": "drug"},
    {"id": "guide-당뇨", "title": "제2형 당뇨병 진료지침", "relevance": 0.645, "category": "guideline"},
    {"id": "drug-empagliflozin", "title": "엠파글리플로진 (Empagliflozin)", "relevance": 0.601, "category": "drug"}
  ]
}
```

### 4-5. 환자 설명문

진단과 치료 계획을 환자가 이해할 수 있는 쉬운 한국어로 설명합니다.

```
POST /api/medical/explain
```

**Request:**
```json
{
  "diagnosis": "불안정 협심증 (ICD: I20.0)",
  "treatment_plan": "관상동맥 CT + Aspirin 100mg + Clopidogrel 75mg",
  "patient_level": "general"
}
```

| patient_level | 설명 |
|---------------|------|
| general | 일반 성인 |
| educated | 의학 상식이 있는 환자 |
| elderly | 고령 환자 (더 쉽게) |
| child | 소아 보호자 |

**Response:**
```json
{
  "explanation": "심장에 피를 공급하는 혈관이 좁아져서 가슴 통증이 발생하고 있습니다...",
  "key_points": ["심장 혈관 검사가 필요합니다", "혈전 방지 약물을 복용합니다"],
  "precautions": ["흉통 발생 시 즉시 응급실 방문", "약물 복용 중 출혈 주의"],
  "follow_up": "1주 후 외래 방문"
}
```

### 4-6. 의무기록 초안

진료 데이터를 의무기록 형식으로 작성합니다.

```
POST /api/medical/draft
```

**Request:**
```json
{
  "type": "progress_note",
  "data": {
    "chief_complaint": "두통 3일",
    "findings": "신경학적 검사 정상, BP 120/80",
    "assessment": "긴장성 두통",
    "plan": "Acetaminophen 500mg tid, 1주 후 f/u"
  }
}
```

type: `progress_note` / `discharge_summary` / `referral` / `consultation`

### 4-7. 상담 이력

```
GET /api/medical/history?limit=50&action=summarize
```

### 4-8. 사용량 통계

```
GET /api/medical/usage
```

```json
{"usage": [{"date": "2026-05-07", "query_count": 15, "token_count": 3200}]}
```

---

## 5. 의료진 전용 API (영어 의료 모델)

영어 의료 모델(medllama2/meditron)로 임상 분석. `translate: true` 시 한국어 번역 추가.

### 5-1. 임상 감별진단

```
POST /api/medical/clinical/analyze
```

```json
{
  "symptoms": ["chest pain", "dyspnea on exertion"],
  "vitals": {"BP": "150/95", "HR": 88},
  "labs": {"Troponin-I": 0.08, "BNP": 250},
  "history": "HTN 10yr, DM2 5yr",
  "age": 55, "sex": "M",
  "translate": true
}
```

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

### 5-2. 약물 정보

```
POST /api/medical/clinical/drug
```

```json
{
  "query": "metformin contraindications and interaction with contrast dye",
  "translate": true
}
```

```json
{
  "answer": {
    "en": "Metformin should be held 48h before and after iodinated contrast...",
    "ko": "메트포르민은 요오드 조영제 사용 전후 48시간 중단..."
  },
  "model": "meditron"
}
```

### 5-3. 영→한 의학 번역

```
POST /api/medical/clinical/translate
```

```json
{"text": "Hashimoto thyroiditis is the most common cause of hypothyroidism in iodine-sufficient areas."}
```

```json
{"translation": "하시모토 갑상선염(Hashimoto thyroiditis)은 요오드 충분 지역에서 갑상선기능저하증의 가장 흔한 원인입니다."}
```

---

## 6. 지식 관리

### 6-1. 병원 전용 문서 색인

```
POST /api/medical/knowledge/index
```

```json
{
  "title": "우리병원 당뇨 관리 프로토콜",
  "content": "제2형 당뇨 환자 초기 관리: 1) HbA1c 목표 6.5% ...",
  "category": "guideline",
  "source": "서울내과의원 내부 프로토콜",
  "shared": false
}
```

`shared: false` → 해당 병원만 검색 가능. `true` → 전체 공유.

### 6-2. 지식 검색

```
POST /api/medical/knowledge/search
```

```json
{"query": "당뇨 초기 약물", "category": "drug", "limit": 10}
```

### 6-3. 지식 통계

```
GET /api/medical/knowledge/stats
```

---

## 7. 에러 코드

| Code | Description |
|------|-------------|
| 200 | 성공 |
| 400 | 필수 파라미터 누락 |
| 401 | X-Medical-Key 누락 또는 잘못됨 |
| 404 | 테넌트/문서 없음 |
| 413 | 입력 텍스트 50KB 초과 |
| 429 | 일일/월간 쿼리 한도 초과 |
| 503 | LLM 서버 오류 |

---

## 8. 보안 및 규정

| 항목 | 조치 |
|------|------|
| 전송 | HTTPS 필수 (TLS 1.2+) |
| 인증 | X-Medical-Key 헤더 (병원별 고유 키) |
| 개인정보 | 환자 직접 식별 정보(이름, 주민번호) 전송 금지 권고 |
| 감사 로그 | 모든 API 호출 자동 기록 (입력 원문 미저장, 요약만) |
| 면책 | 모든 AI 응답에 자동 포함 |
| 한도 | 플랜별 일일/월간 쿼리 제한 |

### 면책 조항
> 이 정보는 AI가 생성한 참고 자료이며, 의료 행위에 해당하지 않습니다.
> 진단 및 치료 결정은 반드시 담당 의사의 전문적 판단에 따라야 합니다.

---

## 9. TypeScript/JavaScript 연동 예시

```typescript
const MEDICAL_AI_URL = process.env.MEDICAL_AI_URL;
const MEDICAL_AI_KEY = process.env.MEDICAL_AI_KEY;

async function medicalRequest(endpoint: string, body: object) {
  const response = await fetch(`${MEDICAL_AI_URL}${endpoint}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Medical-Key': MEDICAL_AI_KEY,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.error || `HTTP ${response.status}`);
  }

  return response.json();
}

// 진료 요약
const summary = await medicalRequest('/api/medical/summarize', {
  text: clinicalNote,
  specialty: '내과',
});
console.log(summary.summary.assessment);
console.log(summary.icd_codes);

// 감별진단
const dx = await medicalRequest('/api/medical/analyze', {
  symptoms: ['chest pain', 'dyspnea'],
  vitals: { BP: '150/95', HR: 88 },
  labs: { 'Troponin-I': 0.08 },
  age: 55,
  sex: 'M',
});
dx.differential_diagnosis.forEach(d => {
  console.log(`${d.probability} ${d.diagnosis_ko} (${d.icd10})`);
});

// 의학 참고 (RAG)
const ref = await medicalRequest('/api/medical/reference', {
  question: '메트포르민 금기 시 대안?',
  category: 'drug',
});
console.log(ref.answer);
ref.sources.forEach(s => console.log(`  ${s.title} (rel: ${s.relevance})`));

// 스트리밍 요약
const streamResponse = await fetch(`${MEDICAL_AI_URL}/api/medical/summarize/stream`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-Medical-Key': MEDICAL_AI_KEY,
  },
  body: JSON.stringify({ text: clinicalNote }),
});

const reader = streamResponse.body!.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const lines = decoder.decode(value).split('\n').filter(l => l.trim());
  for (const line of lines) {
    try {
      const chunk = JSON.parse(line);
      if (!chunk.done) {
        process.stdout.write(chunk.response || '');
      }
    } catch {}
  }
}
```

---

## 10. 연락처

| 항목 | 값 |
|------|-----|
| 관리자 | WeRU.B AI Team |
| Swagger | https://example.org/ollama/docs |
| 서버 상태 | https://example.org/ollama/api/health |


---

## 12. CLI 보강 로드맵 (v2)

현재 CLI v1은 기본 접수/바이탈/코드검색이 구현되어 있습니다.
v2에서 보강 예정:

- **코드 DB 대폭 확장**: KCD 500+, EDI 300+, 약품 200+
- **수납**: 자동 진료비 계산 (KCD -> EDI 매핑 -> 본인부담)
- **간호**: 바이탈 확장(RR/GCS/NRS), SBAR 기록, 약물 상호작용 DB
- **의사**: 가이드라인 기반 처방 추천, 오더세트 확장
- **청구**: 검증 규칙 DB, 삭감 사유 DB, 이의신청 템플릿
- **문맥**: 멀티턴 세션, 단축 명령어 30+

상세: docs/plan_medical_cli_v2.md


---

## 13. 증상 기반 진료과 추천 (Triage)

인증 불필요 (공개 API). 환자가 자연어로 증상을 설명하면 진료과를 추천합니다.

### 기본 사용
```
POST /api/medical/triage
{"symptom": "머리가 아프고 열이 나요", "age": 45, "sex": "F"}
```

### 응답
```
{
  "recommendations": [
    {"rank": 1, "department": "내과", "department_code": "IM", "confidence": 0.85},
    {"rank": 2, "department": "신경과", "department_code": "NR", "confidence": 0.70}
  ],
  "emergency_warning": null,
  "guidance": "발열 동반 두통은 내과 진료를 먼저 권장합니다."
}
```

### 응급 감지
흉통+호흡곤란, 편측 마비, 의식 소실 시 자동 응급 경고 + 119 안내.

### 대화형
```
POST /api/medical/triage/follow-up
{"symptom": "머리가 아파요"}
```

### 부가
- `GET /api/medical/triage/departments` - 18개 진료과
- `GET /api/medical/triage/common-symptoms` - 14개 증상


---

## 14. AI 오케스트레이터

단일 요청으로 복합 AI 워크플로우를 자동 실행합니다.

### 워크플로우 실행
```
POST /api/orchestrator/run
{
  "workflow": "quick_reception",
  "input": {"patient_name": "김영희", "symptom": "머리가 아파요", "age": 41}
}
```

응답:
```json
{
  "status": "completed",
  "steps": [
    {"id": "triage", "status": "completed", "duration_ms": 2500},
    {"id": "register", "status": "completed", "duration_ms": 50}
  ],
  "summary": {"completed": 2, "total_steps": 2, "total_duration_ms": 2550}
}
```

### 스마트 채팅
```
POST /api/orchestrator/chat
{"message": "김영희 머리 아프다고 접수해줘"}
```
자연어 → 의도 감지 → 워크플로우 자동 선택 + 실행.

### 사용 가능 워크플로우
- `quick_reception`: 증상 → 진료과 → 접수
- `full_consultation`: 증상 → 요약 → 감별진단 → 참고 → 설명문
- `nurse_assessment`: 바이탈 → 약물 확인 → 간호 기록
- `bill_calculate`: 질환 → 코드 매핑 → 진료비 계산
- `claim_review`: 코드 검증 → 문제 감지

### 기타 API
- `GET /api/orchestrator/workflows` — 워크플로우 목록
- `POST /api/orchestrator/run/stream` — SSE 스트리밍
- `GET /api/orchestrator/status/<run_id>` — 실행 상태
- `GET /api/orchestrator/history` — 실행 이력
