# WeRU.B AI Server — 개발자 가이드

> 개발자: **신현묵 (Shin Hyunmook)** <8508132+seanshin@users.noreply.github.com>
> 대상: 이 코드를 처음 인수인계받는 사람. **"왜 이렇게 짰는가"**를 남긴다.
> 관련: `LICENSE` · `CLAUDE.md`(변경 이력 정본) · `docs/API_REFERENCE.md`(외부 계약)

---

## 0. 한 줄 요약

**이 서버는 Ollama 프록시가 아니다.** Ollama는 추론 엔진으로만 쓰고, 그 위에 **운영·안전·
품질 계층**을 얹은 애플리케이션이다. Ollama를 그대로 노출했을 때 실제로 터졌던 문제들을
하나씩 막으면서 자란 코드이므로, **각 우회의 이유를 모르면 "불필요해 보이는 코드"를 지우게
된다.** 이 문서는 그 이유를 남긴다.

```
[호출자] ──HTTPS──> nginx ──> gunicorn(app.py, 36 Blueprint)
                                   │
                                   ├─ 인증/레이트리밋/CSRF/SSRF 가드
                                   ├─ 도메인 라우팅(의료·한국어·이미지·STT·RAG)
                                   ├─ 품질 계층(force_korean·평가 하네스)
                                   └─ 엔진 호출 ──> Ollama(:11434) / faster-whisper / ChromaDB
                                                    SANA 사이드카 / iMac 워커
```

---

## 1. Ollama를 그대로 쓰지 않는 이유 — 개선 항목 상세

각 항목은 **문제 → 해법 → 코드 위치 → 실측**으로 적는다. 지우기 전에 반드시 읽을 것.

### 1-1. 컨텍스트 길이가 전역이라 요청이 조용히 잘린다 🔴

- **문제**: Ollama의 `OLLAMA_CONTEXT_LENGTH`는 **프로세스 전역**이다(현재 8192). 큰 시스템
  프롬프트+도구 스키마를 보내면 서버가 **에러 없이 중간을 잘라버린다**. 호출자는 품질이
  나빠진 것만 보고 원인을 모른다. 로그에만 `truncating limit=4098 prompt=14706` 형태로 남는다.
- **해법 A(`/v1`)**: OpenAI 호환 엔드포인트를 **Ollama의 `/v1` 패스스루로 만들지 않았다.**
  `/api/chat`으로 **변환**하면서 요청별 `options.num_ctx`를 주입한다.
  → `routes/openai_compat.py::build_ollama_payload`
- **해법 B(`/api/generate`)**: 외부 클라이언트가 8K~17K 토큰을 상시 보내는 것을 발견하고
  (v2.124.32), 프롬프트 길이를 추정해 `num_ctx`를 **고정 버킷으로 상향**한다. 가변값으로
  주면 요청마다 모델이 리로드돼 오실레이션이 생기므로 **고정 상한**을 쓴다.
  → `routes/ollama.py::_estimate_tokens`, `_auto_num_ctx`
- **실측**: 한글은 토큰/문자 ≈ 1.2, 그 외 ≈ 0.32(과소추정 회피 편향). 상한 20480 =
  슬롯당 KV 약 12.7GB로 16GB에서 안전.

### 1-2. 임베딩 모델까지 전역 컨텍스트를 먹어 OOM 🔴

- **문제**: `bge-m3` 임베딩 호출에도 전역 8192가 적용돼 KV 4.5GB를 잡고, 상주 14b와 겹쳐
  **VRAM OOM으로 RAG가 500**을 뱉었다.
- **해법**: 임베딩 요청에 `options.num_ctx=2048`을 **명시**. 4.5GB → 1.16GB로 공존 가능.
  → `client/rag.py::embed_text`
- ⚠️ 원인은 `NUM_PARALLEL`이 아니라 **컨텍스트**였다. 병렬도를 낮추는 방향으로 잘못
  진단하기 쉬운 지점.

### 1-3. OpenAI 도구 호출 포맷이 Ollama와 다르다

- **문제**: OpenAI는 `tool_calls[].function.arguments`를 **JSON 문자열**로, Ollama는
  **객체**로 다룬다. 변환 없이 넘기면 멀티턴 에이전트 루프가 **1스텝 만에 죽는다**
  (`Value looks like object, can't find closing '}'`).
