# Release v2.93.0 — 의료 오케스트레이션 통합 (신규 능력 → 챗 도구 연결)

**2026-06-12** | 이전: [v2.92.1](RELEASE_v2.92.1.md)

## 배경 (라이브 점검 결과)

의료 RAG·API 오케스트레이션을 라이브 점검한 결과 **핵심 경로는 정상**이나, 최근 추가된
medical-law RAG·ERP/notice·저가약 대체조제 능력이 **챗 오케스트레이터(`/api/orchestrator/chat`)의
도구 레지스트리에 없어 대화형으로 접근 불가**(직접 API만 가능)한 갭을 확인.

라이브 점검 정상 확인:
- `/api/medical-law/ask` — 권위 위계(법령 우선) 인용 정상("진료기록 10년" = 의료법 시행규칙 제15조).
- `/api/notice/search` — notice_knowledge 5건 검색.
- `/api/notice/diagnose` — 코드 자동해석 정상(TARIFF `EB411`→[REVIEW_CRITERIA,TARIFF] HIGH 0.8 근거 5건 / DRUG `645302132`→[DRUG_MATERIAL,REVIEW_CRITERIA] 근거없음→보류=환각방지).
- `/api/notice/drug-substitution/<code>` — 대체 후보(동일 주성분코드·저가순) 정상.
- 컬렉션 건강: medical_law_knowledge 9,593 / medical_pub_knowledge 15,285 / notice_knowledge 4,964 / medical_knowledge 156.

## 변경 사항 (보완)

### 1. 오케스트레이터 도구 4종 등록 (config/tools.json 21→25)
qwen3:8b tool-calling이 대화형 질의를 신규 능력으로 자동 라우팅:
- `medical_law_ask` → `/api/medical-law/ask` — 의료법/건강보험 법령·고시·발간물 RAG 자문.
- `notice_search` → `/api/notice/search` — 시행 중 심사기준 검색.
- `notice_diagnose` → `/api/notice/diagnose` — 청구 삭감위험 평가(claim_line 중첩 객체).
- `drug_substitution` → `/api/notice/drug-substitution` — 저가약 대체조제 lookup.

### 2. diagnose ↔ drug-substitution 오케스트레이션
`POST /api/notice/diagnose`가 DRUG 청구 진단 시 `sub_lookup`을 동반 호출해 응답에
**`substitution`** 필드 추가(eligible + 저가 대체 후보). ERP가 한 번의 진단으로
**삭감위험 + 저가 대체 기회**를 함께 받는다(양 return path = 근거있음/보류 모두).

### 3. POST /api/notice/drug-substitution (도구 호환)
오케스트레이터 `ai:` 액션은 POST+JSON 디스패치라, GET `/<code>`와 동등한 POST(body `{code}`)
엔드포인트 추가. 기존 GET은 유지.

## 검증
- iMac: tools.json JSON 유효(25 tools), notice.py 문법, test_drug_substitution/test_notice_v287 통과.
- GPU(배포 후): 재기동 + `POST /api/orchestrator/tools/reload` → 신규 4도구 노출,
  `/api/orchestrator/chat` 의료 질의 라우팅, diagnose substitution 필드 라이브 확인.
