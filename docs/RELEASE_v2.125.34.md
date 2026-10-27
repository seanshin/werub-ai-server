# v2.125.34 — 일반 AI 표면 신설(의료 AI와 분리) + HyperCLOVA 커뮤니티 모델 + heavy 모델 제자리 갱신

2026-09-03 · HEAD `8b7ddc1`+ · 회귀 **2072 passed · 9 skipped · 0 failed**(신규 18)

## 1. 배경 — 주간 모델·엔진 스캔이 시작점

`scripts/check_model_updates.py` 실측(보유 39종 대조): **36 최신 · 제자리 갱신 3건**
(`qwen3.6:35b-a3b`(heavy 기본값)·`qwen3.6:27b`·`gemma4:26b`) · Ollama 0.32.5 ↔ 최신 **0.33.2**.
웹 조사 결과 도메인별 공백 없음 — 의료(MedGemma 1.5=현행 최신·med42 후속 없음)·
STT(whisper large-v3 후속 미출현)·비전·임베딩 전부 현행 유지가 정답.

신규 후보는 둘뿐이었고 하나는 기각, 하나는 용도를 바꿔 채택했다(§2·§5).

## 2. HyperCLOVA 라이선스 판독 — Omni-8B 기각, SEED-Text 1.5B를 일반 표면 전용으로 채택

- 🔴 **Omni-8B(멀티모달)는 기각**: ① 커스텀 아키텍처라 **GGUF/Ollama 구동 불가**
  (공식·커뮤니티 변환본 모두 부재·사이드카는 sm_120 인프라 벽) ② 라이선스가
  *"unauthorized or unlicensed professional services, including … medical/health"* 를
  금지 — 의료 표면에 얹을 수 없는 모델이다.
- ✅ **SEED-Text-Instruct 1.5B(Q8_0 GGUF)는 커뮤니티/일반 용도로 채택**(운영자 지시:
  의료 아닌 커뮤니티 용도). 같은 금지 조항이 있으므로 **일반 표면에만 배선하는 것
  자체가 라이선스 준수 장치**다. 부수 의무 이행: 응답에 `powered_by: "HyperCLOVA X"` 고지.
- 설치: `ollama pull hf.co/cherryDavid/HyperCLOVAX-SEED-Text-Instruct-1.5B-Q8_0-GGUF`
  → `ollama cp` → **`hyperclova-seed:1.5b`**(1.9GB·경량이라 fast-lane 대상 크기).
  라이브 검증: 한국어 chat 자연 응답·`done_reason: stop`·`think:false` 400 없음
  (새 모델은 실입력으로 response 확인 — 메모리 규율).

## 3. `/api/general/*` — 일반 AI 표면 (routes/general_ai.py, 4 엔드포인트)

운영자 지시: *"일반 AI와 의료 AI가 구분되는 형태로 서버 구성"*. 분리는 문서가 아니라
**코드가 보증한다**:

1. **모델 allowlist** — 이 표면은 `qwen2.5:14b`·`qwen2.5:7b`·`exaone3.5:7.8b`·
   `hyperclova-seed:1.5b`만 호출 가능. 목록 밖 `model`(의료 모델 포함)은 **400**.
   회귀가 이름 패턴(`med|bio|clinic…`)으로 allowlist 재유입을 소스 수준에서 차단.
2. **시스템 프롬프트 계약** — 의료·법률 등 전문 자문 요청엔 직접 판단 대신 전용
   표면(의료 AI)을 안내.
3. **라이선스 배선** — HyperCLOVA는 이 표면 전용(§2).

| Method | Endpoint | 설명 |
|---|---|---|
| POST | `/api/general/chat` | 일반/커뮤니티 채팅(profile=assistant\|community·history 20건 캡) |
| POST | `/api/general/chat/stream` | NDJSON 스트림(`{"delta"}`…`{"done":true,…}`) |
| GET | `/api/general/models` | 허용 모델+가용성(조회 실패 시 `available: null` — 결측≠false) |
| GET | `/api/general/status` | 분리 계약 명시(`separation.enforcement`) |

규율 이행:
- **무성 실패 금지**(v2.125.16 클래스): LLM HTTP 오류·전송 예외·빈 응답 = **502**,
  스트림 중단은 무언 종료 대신 `{"error"}` 줄(v2.125.13 규율).
- **라우터 경유**(D1): 신설부터 `model_router.chat/chat_stream`·라우팅 표에
  `general.chat`·`general.community_chat` 등록(코드에 모델명 없음)·MIGRATED 등재.
- Swagger: `General AI` 태그 선언(의료 블록과 구분 배치)·security·responses 스키마.
- history의 `system` 역할은 걸러진다(프롬프트 주입 최소 방어) — 시스템 프롬프트는
  서버 것 하나뿐.

⚠️ **회귀가 내 결함 2건을 잡았다**: 400 description에 넣은 JSON 리터럴 예시가
docstring YAML 파서를 깨뜨렸고(→ 라이브 openapi 스펙 테스트 연쇄 실패), General AI
태그 설명을 문자열 이어붙이기로 써서 태그 설명 전수 검사 정규식 밖이었다. 둘 다
v2.125.29~31에서 만든 감사 회귀가 잡은 것 — 그 감사가 첫 실전 가치를 증명했다.

