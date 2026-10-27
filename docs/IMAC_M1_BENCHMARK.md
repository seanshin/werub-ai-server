# iMac M1 베이스라인 벤치마크 — Phase 0-1 (apple-silicon-worker)

> **측정일**: 2026-06-07 | **측정자**: Claude Code (대상 iMac에서 직접 실측)
> **목적**: apple-silicon-worker Phase 0-1 — "CPU 노드" 전제 검증 및 워크로드 이관 설계 근거 확보
> **Plan**: `docs/01-plan/features/apple-silicon-worker.plan.md` §7-0

## 1. 시스템 사양

| 항목 | 값 |
|------|-----|
| 칩 | Apple M1 (CPU 8코어 / **GPU 8코어** / ANE 16코어 11 TOPS) |
| 메모리 | 16GB Unified (측정 시 free 74%) |
| macOS | 26.5 (25F71) |
| 디스크 여유 | **31GB** ← Phase 2 STT 모델(~3GB) 여유 있으나 대형 모델 추가 시 주의 |
| Ollama | v0.23.4 |
| OLLAMA env | **전부 미설정** (FLASH_ATTENTION/NUM_PARALLEL/KEEP_ALIVE 기본값) |

## 2. 핵심 결론

1. ✅ **Metal 가속 정상 동작** — 모든 모델 `ollama ps` PROCESSOR **100% GPU**. 기존 문서의 "iMac = CPU 노드" 표기는 오류 (ARCHITECTURE.md 정정 필요, Phase 0-7)
2. ✅ **3모델 동시 상주 가능** — 3b + 7b + bge-m3 = 8.3GB, 16GB 내 안정
3. ⚠️ **동시 요청이 직렬 처리됨** — NUM_PARALLEL 미설정 상태에서 동시 2요청이 큐잉됨 (개별 속도 유지, wall-clock 2배). **Phase 0-2에서 `OLLAMA_NUM_PARALLEL=2` 설정 필요 근거**
4. △ **7b는 ~11 tok/s** — M1 메모리 대역폭(68GB/s) 한계로 실시간 대화 부적합, **배치 전용 권장**. 3b가 실시간 오프로드 적정선
5. ✅ **임베딩은 야간 색인에 충분** — 실전 청크(500자) 기준 5 docs/s = 300청크/분

## 3. LLM 생성 속도 (eval, 웜 상태)

| 모델 | 크기 | eval tok/s | prompt tok/s (웜) | 콜드 로드 |
|------|------|-----------|------------------|----------|
| qwen2.5:3b | 2.3GB | **22.4 / 21.9** (2회) | 1,063 | 14.6s |
| qwen2.5:7b | 4.8GB | **10.9 / 10.8** (2회) | 584 | 6.2s |
| qwen2.5-coder:7b | 4.8GB | **11.1** | 129* | 4.8s |

\* coder 측정은 다운로드 동시 진행 중이라 prompt 수치 참고용

## 4. 동시 요청 (NUM_PARALLEL 검증)

| 시나리오 | 결과 |
|---------|------|
| 3b 단일 | 21.9 tok/s |
| 3b 동시 2요청 | 개별 22.0 / 21.9 tok/s — **직렬 큐잉** (wall 19.8s ≈ 순차 합), 합산 처리량 20.2 tok/s |

→ 병렬화 안 됨 = 현재 설정에선 동시성 1. NUM_PARALLEL=2 적용 후 재측정 항목 (Phase 0-2).

## 5. 임베딩 (bge-m3, 1024d, 100% GPU)

| 배치 | 텍스트 길이 | 처리량 |
|------|------------|--------|
| 16건 | ~135자 | 17.5 docs/s |
| 64건 | ~500자 (공시 청크 실전 크기) | **5.0 docs/s** (12.7s) |

→ 공시 1건(평균 ~10청크) 색인 2초, 연초 수가 개정기 batch 수백 건도 분 단위 — erp-notice-rag 야간 색인 위임 타당.
qwen3-embedding:8b(4.7GB)는 Phase 3 착수 시 별도 측정 (메모리 상호배제 필요).

## 6. 측정 방법

```bash
# 생성: /api/generate stream=false → eval_count/eval_duration로 tok/s 산출 (num_predict=256, 2회 측정)
# 임베딩: /api/embed 배치 → wall-clock
# Metal 확인: ollama ps PROCESSOR 컬럼
# 동시성: threading 2요청 wall-clock vs 개별 eval_duration 비교
```

## 6-1. Phase 0-2 튜닝 후 재측정 (2026-06-07)

