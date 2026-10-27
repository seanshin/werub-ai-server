# v2.125.28 — 의료 데이터 카탈로그 + Swagger 배치: 내부 시스템 연동 진입점

날짜: 2026-08-31 | 함께: 외부 의료 소스 강제 재동기화(신규 개정판 적재)

## 1. 문제 — 공백은 "문서"가 아니라 "발견 지점"이었다

내부 시스템이 의료 데이터를 쓰려면 먼저 **무엇이 있고 어떤 API로 꺼내는지** 알아야 한다.
착수 전 실측:

- 의료 라우트 **105/108이 이미 Swagger docstring 보유** ⇒ 문서화는 공백이 아니다.
- 그런데 의료 엔드포인트가 **26개 태그로 흩어져** 있었다 — `Medical - DUR`·`Medical - DrugInfo`·
  `Medical - Code`·`Notice`·`Medical Law`… 1~3개짜리 태그가 다수.
- `swagger_template["tags"]`에는 **6개만 선언**돼 있었고(Queue·Ollama·Image·Heavy·Keys·Config)
  의료 태그는 하나도 없었다. Swagger UI는 미선언 태그를 **설명 없이 알파벳순으로 뒤에** 붙인다.
- 결과: 전체 622경로 안에서 연동자가 의료 표면을 파악할 방법이 **문서 통독뿐**이었다.

⚠️ 착수 초기 판정 정정: 표 이름으로 라우트를 grep해 `dur_interaction`·`drug_easy_info`가
"라우트 참조 없음"으로 보였으나 **위음성**이었다 — 라우트가 `core.dur`·`core.drug_info`를
경유한다(`/api/medical/dur/check`·`/api/medical/drug-info`는 존재). 탐지기를 먼저 의심해서
없는 결함을 보고하지 않았다.

## 2. 처치 ① — `GET /api/medical/catalog` (신규)

보유 데이터셋을 **기계가 읽는 형태**로 한 번에 낸다. 항목마다 건수·최신성·`last_sync_status`와
**그 데이터셋을 실제로 서빙하는 엔드포인트 목록**을 싣는다.

라이브 출력(2026-08-31):

| id | 데이터 | 건수 | 경과 |
|---|---|---|---|
| `drug_master` | 약가 마스터(제품·표준코드) | 305,522 | 0일 |
| `fee_codes` | 코드 마스터(수가·치료재료·약가) | 120,560 | 4일 |
| `drug_substitution` | 저가약 대체조제 | 11,020 | 0일 |
| `drug_easy_info` | 의약품 개요정보(e약은요) | 4,742 | 0일 |
| `dur_interaction` / `dur_single` / `dur_product` / `dur_split` | DUR 4종 | 1,325 / 3,015 / 3,791 / 2,077 | 0일 |
| `kcd_codes` / `edi_codes` / `dx_order_mapping` | 코드·매핑 | 469 / 256 / 100 | — |
| `claim_rules` / `denial_reasons` | 청구 규칙·삭감 사유 | 39 / 34 | — |
| `notices` / `law_articles` / `reportable_diseases` | 고시 색인·의료법 조문·법정감염병 | 91 / 20 / 20 | — |
| RAG `medical_law_knowledge` / `medical_knowledge` | 청크 | 13,527 / 156 | — |

`summary` = 16 데이터셋 · **전부 available** · 453,081행 · 최장 경과 4일.

설계 규율(이 레포가 반복해 배운 것 적용):
- **데이터셋별 fail-open** — 저장소 하나가 없어도 나머지를 낸다. 그 항목만 `available:false` +
  `error`. 조용히 빈 결과를 내지 않는다(1급 결함 클래스).
- **결측은 `null`** — 최신성을 못 재면 0이 아니라 null. 0으로 세면 "방금 갱신됨"으로 보인다.
- **집계만** — 레코드 본문·PHI 없음. 허용 키를 회귀로 화이트리스트 고정.

## 3. 처치 ② — Swagger 태그 배치 (한 곳에서)

`swagger_template`에 의료 태그를 **선언 + 순서 지정 + 설명**했다. Swagger UI는 선언 순서를
따르므로 `/docs`에서 의료 표면이 한 덩어리로 뜬다:

```
Medical - Catalog  ← 🔎 여기서 시작
Notice · Medical - DUR · Medical - DrugInfo · Medical - Code · Medical Law · Medical - Knowledge   (데이터)
Medical - Clinical · Triage · Drug Analysis · Advanced AI · Predictive · Imaging · Avatar …        (추론)
Medical - Models · Stats · Admin · Validation · Research · Training                                 (운영)
```

🔑 **라우트 docstring 100여 개를 건드리지 않았다** — 태그 이름은 그대로 두고 선언만 추가했다.
큰 diff 없이 배치 목적을 달성하고 회귀 위험을 만들지 않는다.
API `info.description`도 카탈로그를 진입점으로 명시하도록 고쳤다(제목만 바꾸면 배치의 목적이 사라진다).

## 4. 함께 수행 — 외부 소스 강제 재동기화

7일 가드를 우회해 지금 확인하도록 트리거했다. **HIRA 게시판은 `force=true`를 쓰지 않았다** —
동일 게시글 재적재는 정보 이득 없이 DB 전량 교체 위험만 남기기 때문이다(확인은 강제하되
적재는 신규일 때만).

- 🔴 **`약제급여목록 2026-09-01판 22,045건 적재`** (종전 21,998) — 7일 주기를 기다렸으면 놓쳤을 신규 개정판.
- `dur-sync refresh done: 9/9 ok (67s)` — DUR 8종 + e약은요 전량 재수집 성공.
- `저가약대체조제` skip(신규 게시글 없음 — 정상).

## 5. 검증

- 신규 회귀 36건 `tests/test_medical_catalog.py`:
  - 🔑 **카탈로그가 가리키는 엔드포인트가 실제로 코드에 존재하는지 전수 대조**
    (`{code}`→`<code>` 변환 후 라우트 소스 매칭). **이 테스트가 실제로 결함을 잡았다** —
    내가 추측으로 적은 `/api/medical/cli/codes`·`/api/medical/cli/rules`는 없는 경로였고,
    실제는 `/api/medical/code/kcd`·`/api/medical/code/edi`·`/api/medical/code/mapping`·
    `/api/medical/cli/execute`였다. 카탈로그가 없는 API를 가리키면 연동자를 잘못된 전제로 보낸다.
  - fail-open(전멸/부분 파손) · 결측 null · `…+00:00Z` 이형 타임스탬프 파싱 ·
    집계만 노출(키 화이트리스트) · 태그 선언 커버리지 · 카탈로그 태그가 첫 번째 ·
    description이 진입점을 가리킴.
- 전체 회귀: **§ 배포 로그 참조**.

## 6. 남긴 것

- 태그 **이름 자체**는 통합하지 않았다(`Medical - Clinical`/`Clinical EN`/`Clinical QA` 등).
  이름을 합치면 기존 소비자의 태그 필터가 깨지고 docstring 100여 개를 건드려야 한다 —
  배치(선언 순서·설명)만으로 목적이 달성되므로 **하지 않는 쪽을 택했다**.
- 카탈로그는 **집계 스냅샷**이다. 스키마(컬럼 정의)까지 원하면 별건으로 확장한다.
- 치료재료(data.go.kr, ~7분)는 이번 트리거 대상에 넣지 않았다 — `hira-drug/sync`의 기본
  `source=all`은 HIRA 게시판 2종만 돈다(치료재료는 `source` 명시 필요). 4일 전 수집분이 유효.
