# Release v2.124.56 — 라이선스·저작자 표기 + 개발자 가이드(Ollama 개선점 정본)

날짜: 2026-07-29 | 종류: docs/legal | 배포: 기능 변경 없음(주석·문서만)

## 배경

**인수인계 가능한 상태**로 만든다. 이 코드베이스는 "Ollama를 그대로 쓰면 터지는 것들"을
하나씩 막으면서 자랐기 때문에, **이유를 모르면 불필요해 보이는 코드를 지우게 된다.**
그 이유를 남기는 것이 이번 작업의 목적이다.

## 1. `LICENSE` (신규)

**독점 라이선스** — Copyright (c) 2026 신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>.
단순 저작권 문구가 아니라 이 프로젝트에 실제로 필요한 조항을 포함한다.

- **⚕️ 의료 고지**: 의료기기 아님 · 자문 보조이며 진단·처방 대체 아님 · 자체 파인튜닝 모델이
  규제 대상 경로에서 서빙되지 않도록 `medical_pin_public_models` 가드로 차단됨
- **🔐 개인정보 고지**: PHI 비저장(`medical_phi_store=false`) · 실시간 STT 오디오 30초 버퍼·
  전사 미보관 · API/테넌트 키 **sha256 저장**
- **📦 제3자 구성요소 표**: Ollama(MIT)·Flask·faster-whisper·SpeechBrain·ChromaDB·diffusers 등
  + **모델 라이선스 등급**(Apache-2.0 / Llama CL / **HAI-DEF 하류 전달 의무** / 상업 등록 필요)
  + **CC BY-NC 모델 미사용** 명시(bespoke-minicheck는 v2.124.13에서 교체 제거)

⚠️ 성격을 **독점(All Rights Reserved)** 으로 잡았다 — 의료·상업 맥락이고 저장소가 PRIVATE라
가장 안전한 기본값. 공개 전환은 되돌리기 어려우므로 **소유자 결정 사항**으로 남긴다.

## 2. `docs/DEVELOPER_GUIDE.md` (신규) — 핵심 산출물

**§1 "Ollama를 그대로 쓰지 않는 이유" 11항목**을 각각 *문제 → 해법 → 코드 위치 → 실측*으로 기술:

| # | 항목 |
|---|------|
| 1-1 | 전역 `OLLAMA_CONTEXT_LENGTH` → **요청 무성 절단**. `/v1` 변환 프록시 + `/api/generate` num_ctx 자동 상향(고정 버킷=리로드 오실레이션 회피) |
| 1-2 | 임베딩까지 전역 컨텍스트 적용 → **bge-m3 KV 4.5GB → OOM**. `num_ctx=2048` 명시로 1.16GB |
| 1-3 | `tool_calls.arguments` OpenAI=문자열 / Ollama=객체 → 변환 없으면 **에이전트 루프 1스텝 사망** |
| 1-4 | 멀티모달 3경로 불일치(`generate` 무시·`/v1` 502) → 정규화 + **원격 URL은 SSRF로 400** |
| 1-5 | **thinking 모델은 `response`가 빈다**(토큰이 `thinking` 필드로) — 모델 비호환 오진 주의 |
| 1-6 | 한국어 필터가 **영문 OCR을 전부 삭제** → `_keep_content`(내용 보존 우선) |
| 1-7 | `MAX_LOADED=1` 스왑 관리(상주 fast-lane·heavy single-flight·`single_model` 17.6→4.9s) |
| 1-8 | **VRAM은 Ollama만 쓰지 않는다**(LLM·실시간 STT·SANA·ComfyUI) — 진료 우선 반납 정책 |
| 1-9 | `format` 제약 디코딩을 평가·의도 분류 기반으로(intent+slot **489ms** 실측) |
| 1-10 | 안전 계층(인증 fail-closed·키 해싱·프롬프트 캡·**DNS 없는 SSRF 검사**·AI 마킹·레이트리밋) |
| 1-11 | 노드 위임 **폴백 경계** — 200+이형 응답은 폴백을 우회한다(6주 무증상 사례) |

§2 실제 사고 기반 **함정 10건** · §3 배포 절차(실 DB dry-run 포함) · §4 파일 지도 ·
§5 유지보수 원칙(측정 선행·무성 실패 금지·되돌릴 수 있게·정직한 기록·클래스 단위 회귀).

## 3. 소스 주석 (선별)

293개 파일에 기계적 주석을 뿌리지 않았다 — **노이즈가 되고 읽히지 않는다.**
개선의 이유가 실제로 담긴 **12개 파일**에 `__author__`/`__license__` +
**"지우기 전에 읽을 것"** 맥락 주석을 넣었다:
`routes/{ollama,openai_compat,vision,medical_law}.py` ·
`core/{stt_realtime,meeting_stt,meeting_pipeline,faithfulness,content_marking}.py` ·
`core/orchestrator/dispatch.py` · `app.py` · `worker_app.py`

⚠️ `from __future__` 임포트가 있는 5개 파일은 **임포트 뒤로 배치**해야 한다(문법 제약).
첫 시도에서 `worker_app.py`가 깨져 즉시 교정했다.

## 4. README

버전 정합(v2.124.55 → 본 릴리즈 .56) + 최상단에 **개발자·라이선스·개발자 가이드 포인터**.

## 검증

전체 회귀 **1,085 passed / 7 skipped** · 실 DB import dry-run 통과 · HUP NRestarts 0 · health 200.