- **해법**: 양방향 변환. 요청은 문자열→객체(`normalize_inbound_messages`), 응답은
  객체→문자열 + `finish_reason=tool_calls`.
  → `routes/openai_compat.py`

### 1-4. 멀티모달(이미지) 입력 경로가 제각각이었다

- **문제**(2026-07-29 그룹웨어 제보로 발각): 같은 이미지·프롬프트인데 경로마다 달랐다.
  `/api/chat+images` ✅ / **`/api/generate+images` = 조용히 무시** / **`/v1+image_url` = 502**.
  특히 `/api/generate`는 핸들러가 `images`를 페이로드에 넣지 않아 **오류 없이 버려졌고**,
  모델이 "이미지가 포함되어 있지 않습니다"라고 답해 호출자가 원인을 알 수 없었다.
- **해법**: ①`_normalize_images()` — `data:image/...;base64,` 접두 제거(Ollama는 raw base64만
  받는다) ②OpenAI 멀티모달 `content` 배열 → `content` 문자열 + `images[]` 변환
  ③원격 `http(s)` URL은 **받아오지 않고 400**(요청 경로에서 임의 URL fetch = SSRF 표면).
  → `routes/ollama.py::_normalize_images`, `routes/openai_compat.py::_convert_multimodal_content`

### 1-5. thinking 모델은 `response`가 비어 있다 ⚠️ (오진 주의)

- **현상**: `qwen3-vl:8b`에 이미지를 넣고 Ollama `/api/generate`를 **직접** 부르면
  `response`가 **빈 문자열**이고 `done_reason=length`가 된다. `num_predict`를 키워도,
  `think:false`를 줘도(0.30.11 기준) 동일하다.
- **원인**: 이 모델은 사고 토큰을 **`thinking` 필드**로 내보낸다. 예산이 모자라면 실제 답변에
  도달하지 못한 채 끝난다. **모델 비호환이 아니다.**
- **함의**: v2.118.2에서 "qwen3-vl vision 깨짐"으로 판단해 기본 비전 모델을 minicpm-v로
  바꿨는데, 그 판단의 근거가 이 현상이었다. 앱 경로(시스템 프롬프트 주입·num_predict 미구속)
  로는 정상 동작한다.
- **교훈**: 빈 응답을 보면 **모델을 탓하기 전에 ①thinking 필드 ②후처리 필터**를 의심할 것.
- **2026-08-19 정정(v2.125.10 · Ollama 0.32.5)**: 위 "num_predict를 키워도 동일"은 0.30.11 관측이고, 0.32.5에서는
  **num_predict 2048이면 정상**(300이면 사고만 쓰다 잘림 · `think:false`·`/no_think`는 여전히 무시). 사전 등록 PoC 통과로
  기본 비전을 `qwen3-vl:8b`로 **복귀**(폴백 minicpm-v · `_vision_payload`가 예산 보정). 같은 클래스=`core/meeting_vision.py`.

### 1-6. 한국어 후처리가 내용을 지운 사고 🔴

- **문제**: `/api/vision/describe`가 `language=ko`에서 **200 + 빈 description**을 반환했다.
  모델은 정상 판독했는데, 영문 우세 출력이 `force_korean` 필터를 타면서 **영문이 전부
  제거**된 것. 빈 응답 502 가드는 필터 **이전**에 있어 못 잡았고, 호출자에겐 "인식 실패"로 보였다.
- **해법**: `_keep_content()` — 필터 결과가 비거나 **기호만 남으면 원문을 유지**한다.
  **한국어화보다 내용 보존이 우선**이다.
  → `routes/vision.py::_keep_content`, `_ensure_korean`

### 1-7. 모델 스왑 관리 (16GB 천장)

- `OLLAMA_MAX_LOADED_MODELS=1`이라 **다른 모델을 부르면 상주 모델이 축출**된다.
  36개 모델 플릿을 16GB에서 굴리는 대가다.
- **해법**: ①상주 fast-lane(`qwen2.5:14b`, `keep_alive=-1`) ②heavy 모델은 **single-flight
  락**으로 동시 로드 차단(409) 후 응답 끝나면 **상주 자동 복원**
  ③의료 파이프라인은 `single_model` 옵션으로 스왑 자체를 없앨 수 있게 함(17.6s → 4.9s 실측).
  → `routes/heavy.py`, `app.py::_gpu_manager`

### 1-8. VRAM은 Ollama만 쓰는 게 아니다 ⚠️

