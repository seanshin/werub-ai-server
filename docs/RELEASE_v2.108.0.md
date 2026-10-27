# Release v2.108.0 — 영상 Q8 업그레이드 + 아바타 P3 EP 대리모델 R&D + R2/R3 실 심장 종단 검증

**날짜:** 2026-06-21
**대상:** 트윈 디지털트윈팀 — 아바타 공동진화 일단락(production-validated)
**GPU HEAD:** 76c03bb (본 릴리즈 문서 커밋 별도)
**성격:** 영상 판독 품질 업그레이드 + 아바타 EP 대리모델 R&D 완주 + 실 심장 기하 종단 검증

---

## 배경

v2.107.0(영상 MedGemma 1.5 멀티모달 + 비전 한글 + 아바타 P2/P3 groundwork) 이후, ① 영상 판독 정확도 향상(Q8) ② 트윈 협업으로 **실 심장 CT 종단 검증**(외부 게이트 해소) ③ P3 EP 대리모델 R&D 완주(openCARP→데이터셋→신경연산자)를 본 릴리즈로 정리한다. **아바타 R1/R2/R3가 실 심장 기하에서 production-validated** 상태에 도달, 협업을 일단락한다.

## 1. 의료 영상 판독 — Q4 → Q8 업그레이드 (정확도↑)

- **모델**: `MEDICAL_VISION_MODEL: dcarrascosa/medgemma-1.5-4b-it:Q4_K_M → :Q8_0`. 폴백 Q4_K_M.
- **근거**: 실 공개 흉부 X-ray 비교서 Q8이 Q4보다 **완결적 differential** 산출(병변 명시 배제: consolidation·nodules·masses·pneumothorax·fracture, soft tissues 추가).
- **VRAM**: 로드 6.7GB, 14b 상주와 공존 ~15GB < 16.3GB. 프로덕션 E2E: Q8 + 한글 경로(`_ensure_korean`) 상세 한글 판독 정상.

## 2. 아바타 R2/R3 — 실 심장 기하 종단 검증 (외부 게이트 해소)

- 트윈이 **데모 흉부 CT(studyUID …243062, R2 폐로 검증했던 그 CT)를 심장용 재스테이징** → 우리 서버 E2E:
  - **R2**: `roi=heart` 실 심장 메시(organs=[heart]·20K faces·glTF 360KB).
  - **R3**: `cardiac-ep` job `ep-86fa3947d4b3` **done — V29795 정점·LAT 0~203ms·12-lead ECG fs500·glTF 1.2MB·17.8s**(서버 로그 교차검증).
- **의미**: 흉부 CT가 심장 FOV를 포함함을 실증 → "0 mask" 해소. **R3 cardiac-ep가 실 심장 기하(29,795 정점)에서 생리적으로 타당한 전심장 LAT(0~203ms, 슬랩 0~10ms 대비)를 생성** = 환자특이 CT(P2026-0008·HIS 게이트) 없이 R2 실메시·R3 정밀화 데모 결선.

## 3. 아바타 P3 — EP 대리모델 R&D 완주 (개념 입증)

> 현 `eikonal-surrogate-v1.1` 라이브 유지(비블로킹). 아래는 향후 정밀/실시간 엔진 후보의 end-to-end 입증.

- **P3a 엔진 플러밍**: `core/cardiac_ep.py` 레지스트리(`DEFAULT_ENGINE`/`SUPPORTED_ENGINES`)·`engine` 컬럼(자동 마이그레이션)·`submit(engine=)`·`cardiac_ep_run.py --engine` 디스패치·라우트 optional. **출력 인터페이스 락** — 후속 대리모델 드롭인.
- **P3b openCARP PoC**: Docker 격리 설치(CPU/MPI·GPU/Ollama 무영향). 단일세포 EP + 조직 monodomain → LAT ground-truth. `scripts/opencarp_poc.sh`.
- **P3c 데이터셋 + 신경연산자**: `scripts/opencarp_sweep.py`(점자극·`g_il`/`g_it`·자극위치 파라미터화 → **144 sims**) + `scripts/cardiac_surrogate_train.py`(coord-conditioned MLP/DeepONet-lite). **held-out 29 sims test RMSE 0.106ms(LAT span 1.1%, baseline 16.5×)** — 미지 (전도도, 자극위치) 조합 일반화·전파 물리 학습 입증.
- **P3d(ready-to-resume)**: 실 심장 메시(§2, 29,795 정점) 가용 → openCARP 볼륨화+스윕+학습+엔진 등록은 트리거(트윈 실시간 인터랙션 요구 / 환자 CT) 시 착수. 설계 `docs/01-plan/features/cardiac-ep-surrogate-engine.design.md`.

## 4. 트윈 요청 처리 현황

| # | 요청 | 상태 |
|---|---|---|
| 1 | 데모 흉부 CT 심장 재스테이징 | ✅ **CLOSED** (R2 실메시 + R3 실 심장 EP 종단) |
| 3 | P1 MedGemma 1.5 영상 | ✅ **CLOSED** (Q8 멀티모달·한글 라이브, 트윈 재프로빙) |
| 2 | 환자특이 실 CT(P2026-0008) | 🟡 HIS Orthanc 정합 게이트 |
| 4 | 실 12-lead ECG | ⚪ 미래·기기 게이트 |

## 검증

- 영상 Q8 E2E: 실 X-ray → 상세 한글 판독, VRAM 공존, health 200·NRestarts 0.
- R3 실 심장: job ep-86fa3947d4b3 done(V29795·LAT 0~203ms) 서버 로그 확인.
- P3: openCARP LAT 생성·신경연산자 일반화 1.1%(GPU CPU 학습, LLM 무경합)·engine bad→clean error. 문법훅 218 통과.

## 일단락

아바타 R1(생리)·R2(장기 메시)·R3(심장 EP)가 **실 심장 기하에서 production-validated**. AI↔트윈 능동 요청 소진(#1·#3 CLOSED, #2·#4 외부 게이트). P3 대리모델은 개념 입증 완료·ready-to-resume.

## 커밋

- `c5753f1` feat(vision): Q4→Q8 영상 판독 업그레이드
- `4c92bb1`·`1e7aab8`·`a7409aa` feat(avatar): P3c openCARP 스윕 + 신경연산자 + 견고화
- `76c03bb` docs(avatar): 트윈 요청 #1/#3 CLOSED — 실 심장 종단 검증
- `7bbd713`·`2b57ee9`·`5fd79f5` docs(동기화)
