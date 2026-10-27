# Release v2.107.0 — 의료 영상 MedGemma 1.5 멀티모달 + 비전 한글 네이티브 + 아바타 P2/P3 groundwork

**날짜:** 2026-06-21
**대상:** 트윈 디지털트윈팀 — 업그레이드 제안서(2026-06-21) P1/P2/P3 회신 및 결선
**GPU HEAD:** 99ab6ab
**성격:** 영상 판독 모델 업그레이드(트윈 인터페이스 무변경) + 아바타 후속 정밀화 groundwork

---

## 배경

트윈팀이 AI 서버 업그레이드 3건(P1 MedGemma 1.5 · P2 TotalSegmentator 심혈관 · P3 EP 대리모델)을 제안. 전부 **AI 측 모델/엔진 교체로 트윈 소비 인터페이스(엔드포인트·glTF `_LAT`·ECG·organ-mesh roi) 무변경**. 본 릴리즈는 실측 검증 기반 회신 + AI 측 준비분을 출하한다. 회신서: `docs/01-plan/references/ai-server-upgrade-proposal-reply-to-twin.md`.

## 1. 의료 영상 판독 — MedGemma 1.5 멀티모달 (P1 완결, headline)

- **모델 교체**: `MEDICAL_VISION_MODEL: medgemma:4b → dcarrascosa/medgemma-1.5-4b-it:Q8_0`(gemma3 4.3B·128K·vision+text). 폴백 `medgemma:4b`.
- **게이트 해소(트윈 제공)**: `google/medgemma-1.5-4b-it` 공식 멀티모달(2026-01) + ollama `dcarrascosa/...`(mmproj 비전 프로젝터 동반). 이전 "text-only로 보임"은 GGUF `mmproj` 누락이 원인.
- **VRAM/품질**: 실 X-ray 비교서 **Q8_0**이 Q4보다 완결적 differential 산출(병변 배제 구체화) → Q8 채택(로드 6.7GB, 14b 상주와 공존 ~15GB<16.3GB). 폴백 Q4_K_M(경량 4.9GB). 트윈 권고대로 4B(27B 후순위).
- **영향 경로**: `analyze`·`report`·**`dual-read`(primary)**·`stream` — `MEDICAL_VISION_MODEL` 참조라 자동 반영. 트윈 무변경.
- **GPU E2E**: `ollama show` Capabilities=vision; **실 공개 흉부 X-ray → 한글 영상의학 판독**("폐 양측 잘 확장·주요 혈관/흉곽 정상·종양 미관찰 / Impression: 정상") 생성. 비의료 합성이미지는 적절히 거부(환각 방지).

## 2. 의료 비전 한글 네이티브 옵션 (P1)

- **근본 원인**: `force_korean`은 *필터*(한글 없는 라인 삭제)라 영어중심 출력(LLaVA-Med 등)에 적용 시 내용이 통째로 삭제됨. 특히 `dual-read`는 한글 후처리가 전무해 영어 노출 → 트윈이 `/imaging/translate` 후처리.
- **수정**: `_ensure_korean(text, language)` — **한국어 우세=force_korean 정리, 영어 우세=translate_core(의료 도메인) 번역**(내용 보존), 실패 시 폴백(비파괴). `analyze`·`report`·`describe`·**`dual-read`** 적용.
- **효과**: 트윈이 `language=ko`(기본)로 호출 시 `/imaging/translate` 생략 가능. 인터페이스 무변경.

## 3. 아바타 P2 — 심혈관 세분 ROI plumbing (라이선스 게이트)

- **발견**: TS는 이미 최신 **2.14.0**(버전업 불요), `heartchambers_highres`(11구조) task 설치돼 있으나 **TS 라이선스 필요 + fast 미지원**, 현재 `missing_license`. 병원=상용이라 라이선스 정책 결정 선결.
- **정정**: 현 R3 심장 메시는 "대리근사" 아님 — **실제 TotalSegmentator(heart) 분할**. "0마스크"=폐-FOV CT(HIS #2 게이트).
- **plumbing**: `avatar_organ_mesh.py roi=heart_chambers`(라벨 7종) + `avatar_mesh SUPPORTED_ROIS` 추가 — **무라이선스 시 `license_required` graceful 에러**(crash 아님), `roi=heart` 무료 대체 권고. 기존 lung/heart/kidney/liver 경로 100% 불변(additive).

## 4. 아바타 P3 — EP 대리모델 엔진 플러밍 + openCARP PoC

- **엔진 선택 플러밍(P3a)**: `core/cardiac_ep.py` 레지스트리(`DEFAULT_ENGINE`/`SUPPORTED_ENGINES`) + `engine` 컬럼(자동 마이그레이션) + `submit(engine=)` 검증, `cardiac_ep_run.py --engine` 디스패치, 라우트 optional(트윈 미전송=기본). **후속 대리모델 = 레지스트리 등록 + compute 분기 1곳**. 출력 인터페이스 락.
- **openCARP PoC(P3b)**: Docker 격리 설치(`docker.opencarp.org/opencarp/opencarp:latest`, CPU/MPI, GPU/Ollama 무영향). 단일세포 EP(bench tenTusscherPanfilov) + **조직 monodomain → LAT**(756노드·전파 5.55ms) = 대리모델 학습 ground-truth 생성 검증. 재현 `scripts/opencarp_poc.sh`. 설계 `docs/01-plan/features/cardiac-ep-surrogate-engine.design.md`.
- **남은 R&D(P3c~)**: 파라미터 스윕 데이터셋 + 신경연산자(PINO/BLNM) 학습 → 엔진 등록·A/B. 현 `eikonal-surrogate-v1.1` 라이브 유지(비블로킹).

## 5. 문서 동기화

- 화이트페이퍼·README·ARCHITECTURE·MCP 가이드 v2.106.1 정합(API 592+, 임베딩 bge-m3 정정, 아바타 디지털 트윈 반영).

## 검증

- 영상 1.5 E2E: 실 흉부 X-ray → 한글 판독 생성, VRAM 공존, health 200·NRestarts 0.
- P3a: engine 컬럼 마이그레이션·`bogus` engine→clean error(GPU). P2: heart_chambers→`license_required` graceful(GPU). P3b: openCARP LAT 생성(GPU).
- 비전 한글: 영어우세→의료 번역, 한국어우세→필터(GPU). 회귀 문법훅 218 통과.

## 트윈 영향 요약

| 제안 | 상태 | 트윈 액션 |
|---|---|---|
| P1 MedGemma 1.5 | ✅ **완결**(텍스트+한글+영상 멀티모달) | 무변경(품질 향상 수혜) |
| P2 심혈관 세분 | plumbing 준비 | TS 라이선스 정책 + HIS #2 심장 CT |
| P3 EP 대리모델 | 플러밍+PoC 완료 | 무(P3c R&D는 AI 소유) |

## 커밋

- `1f61a27` feat(vision): P1 한글 네이티브 옵션
- `3bac0dc` feat(avatar): P2 chamber plumbing + P3 엔진 플러밍
- `1e98d43` feat(avatar): P3b openCARP PoC
- `c78ad87` feat(vision): 영상 MedGemma 1.5 멀티모달 업그레이드
- `519a44a`·`0800096`·`b8b4601`·`99ab6ab` docs(동기화·회신서)