- 같은 16GB를 **상주 LLM(10.4GB) · 실시간 STT medium(1.96GB) · 배치 STT large-v3
  (float16 3.84GB / int8 1.91GB) · SANA 사이드카(~2.4GB) · ComfyUI**가 나눠 쓴다.
- **해법**: 배치 STT 시작 전 **유휴 점유자에게 반납 요청**. 단 **진행 중인 진료는 절대
  끊지 않는다**(활성 세션·최근 발화면 반납 안 함 → 배치가 실패하고 대기열이 다시 태움).
  → `core/meeting_pipeline.py::_free_realtime_stt_vram`, `_free_sana_vram`
- **점검 습관**: 서비스가 보고하는 `loaded:false`를 믿지 말고
  `nvidia-smi --query-compute-apps`로 **실제 점유 프로세스**를 볼 것.

### 1-9. 구조화 출력(`format`)을 적극적으로 쓴다

- Ollama의 JSON 스키마 제약 디코딩을 **평가 하네스·의도 분류·벤치마크**의 기반으로 삼는다.
  생성형 MC 포맷 편향이 사라지고 파싱 실패가 0이 된다.
  → `scripts/eval_bench.py`, `core/faithfulness.py`, 명령 intent 추출
- **실측**: 명령 intent+slot 추출이 상주 14b에서 **489ms**(비상주 7b는 스왑 때문에 4,721ms).

### 1-10. 안전·보안 계층 (Ollama에는 없는 것)

| 항목 | 이유 |
|------|------|
| 인증 fail-closed | Ollama는 인증이 없다. Blueprint별 가드 + 의료는 X-Medical-Key 수용 |
| API 키 sha256 | 평문 저장 금지. 발급 응답이 원본을 보는 유일한 기회 |
| 프롬프트 캡(20만자) | 단일 워커 자원 점유 방지 |
| SSRF 정적 검사 | 요청 경로에서 **DNS를 부르지 않는다**(블로킹 20s → 스레드 고갈 경험) |
| AI 생성물 마킹 | AI 기본법 §31 — 가시 워터마크 + PNG 메타 + C2PA |
| 레이트리밋 | 키 기반 버킷(프록시 뒤 IP는 단일 버킷으로 붕괴) |

### 1-11. 노드 오케스트레이션 (GPU ↔ iMac)

- 호출자는 **GPU 하나만** 부르고, capability별로 iMac에 자동 위임된다. 실패는 **로컬 폴백**.
  → `core/orchestrator/dispatch.py`
- ⚠️ **함정**: 폴백은 *예외/`available:false`* 만 실패로 본다. 워커가 **200으로 이형 응답**을
  주면 폴백이 우회되고 위임 경로가 **조용히 100% 실패**한다(2026-07-29 meeting STT
  `text` vs `full_text` 키 불일치 사례). 새 capability 배선 시 **응답 키를 실제 호출로 대조**할 것.

---

## 2. 개발자가 반드시 알아야 할 함정

실제로 사고가 났던 것만 적는다. 전부 재발 방지 코드/테스트가 있다.

1. **gunicorn `preload_app=True` + SQLite** → fork 시 `-shm` 상속으로 워커 데드락.
   `preload_app=False`가 정답이었다(60초 행·HTTP 000의 진짜 원인).
2. **스키마 변경 배포** → 신규 컬럼 인덱스를 `CREATE TABLE` 스크립트에 넣으면 **기존 DB에서
   부팅 실패**한다. `ALTER` 이후에 인덱스를 만들고, 배포 전 **실 DB `import app` dry-run 필수**
   (단위 테스트는 임시 DB라 못 잡는다).
3. **graceful HUP은 만능이 아니다** — 새 코드가 부팅에 실패하면 마스터까지 내려간다.
4. **`pkill -f`는 자기 자신도 죽인다**(원격 bash cmdline에 패턴이 포함됨).
   안전 경로는 `kill -HUP $(pgrep -o -f gunicorn)`.
5. **테스트 파일 basename 충돌** → pytest가 **수집 단계에서 전체 중단**된다. 조용히 0 테스트가
   되는 게 아니라 아예 안 돈다.
6. **문자열 치환 패치는 매치 위치를 확인**할 것(첫 매치만 바뀌어 본 라우트가 무방비였던 사례).
7. **Swagger 독스트링 한 줄이 `/openapi.json` 전체를 500**으로 만든다(따옴표 없는 `{}`).
   `tests/test_swagger_docstrings.py`가 막는다.