## 4. heavy 기본값 `qwen3.6:35b-a3b` 제자리 갱신 (07d35212591f → 096fdbd02fe6)

레지스트리 갱신 감지(§1). heavy 기본값은 grounded A/B로 베이스라인을 잡은 모델이라
단순 pull이 아니라 **롤백 확보 + 전/후 결정적 프로브**로 진행했다:

1. **롤백 태그**: `ollama cp qwen3.6:35b-a3b qwen36-35b-a3b-pre0903`(모델 디렉터리
   파일 권한 없이 blob GC를 막는 방법 — pull의 "removing unused layers"에서 구판 보존).
2. **사전 프로브**: seed 42·temp 0·`think:false`·4프롬프트(한국어 추론·grounded·
   JSON·요약) → `/tmp/heavy_pre.json`.
3. pull(21GB) → 크기 **23GB→22GB**(실제 가중치/양자화 변경·템플릿 수정 아님).
4. **사후 diff = 2/4 IDENTICAL**: 🔑 **grounded·요약은 완전 동일**(heavy tier의
   존재 이유인 축). ko-reason은 표현 다른 정상 답변. ⚠️ json-fmt는 "과" 필드가
   나빠졌다(진료과 대신 입력 문장 반복) — **N=1이고 heavy의 실사용 경로 아님**
   (JSON 추출은 triage/qwen3:8b 소관)이라 차단 사유로 보지 않되 정직 기록.
5. **채택 + 롤백 보존**: 문제 발생 시
   `ollama cp qwen36-35b-a3b-pre0903 qwen3.6:35b-a3b` 한 줄로 복원.
   프로브 원자료 `/tmp/heavy_{pre,post}.json`(GPU).

**후보 보존분 `qwen3.6:27b`·`gemma4:26b`는 갱신하지 않는다** — 보존 목적이
"평가 당시 판의 재현"이라 제자리 갱신은 보존을 깨뜨린다. 필요 시점에 그 시점 판으로.

## 5. 백로그 기록(코드 변경 없음)

- **Ollama 0.33.2**(08-27): 변경분은 Claude Desktop 게이트웨이·KV prefill 복원·MLX —
  현행 결함을 고치는 것 없음 ⇒ **D3 규율 유지**(트리거 없이 업그레이드 금지).
- **Qwen3.8-27B**(08-13·Apache 2.0·비전 내장·262K ctx): dense 28B·기본 Q4 18GB로
  **16GB 부적합 클래스**(v2.125.17 스캔에서 dense 27B+ 기각과 동일). 16GB에 드는
  UD-Q3_K_XL 13.4GB는 품질 미검증 — 후보 기록만, 채택은 grounded A/B 필수.
- **HyperCLOVA Omni-8B**: 기각 사유 §2(재평가 트리거=llama.cpp 아키텍처 지원 +
  일반 표면 수요).

## 6. 배포·검증

- 회귀 클린 전체 실행 **2072 passed · 9 skipped · 0 failed**(GPU).
- 배포 `scripts/deploy.sh`(pull→실DB dry-run→회귀→HUP→게이트) — 결과는 §7.

## 7. 배포 결과 — 게이트 177/177 · 라이브 전 항목 검증

- 배포 `2026-09-03 14:19→14:28` · **게이트 통과 177/177 · 베이스라인 0건 · 신규 실패 없음**
  (로그 `logs/deploy_20260903_v2.125.34.out`) · HEAD `ce0192c` · 라이브 `2.125.34` ·
  health 200 · endpoints **594→598**.
- 라이브 검증(전부 실호출):
  - `GET /api/general/status` — 분리 계약 3장치 노출·기본 모델 정합(assistant=qwen2.5:14b·
    community=hyperclova-seed:1.5b).
  - `GET /api/general/models` — 4모델 전부 `available: true`·hyperclova에 `powered_by`.
  - **의료 모델 차단 라이브 확인**: `model=medgemma:4b` → **400** + allowed 목록.
  - community chat — hyperclova 한국어 응답 **1.79s**·`powered_by: "HyperCLOVA X"`.
  - stream — `{"delta"}` 조각·마지막 `{"done":true, model, tokens, profile, powered_by}`.
  - assistant chat — qwen2.5:14b·powered_by 없음(고지는 HyperCLOVA에만).
- 프로브 원자료 보존: `docs/01-plan/references/heavy-inplace-update-20260903-{pre,post}.json`.

### 함정 재확인(이번 세션 실측)

- 장기 SSH가 원격에서 또 끊겼다(배포 감시 중 1회) — 배포는 nohup이라 무관·감시는
  짧은 접속 반복 로컬 루프로(기존 규율 유효).
- `pgrep -f "deploy.sh"`가 **ssh로 보낸 자기 명령줄을 매칭**해 완료 후에도 15분간
  RUNNING으로 보였다(메모리의 pkill/pgrep 함정과 같은 클래스 — 판정 문자열은
  브래킷 패턴이나 로그 마커로).
