# v2.125.30 — 연동 표면 설명 보강 + 잘못된 판단 정정

날짜: 2026-08-31 | 선행: v2.125.29 전체 API 감사

## 1. 🔴 정정 — flasgger 탓이 아니었다

v2.125.29 초안은 "`description` 없음 560건은 **flasgger 동작**이므로 결함이 아니다"라고 적었다.
**틀렸다.** 라이브 스펙을 직접 확인하니 flasgger는 docstring 첫 줄을 `summary`로,
**`---` 앞 본문을 `description`으로** 싣고 있었다:

- `/api/medical/catalog` → description 있음(본문 산문이 그대로 실림)
- `/api/version` → description 있음
- `/api/code/review` → description **없음**(docstring이 한 줄 요약뿐)

description을 가진 것은 591 중 **31건뿐**이고, 나머지 **560건은 진짜 문서 공백**이었다.
기전을 확인하지 않고 도구 탓으로 돌릴 뻔했다 — 이 레포가 반복해서 배운 실패 모드다.

## 2. 처치 — 우선순위는 "연동 표면"

560건을 일괄 생성하지 않는다. 지어낸 산문은 문서-코드 드리프트를 만들고, 검증할 수 없는 설명은
없느니만 못하다. 대신 **의료 카탈로그가 실제로 가리키는 23개 경로**를 우선 대상으로 잡았다 —
내부 시스템이 처음 붙는 표면이 정확히 그 집합이다.

측정: 23개 중 **14개에 설명 없음** → **13건 보강**(1건은 이미 본문 보유).

| 경로 | 보강한 내용 |
|---|---|
| `/api/medical-law/ask` | 근거 세 갈래 병합(법령 3단비교+고시 벡터검색+시드 조문)·인용 라벨·**자문 보조 고지**·코퍼스 현황 확인 경로 |
| `/api/medical-law/review` | 위험밴드 + 근거 인용 강제·자문 보조 고지 |
| `/api/medical/dur/drug` | 성분명/DUR 코드로 병용금기 상대 조회 · 조합 검사는 `dur/check` · 정본·갱신 경로 |
| `/api/medical/dur/stats` · `/drug-info/stats` | 기대치와 다르면 동기화 실패 의심 → `sync-status` 확인 |
| `/api/medical/code/{kcd,edi,mapping}` | 각 코드계의 용도와 **서로 넘어가는 경로** |
| `/api/medical/cli/execute` | 참조 데이터(`claim_rules`·`denial_reasons`)·의도 불확실 시 `suggest` |
| `/api/medical/knowledge/search` | 하이브리드(벡터+키워드+재랭킹) · **의료법은 별도 코퍼스**임을 명시 |
| `/api/notice/fee-codes/stats` · `drug-master/stats` · `drug-substitution/stats` | 적재 현황과 **개별 조회 경로로의 연결** |

모든 설명은 **기존 docstring·코드에서 확인된 것만** 적었다. 특히 의료법 두 경로에는
"자문 보조이며 법률 자문이 아니다"를 스펙 본문에 넣었다 — 연동자가 스펙만 보고 붙는 경우를 위해서다.

## 3. 검증

- `tests/test_medical_catalog.py`에 회귀 1건 추가 — **카탈로그가 가리키는 모든 경로가
  산문 설명을 갖는지** 전수 검사. 데이터셋을 추가하면서 설명 없는 경로를 가리키면 실패한다.
- 관련 회귀 53 passed(카탈로그 37 + API 표면 16).

## 4. 남긴 것

- `description` 없음 잔여 ≈547건 — 도메인별로 나눠 후속 처리. **일괄 생성은 하지 않는다.**
- `responses` 없음 131건 · 응답 schema 전무 174건 — 라우트마다 실제 반환을 읽어야 한다.
