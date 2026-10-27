# Release v2.106.1 — 아바타 R3 cardiac-ep v1.1 정밀화 (다중 개시부 + 이방성 전도)

**날짜:** 2026-06-18
**대상:** 메디컬 디지털 트윈팀 — R3 심장 EP 표면 대리모델 고도화
**GPU HEAD:** a48f01c (본 릴리즈 문서 커밋 별도)
**성격:** 엔진 내부 정밀화 — **인터페이스·정점 정렬·출력 포맷 불변, 트윈 무변경**

---

## 배경

v2.106.0의 R3 cardiac-ep v1.0은 **단일 apex 개시 + 등방 CV** 표면 eikonal이었다. 생리적으로 ① 실제 심실은 His-Purkinje로 **다중 조기활성 부위**(심내막 breakthrough)에서 거의 동시 시작 ② 전도는 **섬유 방향 이방성**(종방향 빠름)이라는 차이가 있어, 단일 점에서 퍼지는 동심원(bullseye) 활성맵은 비현실적이었다. v1.1은 이 둘을 보정한다(엔진만 교체, 트윈 결선 무변경).

## 1. 다중 개시부 (multi-site initiation)

- `select_init_sites()`: 심첨(apex) + 심첨 영역(장축 하단 `apical_frac`) **farthest-point sampling** 5개 → RV/LV 심첨 near-simultaneous breakthrough(His-Purkinje 등가) 근사.
- 효과: 단일 점 bullseye 제거, 더 자연스러운 활성 시퀀스, QRS 폭 현실화.
- `meta.n_init_sites`·`meta.init_sites` 노출. `--n-init`(기본 5)로 조절.

## 2. 이방성 전도 (anisotropic conduction)

- `fiber_field()`: 규칙기반 **원주방향 섬유장**(LDRB 없이 — `f = norm(장축 × 반경방향)`, 중벽 섬유 근사).
- `_aniso_adjacency()`: **타원형 eikonal** 변 가중 — `1/v² = cos²/cv_long² + (1-cos²)/cv_trans²`(cos=|방향·섬유|). 종방향(섬유 따라) 빠르고 횡방향 느림(`cv_long`/`cv_trans`≈1.2/0.7 mm/ms).
- 효과: 활성 파면이 섬유 방향으로 신장 → 해부학적으로 더 그럴듯한 LAT 패턴. `meta.cv_long`/`cv_trans`.

## 3. 하위호환 / 인터페이스 불변

- `compute_lat` 속도 지정 3경로: `cv_long`/`cv_trans` 직접 | `cv_mm_per_ms`(기하평균)+`anisotropy_ratio` | 기본값. 기존 `cv_mm_per_ms` 호출 그대로 동작.
- **출력 포맷·정점 정렬 불변**: glTF `_LAT`·JSON 폴백 `lat.lat_ms[]`·12-lead ECG·202폴링 그대로. 트윈 렌더 코드 변경 0.
- `engine=eikonal-surrogate-v1.1`. 병태 프리셋(lbbb/rbbb/lvh/rvh/lqts) 유지.

## 검증

- 회귀 `test_cardiac_ep` 9 → **12**(다중개시·이방성·섬유장 추가) — iMac/GPU venv 통과.
- 라이브: cardiac-ep-status `engine=eikonal-surrogate-v1.1`(HUP reload), health 200·NRestarts 0.
- 비교(타원체): v1.0 단일·등방 max 129ms → v1.1 다중·이방 max 123ms·5 sites·cv_long/trans 1.2/0.7, LBBB 178ms(벽지연 반영), ECG Einthoven 성립.
- ⚠️ run 스크립트(격리 venv subprocess)는 git pull 시 새 모델 자동 사용(서버 재기동 불요), stats 라벨만 HUP 반영.

## 커밋

| 커밋 | 내용 |
|---|---|
| a48f01c | **R3 cardiac-ep v1.1**(다중 개시부 + 이방성 전도, model/run/test/회신서 §2.5) |

## 잔여 (정밀화 로드맵)

- openCARP 체적 엔진(tet+LDRB 섬유+monodomain) = 물리 엄밀 경로(정점정렬 재보장 필요).
- 신경연산자 ML 대리(openCARP 학습데이터→GNN/DeepONet) = 실시간 LAT·부정맥 기원 역문제. 출력 인터페이스 불변 전제(엔진만 교체).
- 실 심장 FOV 흉부 CT 확보 시 실심장 LAT·ECG 정밀 확정.
