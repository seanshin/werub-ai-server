# Release v2.109.0 — P3d 심장 EP 대리모델 A/B·역문제 실증 + 회귀 테스트 다지기

**날짜:** 2026-06-21
**대상:** 트윈 디지털트윈팀 — 아바타 R3 EP 대리모델 R&D 종착
**GPU HEAD:** 9597618 (본 릴리즈 문서 커밋 별도)
**성격:** R&D 마일스톤(대리모델 개념·A/B·역문제 실증) + 신규 코드 회귀 테스트. 현 엔진 라이브 무변경.

---

## 배경

v2.108.0에서 P3 EP 대리모델 R&D를 데이터셋·학습·견고화까지 완주했다. 본 릴리즈는 ① **P3d**(심장 기하 대리모델 → A/B → 역문제)를 우리 하드웨어 한계까지 실증하고, ② 이번 세션 신규 코드(P1 한글 헬퍼·P3a 엔진 플러밍·P2 chamber)를 **회귀 테스트로 고정**한다. 현 `eikonal-surrogate-v1.1` 엔진은 라이브 유지(대리모델은 프로덕션 미등록).

## 1. P3d — 심장 EP 대리모델 A/B + 미분가능 역문제 (개념 실증)

### biophysical 벽 (정직한 한계)
- 심장-class 표면 → tet 변환 성공(`scripts/heart_mesh_to_carp.py`, pyvista+tetgen). 단 openCARP monodomain은 **fine mesh(~0.2mm = 수백만 요소)** 필요 → 4013노드(~2mm) 메시는 **전도 차단**(부분 활성 526/4013). whole-heart biophysical 스윕은 단일 16GB 박스선 비현실적(설계 §6 Phase4 HPC 사유 재확인).

### 피벗 — eikonal 교사 대리모델 (자체 완결)
- 현 `eikonal-surrogate-v1.1`을 교사로 심장-class 표면(972노드)서 CV·병태 스윕(`scripts/eikonal_heart_sweep.py`, 25 sims, LAT 119~322ms) → coord-conditioned MLP 학습 → **held-out 미지(CV,병태) 일반화 RMSE 6.5ms = 2.0%**.

### A/B + 역문제 (`scripts/cardiac_surrogate_ab.py`)
- **충실도**: 신경 vs eikonal LAT RMSE **1.3~1.4%**, 그리고 **6~9× 빠름**(eikonal 18.9ms vs neural 3.0ms).
- **미분가능 역문제**(eikonal Dijkstra 불가): 관측 LAT → 경사하강 300스텝 → **CV 0.62 복원(오차 0.0003)**. ⇒ 부정맥 기원/파라미터 추정·instant what-if = 신경 대리의 **차별 가치** 실증.

### 프로덕션 등록 보류 (정직한 판단)
- 현 대리는 **학습 기하(타원체) 전용** — 임의 환자 심장 형상엔 미일반화 → `SUPPORTED_ENGINES` 등록 시 **오LAT 위험**. 등록 트리거 = **多심장 학습 + 그래프/메시 신경연산자**(기하 일반화) OR **HPC openCARP**(biophysical grounding). 엔진 플러밍(P3a)·개념·A/B·역문제는 ✅ 실증.

## 2. 회귀 테스트 다지기

이번 세션 신규 코드를 라이브 E2E 외 **단위 테스트로 고정**(향후 회귀 차단):
- `tests/test_vision_korean.py`(5) — `_ensure_korean` 4분기(비-ko/빈값 원문·한국어우세 force_korean·영어우세 translate_core mock·실패 폴백 비파괴).
- `tests/test_cardiac_ep_engine.py`(4) — 엔진 레지스트리·engine 컬럼 자동 마이그레이션·미지 엔진 거부·기본 엔진 영속화.
- `tests/test_avatar_mesh.py`(+2) — `SUPPORTED_ROIS` heart_chambers·스크립트 `ROI_TASKS` heartchambers_highres 매핑.
- **전체 스위트 647 passed, 7 skipped**(회귀 0).

## 검증

- P3d: 데이터셋 생성·학습(2.0%)·A/B(1.3~1.4%·6~9× 빠름)·역문제(CV 복원 0.0003) GPU 실행. 학습=CPU(LLM 무경합), health 200·NRestarts 0.
- 회귀: 신규 17 + 전체 647 green. 문법훅 통과.

## 종착 정리

아바타 R3 EP 대리모델 R&D는 **우리 하드웨어에서 도달 가능한 한계까지 완주**(P3a 플러밍·P3b openCARP·P3c 데이터셋/학습/견고화·P3d A/B/역문제). 그 너머(프로덕션 biophysical 엔진)는 HPC + 多심장 학습 인프라 필요 = 별도 단계. 현 eikonal-surrogate-v1.1 라이브.

## 커밋

- `040f159` test: 신규 코드 회귀 테스트 17
- `01db6c2` feat: heart_mesh_to_carp(표면→tet→CARP)
- `dba1f6c` feat: eikonal_heart_sweep(교사 데이터셋)
- `bfb71e7`·`855dfb3` feat/fix: cardiac_surrogate_ab(A/B+역문제)
- `9597618` docs: P3d 결과·게이트