8. **status 200 ≠ 전 경로 정상.** 휴면 기능의 `NameError`가 3회 있었다(HR Law 가드 6곳 등).
   인가·기능 경로는 **실제 호출로** 검증할 것.
9. **품질을 측정할 수 없는 상태에서 두 변수를 동시에 바꾸지 말 것**(배치+int8 동시 적용 →
   실사용 OOM). 한 번에 하나씩, 긴 입력으로 먼저.
10. **응답 스키마를 확인하고 판정할 것**(DUR 임부금기는 `pregnancy_warnings`,
    `contraindications`는 병용금기 전용 — 필드 오독으로 회귀를 오인한 적 있음).

---

## 3. 개발·배포 절차

```bash
# 1) 로컬: 문법 + 단위 테스트
python3 -m py_compile <changed files>
# (전체 회귀는 GPU에서)

# 2) 커밋·푸시
git commit && git push

# 3) GPU에서 배포 — 절차 전체가 스크립트다
ssh deploy@<GPU> 'cd ~/WeRUBLLMManager && scripts/deploy.sh'
```

`scripts/deploy.sh`가 하는 일(각 단계 실패 시 즉시 중단):

| 단계 | 내용 | 왜 |
|---|---|---|
| 1 | `git pull --ff-only` | |
| 2 | **실 DB dry-run** `import app, os; os._exit(0)` | v2.124.51 부팅 실패 1분 중단 재발 방지. `os._exit`은 **종료 단계 네이티브 크래시**(exit 139) 우회 — 안 하면 실제 임포트 오류와 구분이 안 된다(v2.124.93) |
| 3 | **G4 회귀** `pytest -q` | |
| 4 | graceful HUP + health 200 + `NRestarts` 확인 | |
| 5 | **G1 스모크 + G2 골든셋** `smoke_contract.py --gate` | **신규 실패만 차단**(기준선 대비) |

- **게이트가 왜 "신규 실패만"인가**: 08-06~08-10 스모크가 **나흘간 63/64**였다.
  "전건 통과 아니면 중단"이었다면 그 사이 나간 v2.124.92·93·94가 전부 막혔을 것이다.
  알려진 실패는 `config/gate_baseline.json`에 **사유와 함께** 등록하고, 새 실패만 막는다.
  → `core/gate_baseline.py`
- **긴급 우회**: `SKIP_REASON="…" scripts/deploy.sh --skip-gates`
  우회를 막는 게 아니라 **우회한 사실이 `logs/deploy.log`에 남게** 하는 것이 목적이다.
- ⚠️ **평가 배치와 동시에 실행하지 말 것.** 5단계가 GPU를 쓴다
  (2026-08-10 실측: 배치 중 다른 GPU 작업을 겹쳐 배치가 38분간 멎었다).

- **엔진(Ollama) 업그레이드**는 `scripts/ollama_upgrade_check.sh`(21항목)로
  **업그레이드 전 기준선 → 후 비교**. 런북: `docs/01-plan/features/ollama-upgrade-0.30.plan.md`
- **드라이버·재부팅**: `docs/MAINTENANCE_GPU_DRIVER.md`
  (⚠️ user systemd 유닛은 `Linger=yes`가 없으면 부팅 시 안 뜬다)

---

## 4. 파일 지도 (핵심만)

> ⚠️ **새 LLM 추상화를 만들기 전에 `docs/DESIGN_LLM_ABSTRACTION.md`를 볼 것.**
> 이미 **여섯 층**이 있다(provider 게이트웨이·복원력·상주·노드·앞단 라우팅·task 라우팅).
> 2026-08-10에 그 사실을 모르고 같은 이름의 파일을 덮어쓴 사고가 있었다 —
> 인벤토리는 `grep <패턴>`만이 아니라 **이름으로도** 훑어야 한다.


