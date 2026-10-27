# v2.125.51 — 성문 등록·대조·파기 `/api/speaker/*` 구현 (HIS VoiceEMR B2)

기준 v2.125.50 · 출처 = 연동 검토 회신 · 운영자 결정(2026-09-28)

## 0. 판단 정정 (먼저 기록)

같은 날 이 건을 "미제공 확정 → HIS에 호출 중단 안내"로 처리했다(`e311130`, 안내문 **미발송**).
운영자 정정: **"현재는 개발 중이니 계약이 된 상태로 인지하고 진행되어야 함."** HIS 주석
*"WeRU.B 계약 확정 후 활성"*을 '계약 없음'으로 읽은 것이 오판이었다 — 연동 상대가 호출부·설정 게이트·
fail-closed 처리까지 준비해 둔 기능은 개발 단계에서 **계약된 것으로 보고 구현**한다.
안내문은 삭제(이 릴리즈), 백로그 항목은 구현 완료로 갱신.

## 1. 계약 (정본 = HIS 호출부, 실물 판독)

`hospital-run/apps/api/src/modules/voice-emr/stt-chunk.client.ts` · `voice-emr.service.ts`:

| 호출 | 요청 | HIS가 읽는 응답 |
|---|---|---|
| `POST /api/speaker/enroll` | raw PCM 16-bit LE mono · `X-Speaker-Id`·`X-Sample-Rate`(16000)·`X-Prompt-Id` · Bearer | `{speaker_id, samples, quality}` — 404/405/501=미가용, 그 외 !ok=거절 |
| `POST /api/speaker/match` | 같음 | `{enrolled, score}` → HIS가 `isOwner = enrolled ∧ score ≥ τ(0.7)` |
| `DELETE /api/speaker/:id` | Bearer | ok 여부 — **404/405/501=미가용=파기 못함**(철회권 3상태) |

HIS 측: 표본 최소 3·최대 8 · 1.2초 미만 자체 거절 · 게이트 `voiceEmr.speaker.enabled`/`scribe.speaker.enabled` 기본 off ·
"성문은 1차 필터일 뿐 인증 아님"(오더/활력 확인 게이트 항상 유지).

## 2. 구현

- `core/speaker_store.py` — ECAPA-TDNN(`meeting_diarize._get_encoder` 재사용·같은 장치 선택) 임베딩을
  L2 정규화해 **합과 표본 수만** SQLite `speaker.db`(0600·gitignore)에 누적. **오디오 비저장.**
  `quality` = 새 표본과 기존 중심의 코사인(첫 표본 null) · `score` = 중심과의 코사인(-1~1).
- `routes/speaker.py` — 3 계약 경로 + `GET /api/speaker/status`(집계만·식별자 없음). 인증 = 기존 blueprint Bearer 훅.
  - 표본 검사: 1.0~30초(초과 413) · 무음(RMS<1e-3) · 형식(홀수 바이트) · 표본률 8k~48k(비16k는 resampy).
  - 표본 한도 20(HIS 8 — 무한 누적 방지·409).
  - **파기는 멱등 200**(없는 id도 `deleted:false`) — 404면 HIS가 "파기 못함"으로 읽어 재시도 포인터를 남긴다.
  - 미등록 대조는 오류가 아니라 `{enrolled:false, score:0}`. 모델 미가용 503 · 처리 실패 500+로그(무성 실패 금지).
  - 로그에는 speaker_id 원문 대신 짧은 해시.
- Swagger `Speaker` 태그 선언 · 레이트리밋(enroll 60/분·match 120/분).

## 3. 검증

- 신규 `tests/test_speaker_api.py` 22건(결정적 임베더 주입 — 모델 없이 계약): 누적·quality · 같은/다른 목소리 ·
  **HIS 판독 재현(isOwner)** · 미등록 · **파기 멱등(404 금지)** · 부적합 표본 5종 · id 형식 5종 · 표본률 ·
  8k 리샘플 · 한도 409 · 모델 미가용 503 · **오디오 비저장**(DB에 표본 바이트 부재) · status 무식별자 ·
  앱 배선+외부 틀린 키 401.
- **실모델 스모크(GPU)**: ECAPA 적재 3.1s(최초) → 표본당 **~9ms**(cuda) · 동일 신호 score 1.0 · 다른 합성 신호 0.44 · 파기 True→False.
- 전체 회귀 **2283 passed**.

## 4. ⚠️ 한계 (정직 기록)

- **실음성 판별력 미측정** — 이 박스에 사람 음성 녹음이 없어 합성 신호로 파이프라인만 확인했다.
  ECAPA 코사인은 같은 사람이라도 대개 0.5~0.8대라 **HIS 기본 τ=0.7은 오거부(본인 불인정) 쪽**일 수 있다
  — 안전측이지만, 실사용 전 등록 실음성으로 τ 보정이 필요하다(HIS 설정 `voiceEmr.speaker.threshold`).
- `speaker.db`는 생체정보 파생물 — 개발 단계라 **평문 SQLite(0600)**. 실서비스 전환 시 암호화·보관기한 결정 필요
  (실서비스 전환 조절 항목에 등재).
- Bearer 없는 octet-stream POST는 앱 CSRF 가드가 인증 전에 415로 막는다(기존 `/api/stt/chunk`와 동일) — HIS는 항상 Bearer.

## 5. 배포 게이트 — 176/177 차단 (이 릴리즈와 무관한 신규 결함 발견)

- 차단 항목 `story[story-korean-only]` 빈 응답 아님(77자 < 100). 이 릴리즈는 story 경로를 건드리지 않는다
  (변경=speaker 신규 모듈·blueprint 등록·Swagger 태그·버전 문자열).
- **단독 재실행 3회 = 통과 2 · 실패 1**, 실패분은 더 나빴다: 본문 `'비가 세차게 내리는 늦\n\n\n\n“”\n\n\n\n“”'`(24자·한국어 비율 0.38)
  — 단어 중간 절단 + **대사 따옴표만 남고 내용이 비었다** ⇒ 모델 출력에 외국 문자(중국어 표류 추정)가 섞이고
  한국어 필터가 문자 단위로 걷어내 **본문이 뼈대만 남은 채 200**으로 나가는 모양(반복 클래스 #1).
- ⚠️ 판정: **베이스라인 미등록**(흔들리는 신호를 묻으면 진짜 신호도 묻힌다). 코드는 라이브 반영 상태(`9b33135`) —
  speaker는 라이브 스모크 정상(enroll→match 1.0→delete→재삭제 false·외부 무인증 401). **story 결함은 별건 처치 대상**.
