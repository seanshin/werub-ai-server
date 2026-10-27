# Release v2.105.0 — 실시간 STT + 번역 위임 (호스트 STT 파이프라인)

**날짜:** 2026-06-18
**대상:** 호스트(HIS/meet) 팀 실시간 회의 자막/통역 STT+번역 위임 요청
**GPU HEAD:** b7127ef (본 릴리즈 문서 커밋 별도)
**회신서:** `docs/01-plan/references/realtime-stt-translate-reply-to-host.md`

---

## 배경

호스트(GPU 부재)가 실시간 회의 자막/통역용 STT+번역을 AI 서버에 위임 요청(배치형 부적합). 협업으로 번역·STT 양쪽을 LIVE 달성하고, 호스트 Jigasi 전사 인프라까지 배선 완료.

**통합 토폴로지:**
```
jigasi(JIGASI_MODE=transcriber) → Vosk WS ws://stt-bridge:2700
  → stt-bridge(Vosk↔HTTP·A+C) → AI 서버 /api/stt/chunk → (STT medium + 번역)
```

---

## 1. `/api/translate` — 실시간 다국어 번역 (계약 1:1)

`{text, source?, targets[], domain?, style?}` → `{translation:{lang:text}, ms, ...}`. 모델 **qwen2.5:14b 상주(warm)**. 단일 `target` 호환. 지원 `en/ja/zh/es/fr/de/ko`.
- `domain`(medical/legal): 전문 시스템 프롬프트. ko 대상+의료/법률은 영어 용어 병기 보존(force_korean 미적용).
- `style`(literal/natural/formal/casual).
- 라이브: 의료 ko→en **~280–370ms/언어**.

### 번역 품질 가드 (라이브에서 잡은 결함 4종)
- **메타 코멘트 억제**(`_clean_translation`): qwen2.5:14b가 모호 원문에 `(Note:.../Corrected version:)` 덧붙이던 계약 위반 → 이중 개행 첫 단락만 + 괄호 영문 메타노트 절단(병기 괄호 보존).
- **🔴 캐시 충돌 봉합**: 공용 `_generate` 캐시키 `prompt[:200]`가 번역 보일러플레이트(~190자)에 짧은 입력을 잘라 **서로 다른 발화→같은 번역**(운영서 환자 발화 혼선) → `[원문]{text}`를 프롬프트 선두로 이동해 키 고유화.
- **언어 충실도 가드**(`_script_ok`): ja에 순중국어 반환 → 대상 언어별 강한 지시(`_LANG_HINT`)+표기 충실도 검사→불일치 시 저온 재시도. ko 한글 후 중국어 degenerate → 전각 문장부호·한자 비율 검사 + force_korean 최후 정리.

## 2. `/api/stt/chunk` — 실시간 청크 STT PoC (워밍 faster-whisper)

`core/stt_realtime.py`: GPU 상주 faster-whisper 싱글톤, raw 16-bit LE mono PCM→numpy 직접 변환(ffmpeg 불요), 단일 직렬 추론, 세션 롤링 버퍼(슬라이딩 30s·TTL 180s·무누수).
- 헤더 `X-Sample-Rate`/`X-Lang`/`X-Mode(partial|final)`/`X-Session-Id`/`X-Targets`.
- **A+C 통합**: `final`+`X-Targets` → STT→번역 단일 왕복(`translate_core` 재사용).
- `GET /api/stt/chunk/status` · **`POST /api/stt/chunk/warm`**(회의 시작 워밍 — 첫 청크 콜드로드 제거).

### 배치 결정: GPU faster-whisper (iMac mlx 데몬 대체)
faster-whisper는 CUDA에서 gunicorn 인프로세스 동작 → 별도 데몬/Metal-fork 회피 불요. iMac mlx보다 단순·테스트 가능. 운영 GPU에 상주(LLM과 VRAM 공존, ~1.9GB 여유).