| 경로 | 역할 |
|------|------|
| `app.py` | 앱 조립·인증/CSRF·헬스/경보·백그라운드 워커·Blueprint 등록 |
| `routes/ollama.py` | 채팅/생성/모델 관리 — **num_ctx 자동 상향·images 정규화** |
| `routes/openai_compat.py` | `/v1` 변환 프록시 — **num_ctx 주입·tool_calls·멀티모달** |
| `routes/medical.py` · `medical_law.py` | 임상 API(테넌트 키)·의료법 RAG(권위 위계·벌칙 역참조) |
| `routes/meeting.py` · `core/meeting_pipeline.py` | 녹화 STT 파이프라인(대기열·화자분리·VRAM 조정) |
| `core/stt_realtime.py` | 실시간 청크 STT(세션 버퍼·어휘 바이어싱) |
| `core/orchestrator/` | 노드 위임(GPU↔iMac)·폴백 |
| `core/faithfulness.py` · `llm_judge.py` · `eval_score.py` | 품질 평가(충실도·G-Eval·종합 지수) |
| `core/content_marking.py` · `c2pa_sign.py` | AI 생성물 표시 의무 |
| `scripts/` | 평가 러너·수집·업그레이드 검증 |
| `tests/` | 회귀(1,081 passed) — **가드 테스트가 여럿**(재발 차단용) |

---

## 5. 유지보수 원칙 (이 코드베이스의 규율)

1. **측정 선행.** 추측으로 최적화하지 않는다. 성능 튜닝 3회 시도 모두 "병목 없음"으로
   측정 기각됐다.
2. **무성 실패 금지.** 조용히 버리거나 잘리는 경로는 반드시 통지한다
   (`dropped_s`·`truncated`·`hotwords_applied`·경고 로그).
3. **되돌릴 수 있게.** 설정은 `config.json` 무재배포 토글, 위험 변경은 킬스위치
   (`MEDICAL_AUTH_ENFORCE` 등), 엔진 교체는 롤백 자산 보존.
4. **정직한 기록.** 실패·오판도 릴리즈 노트와 메모리에 남긴다. 그래야 같은 실수를 안 한다.
5. **회귀는 클래스 단위로.** 버그를 고치면 **같은 클래스를 막는 테스트**를 남긴다
   (레포 가드 테스트: 평문 키 저장·Swagger YAML·core Flask import).


## 계약 스모크 — 회귀가 못 잡는 층 (2026-08-04 신설)

`scripts/smoke_contract.py`

### 왜 별도인가

회귀 1,291건은 **107개 파일 중 실제 서버·모델을 호출하는 것이 5개뿐**이고 나머지는 순수
로직·모킹이다. 2026-08-04에 찾은 결함 5건은 전부 **"코드는 정상인데 답이 쓸모없는"** 클래스라
단위 테스트로는 원리적으로 잡히지 않았다.

| 결함 | 성격 | 단위 테스트 |
|---|---|---|
| meditron이 분석 대신 "…과정을 알아보세요"만 반환 → 입력 에코가 나감 | 모델이 답을 안 함 | ❌ |
| MedGemma가 `<start_of_image>` 특수 토큰을 응답에 실음 | 모델 출력 특성 | ❌ |
| MedGemma가 진료과를 "방사선 종양학과"로 오안내 | 모델 출력 특성 | ❌ |
| 약물 상호작용이 영문 토큰만 읽어 한글명 누락 | 로직 결함이나 **한글 케이스 부재** | ⚠️ |

`predict`·`adverse-drug`는 **테스트 파일 자체가 없었다.**

### 검증 축

구조화 필드 존재 · 입력 에코 아님 · 특수 토큰 부재 · 금지 용어 부재 · 알려진 정답 케이스 ·
빈 응답 아님 · 한국어 비율 · 정본 병합(식약처DUR).

### 실행

GPU를 쓰므로 **CI가 아니라 배포 전·모델 변경 시·주 1회**.

```bash
./venv/bin/python scripts/smoke_contract.py                  # 전체(23 checks)
./venv/bin/python scripts/smoke_contract.py --only dur       # 일부
./venv/bin/python scripts/smoke_contract.py --image /path/to/xray.png
```

### ⚠️ 한계

**새 결함을 찾아주지 않는다. 아는 결함의 재발만 막는다.** 새 결함은 오늘처럼 직접 재봐야 나온다.

⚠️ 체크를 쓸 때도 **과한 요구를 하지 말 것.** 첫 구현에서 "본문에 제15조 인용"을 요구했다가
실패했는데, 답변은 오히려 정확했다 — 시행규칙 제15조 원문이 "법 제22조제2항에 따른…"이라
조문을 정확히 인용하면 본문에는 제22조가 나온다. **체크가 틀린 것이었다.**