**적용**: `setup/com.werub.ollama.plist` (brew services → 전용 launchd 교체, env 고정)
`OLLAMA_FLASH_ATTENTION=1 · NUM_PARALLEL=2 · MAX_LOADED_MODELS=2 · KEEP_ALIVE=30m`
— 서버 로그에서 적용 확인 완료.

| 항목 | 튜닝 전 | 튜닝 후 | 판정 |
|------|---------|---------|------|
| 3b 단일 | 22.4 | **22.6 tok/s** | 동일 (회귀 없음) |
| 3b 동시 2요청 | **직렬 큐잉** (wall 19.8s, 2번째 요청 ~10s 대기) | **진짜 병렬** — 개별 11.7/11.6, wall 18.3s, 합산 21.9 tok/s | ✅ **head-of-line 블로킹 해소** |
| 7b 단일 | 10.9 | 10.6 tok/s | 동일 |
| bge-m3 16건 | 17.5 | 16.5 docs/s | 동일 |
| 3b 메모리 | 2.3GB | 2.5GB | KV캐시 2배 분 (+0.2GB, 허용) |

**해석**: M1 GPU는 3b 단일 스트림으로 이미 포화 — NUM_PARALLEL=2의 이득은 **합산 처리량(+8%)이 아니라
지연 공정성**이다 (두 번째 호출자가 첫 요청 완료를 기다리지 않음). 위임+독립 이중 활용 모드에서
호출이 겹칠 때 필수적인 설정. KEEP_ALIVE=30m으로 재로드 페널티(3b 14.6s)도 제거.

## 7. 후속 측정 항목 (Phase별)

## 6-2. Phase 1 — AI 판별 ML MPS 실측 (2026-06-07)

| 항목 | 결과 |
|------|------|
| sdxl-detector MPS 추론 (웜, 512px) | **58~92ms** — GPU 서버 CPU 추론(~100ms) 대비 동등 이상 |
| MPS↔CPU 동일성 | ai_probability 편차 < 1%p (회귀 테스트 통과) |
| 콜드(모델 로드 포함) | ~2.7s (1회) |
| 마킹(워터마크+iTXt) | 15ms |

## 6-3. Phase 2 — STT 백엔드 PoC (2026-06-07)

**방법**: 한국어 합성 음성 42.8초(say/Yuna, 16kHz — 의료·공시 도메인 문장 248자), large-v3 동일 모델

| 항목 | mlx-whisper | whisper.cpp (Metal) |
|------|------------|---------------------|
| 처리 시간 (웜) | **14.3s (RTF 0.34)** | 21.3s wall — CLI가 매 호출 3GB 모델 재로드 (순추론 ~14s 추정) |
| 전사 품질 | CER 2.8% | CER 2.8% — **양쪽 출력 텍스트 완전 동일** (차이는 구어 숫자 정규화 "세 가지"→"3가지" 등, 의미상 정답) |
| 워커 통합 | ✅ **Python in-process** — Flask 워커에 모델 상주, numpy 직접 입력 | CLI 서브프로세스(매번 재로드) 또는 whisper-server 별도 프로세스 필요 |
| 의존성 | pip mlx-whisper (venv-mac) | brew whisper-cpp + ggml 모델 파일 |

**판정: mlx-whisper 선정** — 품질 동일·속도 동등 조건에서 in-process 상주가 워커 구조(메모리 가드·락)와
자연스럽게 결합. 60분 오디오 환산 ~20.4분 (설계 기준 "20분 이내" 근접 충족 — 긴 오디오 배치 효율로 개선 여지).

**잔여 확인 사항**:
- 실제 회의 녹음(비합성·잡음·다화자)으로 품질 재검증 — 기존 Meeting 샘플 3건 (Plan §7 기준)
- mp4/비-wav 입력은 ffmpeg 필요 (mlx-whisper 오디오 로더) → Phase 2 구현 시 `brew install ffmpeg` 요건
- 화자 분리(SpeechBrain)의 MPS 동작 검증 (Plan 2-5)

| Phase | 항목 |
|-------|------|
| ~~0-2~~ | ✅ 완료 (§6-1) |
| ~~1~~ | ✅ 완료 (§6-2) — 동일성 100장 확대 측정은 GPU 위임 활성화 전 수행 |
| ~~2 PoC~~ | ✅ 완료 (§6-3) — mlx-whisper 선정 |
| 2 | whisper.cpp CoreML vs mlx-whisper — 60분 한국어 오디오 WER/처리시간 |
| 3 | qwen3-embedding:8b Metal 처리량 (notice_knowledge 모델) |
