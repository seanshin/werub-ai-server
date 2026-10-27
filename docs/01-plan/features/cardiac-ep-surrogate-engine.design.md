# 설계 — 심장 EP 대리모델 엔진(P3) 타당성·단계

> 작성: AI 서버팀 · 일자: 2026-06-21 · 대상: 트윈 제안서 P3(BLNM/LNODE/PINO)
> 전제: `cardiac-ep` 출력 인터페이스(per-vertex glTF `_LAT` + 12-lead ECG JSON + `engine` provenance)는 **락**. 후속 대리모델은 동일 계약으로 드롭인 — 트윈 소비측(`cardiacViz.ts`·`EcgPanel.tsx`) 무변경.

## 1. 현 상태 (2026-06-21)
- 엔진: `eikonal-surrogate-v1.1`(CPU·순수 numpy) — 표면 측지 eikonal LAT(다중 개시부+이방성 전도) + 현상학적 이동쌍극자 12-lead ECG. R3 라이브.
- **✅ 엔진 선택 플러밍 구현(이 사이클)**: `core/cardiac_ep.py` 엔진 레지스트리(`DEFAULT_ENGINE`/`SUPPORTED_ENGINES`) + `engine` 컬럼(자동 마이그레이션) + `submit(engine=)` 검증, `scripts/cardiac_ep_run.py` `--engine` 디스패치 지점, 라우트 `engine` optional 파라미터(트윈 미전송=기본). → **대리모델 추가 시 코드 변경 = 레지스트리 등록 + compute 분기 1곳**.
- openCARP: **미설치**(확인). 학습 데이터 생성 경로 부재.

## 2. 후보 (트윈 서베이 근거)
| 후보 | 성격 | 출력 적합성 | 비용 |
|---|---|---|---|
| **BLNM**(Branched Latent Neural Maps) | faster-than-real-time 12-lead ECG 대리 | ECG 직접 — `ecg_12lead` 대체 | 학습 데이터(openCARP/실ECG) |
| **LNODE**(Latent Neural ODE) | PV(압력-용적) 동역학 잠재표현 | 펌프기능(신규 차원, 인터페이스 확장 필요) | 학습 데이터 |
| **PINO**(Physics-Informed Neural Operator, arXiv 2511.08418) | 물리제약 신경연산자 EP | LAT 필드 대체 — `_LAT` 직접 | 물리손실 학습 |

전부 **학습 기반**(대체 아닌 보완) → openCARP ground-truth 또는 실 ECG 코호트 선행.

## 3. openCARP 파이프라인 (P3b ✅ 설치·실증 완료 / P3c~ 미착수)

**✅ P3b PoC 완료(2026-06-21, GPU)** — `scripts/opencarp_poc.sh` 재현:
- **설치**: Docker 공식 이미지 `docker.opencarp.org/opencarp/opencarp:latest`(2.35GB, CPU/MPI). 격리·호스트 무오염. **GPU/Ollama 무영향 검증**(health 200·NRestarts 0·GPU 12.2GB 불변). 디스크 ~3.5G(이미지+실험).
- **단일세포 EP**: `bench`(tenTusscherPanfilov 인간 심실 이온모델) → 300ms 활동전위 생성, real-time 56×.
- **조직 monodomain → LAT**: 튜토리얼 `02_EP_tissue/00_simple`(carputils, 4 MPI) → `vm.igb`(막전위 756노드×21스텝) + **`init_acts_activation-thresh.dat`(LAT)** = 756노드 활성화, 0.62→6.17ms, 전파 spread 5.55ms(매끄러운 파면). **= 대리모델 학습 ground-truth 생성 검증.**

**✅ P3c-1 데이터셋 생성 파이프라인 착수(2026-06-21)** — `scripts/opencarp_sweep.py`:
- 전도도 `g_mult`(CV 프록시) × 자극 위치(메시 x-경계 양면) 스윕 → openCARP monodomain → 노드별 LAT(`init_acts`) 추출 → **(param_vector, LAT[nnodes]) 데이터셋 npz**(params·lat·coords·param_names).
- 초기 데이터셋: 10 sims × 756노드. **물리 검증**: g_mult 0.3→1.0 ⇒ LAT_max 6.87→3.63ms(전도↑→활성↓, 단조). **공간 다양성**: xmin/xmax 자극 LAT 필드 corr −0.999(반전 파면=별개 샘플). 디스크 196K.
- 스케일: 동일 하베스터로 g_mult/자극/병태/이방성 축 확장 → 수백 sims 대규모 데이터셋(P3c-2).

**✅ P3c-2 신경연산자 학습 PoC(2026-06-21)** — `scripts/cardiac_surrogate_train.py`:
- 스윕 확장: 이방성 축(`g_it`) 추가 → `g_mult × g_it × stim` = **30 sims** 데이터셋. (관찰: 슬랩은 파면이 섬유 종방향 전파라 `g_it`가 LAT_max에 미미 — 주축은 CV·자극. 실 심장 메시에선 이방성 기여 증가 예상.)
- **모델**: coord-conditioned MLP(DeepONet-lite) — 입력 `[params, node_coord]` → 노드 LAT. **sim 단위 held-out**(노드 누수 방지=진짜 미지 파라미터 일반화).
- **초기 결과(쉬운 분포)**: 30 sims 면자극 → held-out test RMSE 0.027ms(0.4%). 단 슬랩 면자극=거의 1D라 학습 trivial.
- **✅ 견고화(2026-06-21)**: **점 자극(랜덤 12위치)+`g_il`/`g_it` 동시 스윕+자극위치 파라미터화** → **144 sims**(LAT span 9.9ms, 3D 이방성 파면). 보강 학습기(MLP 256×4·미니배치·cosine LR). **held-out 29 sims test RMSE 0.106ms(1.1%)**, baseline(평균) 1.749ms 대비 **16.5×**. → 모델이 임의 자극점+이방성 전도도로부터 LAT장을 예측, **미지 (전도도,위치) 조합에 1.1% 일반화**(CV 스케일링 아닌 전파 물리 학습).
- **정직한 한계**: 슬랩 기하라 여전히 실 심장보다 단순. 실 환자 심장 메시(복잡 해부+병태+UVC 섬유장)는 더 큰 sims+정식 연산자(PINO/BLNM) 필요. **파이프라인(스윕→데이터셋→학습→일반화)·견고화(점자극·위치 파라미터화)는 end-to-end 입증**.