### 한국어 의료 정확도 (호스트 실음성 발견 → 수정)
호스트 실한국어 검증서 **치명 발견**: `처방전 → STT(small) "초광전" → 번역 "SQUID"` 환각 연쇄(conf 0.73 게이팅 통과). 3개 개선:
- **① 모델 small→medium**: `처방전` 정확 인식. **large-v3는 상주 LLM과 16GB GPU에서 CUDA OOM 확인** → medium이 정확도/VRAM 최적.
- **② 의료 `initial_prompt`**(`STT_INITIAL_PROMPT`): OOV 바이어스 교정(`초방전`→`처방전`), beam_size 1→5.
- **③ 저신뢰 번역 보류 가드**: `conf<STT_TRANSLATE_MIN_CONF`(0.5) → 번역 cascade 보류(`low_confidence`+`translation_held`).
- 전부 env 무재배포 튜닝(`STT_REALTIME_MODEL`/`STT_INITIAL_PROMPT`/`STT_BEAM_SIZE`/`STT_TRANSLATE_MIN_CONF`).

### PoC 수용기준 — 전부 충족(라이브, Yuna TTS 한국어)
| 기준 | 결과 |
|---|---|
| final(번역포함) ≤ 3s | ✅ 워밍 STT 31ms + 번역 → 320~659ms |
| 동시 2세션 무누수 | ✅ 병렬 final, 세션 0 복귀 |
| warm 상주 | ✅ 콜드 로드 후 상주(warm 훅 2.4s) |
| 실패 graceful | ✅ 빈 바디 400·잡음 PCM text=""·크래시 0 |
| 한국어 의료 | ✅ "처방전 받으세요"→"Get the prescription"(SQUID 제거) |

## 3. 부수 — 저우선 백로그 정밀도 fix

- **`fix(dur)` split_cautions 과매칭 봉합**: 서방정분할주의 LIKE 폴백을 입력명이 서방정 제형(서방/CR/SR/OROS) 명시 시에만 허용 → 짧은 성분명 false positive 제거. 회귀 test_dur 15→18.
- **`fix(medical-law)` RAG 본코퍼스 게이트 분리**: `_rag_law_search` `legal_min_score`(min_score-0.07) → 법령/고시/개정법률안 완화 게이트, 얇은 의안이 발간물에 밀려 0.4 게이트 탈락하던 현상 보정. 라이브: 의안 인용 0.3872/0.3763 floor surface.

## 4. 문서

- **AI 기술백서** `docs/AI_TECH_WHITEPAPER.md`(홈페이지/파트너 공개용 11장 — 기반구조·표준·오케스트레이션·의료 정본·보안·책임AI).
- 회신서 `docs/01-plan/references/realtime-stt-translate-reply-to-host.md`(계약·§6 회신·SLO·PoC 결과·한국어 정확도·4질문 답변).

## 호환성 / 위험

- `/api/translate`·`/api/stt/chunk` 신규(Bearer 인증). octet-stream은 CSRF상 Bearer 필수(계약대로 호스트 전송 → 정상).
- STT medium 상주 시 VRAM 14.4/16.3GB(LLM 12.2+medium 1.5 공존, ~1.9GB 여유). large-v3는 OOM(미사용).
- 회귀: test_stt_realtime 7 + test_api_writing 17 + test_dur 18. 문법훅 212 통과. 라이브 배포·HUP 재기동 후 health 200·NRestarts 0.

## 커밋 (주요)

| 커밋 | 내용 |
|---|---|
| d7ecf8a | AI 기술백서 |
| fa80431 | split_cautions 과매칭 봉합 |
| b4ef19a | RAG 본코퍼스 게이트 분리 |
| 9bf4aef | **/api/translate 신규** |
| 7502a59 / f39d4bb / 788bdc2 / b77e878 / af844ec | 번역 가드(메타코멘트·언어충실도·캐시·ko한자) |
| ecf65ec | **/api/stt/chunk PoC** |
| a8a92fe | STT 한국어 정확도(medium+프롬프트+저신뢰가드) |
| b7127ef | **/api/stt/chunk/warm** |

## 잔여 (우리 범위 / 외부)

- 실마이크 정확도 = 실회의 end-to-end 테스트서 확정(TTS는 클린). 추가 의료 어휘는 initial_prompt 확장(무재배포).
- STT 워밍 = stt-bridge가 회의 시작 시 `/api/stt/chunk/warm` 호출 권장.
- 호스트: 실회의 자막 end-to-end 테스트(인프라 배선 완료). 동시성 >3 시 GPU 추가 노드 후속.
