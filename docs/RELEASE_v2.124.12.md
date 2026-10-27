# RELEASE v2.124.12 — GET /api/models/routing (규제 R2 확장 — 모델 교체 자동 감지)

2026-07-20 | 이전: v2.124.11(규칙2 개정·캡 절단 마커) | 배경: 트윈 규제 요청 R2에서
트윈이 선호 방식으로 명시한 "모델 조회 API 전 경로 확장" 이행 — 사전 통보 부담을
자동 감지로 대체.

## 1. 엔드포인트

`GET /api/models/routing` (외부=Bearer, 캐시 5분·`?fresh=1` 우회)

- **설계 핵심 = 라이브 진실 반영**: 하드코딩 표가 아니라 실제 라우팅 함수
  (`_get_medical_model`·`_pin_public`·enhanced 파이프라인·vision/rag 상수)를 호출해
  조립 — 활성 설정(heavy 슬롯)·규제 가드(`medical_pin_public_models`) 변경이 응답에
  **즉시** 반영되므로, 트윈은 폴링만으로 모델 교체를 감지·기록할 수 있다.
- 응답: `server_version` + `engine{name,version}` + `pin_public_models` +
  `routes{경로: {model, digest(12자리), custom(자체 모델 여부), policy}}`.
  판본 3요소(태그+digest+엔진 버전) 원스톱.
- 특수 표기: DUR=`model:null`+"LLM 미사용"(결정적 DB) · `/v1`=caller-specified
  (권장 경로) · enhanced=단계별 pipeline 배열(single_model 정책 병기).
- fail-safe: digest/엔진 버전 조회 실패=null 강등(엔드포인트는 항상 200).

## 2. 검증

- GPU 회귀: test_model_routing 4(+model_pin 7=11 passed).
- 라이브: 전 경로 값이 수동 실측(reply2 Q2 표)과 정확 일치 — summarize=werubmedv2
  `custom:true`·predict/adverse-drug=meditron `ad11a6250f54`·DUR=null·enhanced 단계별
  digest·pin:false. graceful HUP(NRestarts 0)·health 200.

## 3. 트윈 사용법

```bash
curl -H "Authorization: Bearer <key>" https://example.org/ollama/api/models/routing
# 주기 폴링(예: 1일 1회) → routes[경로].digest 변화 = 모델 교체 감지
# custom=true 경로 = 자체 파인튜닝 모델 관여(유형① 감시 축)
```

사전 통보 합의는 유지(이 API는 이중 안전망). 인벤토리 문서(`mfds-model-inventory.md`)의
정적 표와 본 API의 라이브 값이 다르면 API가 사실.