**✅ P3d 착수(2026-06-21) — 심장-class 기하 대리모델 + A/B + 역문제 실증**:
- **⚠️ biophysical openCARP whole-heart 벽**: 심장-class 표면→tet(`scripts/heart_mesh_to_carp.py`, pyvista+tetgen) 변환은 성공하나, monodomain EP는 **fine mesh(~0.2mm=수백만 요소)** 필요 → 4013노드(~2mm) 메시는 **전도 차단**(부분 활성 526/4013). 우리 16GB/단일박스선 whole-heart biophysical 스윕 비현실적(설계 §6 Phase4 HPC 사유 재확인).
- **피벗 — eikonal 교사 대리모델**: 현 `eikonal-surrogate-v1.1`을 교사로 심장-class 표면(972노드)서 CV·병태 스윕(`scripts/eikonal_heart_sweep.py`, 25 sims, LAT 119~322ms) → coord-conditioned MLP 학습. **held-out 미지(CV,병태) test RMSE 6.5ms=2.0%**.
- **A/B(`scripts/cardiac_surrogate_ab.py`)**: 신경 vs eikonal 충실도 **RMSE 1.3~1.4%** + **6~9× 빠름**(eikonal 18.9ms vs neural 3.0ms). **미분가능 역문제**(eikonal Dijkstra 불가): 관측 LAT→경사하강 300스텝→**CV 0.62 복원(오차 0.0003)** = 부정맥 기원/파라미터 추정·instant what-if **차별 가치 실증**.

**P3d 잔여(프로덕션 등록 게이트)**:
2. **기하 일반화**: 현 대리는 학습 기하(타원체) 전용 — 임의 환자 심장 형상엔 미일반화. 프로덕션 엔진 등록은 **다(多)심장 학습 + 그래프/메시 신경연산자** 필요(미등록=오LAT 방지). 단일 기하 대리는 PoC.
3. **biophysical grounding**: openCARP whole-heart=HPC-gated. eikonal 교사는 현 엔진 복제(미분/속도 이득)지 새 biophysics 아님.
- **결론**: 엔진 플러밍(P3a)·대리모델 개념·A/B·역문제 = ✅ 실증. **프로덕션 `SUPPORTED_ENGINES` 등록은 보류**(기하 일반화 미충족 시 오LAT 위험) — HPC+多심장 학습이 트리거.

## 4. 16GB VRAM·운영 영향
- **추론**: 신경연산자 추론은 경량(수십~수백 MB) → 16GB 적합. eikonal과 동일하게 아바타 락(R2와 직렬화)으로 LLM 상주와 공존.
- **학습**: 비용 집중 구간이나 openCARP=CPU/MPI라 GPU 무경합. 학습은 오프라인(배치).
- **안전**: 자문 보조·진단 아님·disclaimer 불변. 대리모델도 동일 비식별·미저장.

## 5. 단계 (비블로킹 — 현 eikonal 기본 유지)
- **P3a ✅(완료)**: 엔진 선택 플러밍(레지스트리·`--engine`·DB·라우트). 후속 드롭인 준비 완료.
- **P3b ✅(완료)**: openCARP 설치(Docker) + 단일세포 EP + 조직→LAT ground-truth 생성 실증(`scripts/opencarp_poc.sh`).
- **P3c-1 ✅**: 파라미터 스윕→LAT 데이터셋 파이프라인(`scripts/opencarp_sweep.py`).
- **P3c-2 ✅(PoC+견고화)**: 점자극·`g_il`/`g_it`·자극위치 파라미터화 144 sims + 신경연산자 학습(`scripts/cardiac_surrogate_train.py`) — held-out 29 sims test RMSE 0.106ms(1.1%), 미지 (전도도,위치) 일반화 입증.
- **P3d ✅(개념·A/B 실증, 프로덕션 등록 보류)**: 심장-class 기하 대리모델(eikonal 교사, held-out 2.0%) + A/B(충실도 1.3~1.4%·6~9× 빠름) + **미분가능 역문제**(CV 복원 오차 0.0003, eikonal 불가)=차별 가치 실증. **biophysical openCARP whole-heart=fine mesh HPC-gated**(4013노드 전도차단 확인). **프로덕션 `SUPPORTED_ENGINES` 등록은 기하 일반화(多심장+그래프 연산자) 미충족이라 보류**(오LAT 방지). 트리거=HPC+多심장 학습. 현 eikonal-surrogate-v1.1 라이브 유지.

## 6. 트윈 영향
- **현재**: 0(인터페이스 불변, `engine` 필드로 버전 추적). 트윈은 `engine` 미전송 → 기본 eikonal.
- **대리모델 채택 후**: 트윈은 `engine=<surrogate>` 옵션 전송 또는 AI 기본 전환 시 자동 수혜. 렌더·ECG 패널 무변경.
