# Release v2.106.0 — 아바타 R3 심장 EP(표면 eikonal) + 다장기 메시

**날짜:** 2026-06-18
**대상:** 메디컬 디지털 트윈팀 — 환자 아바타 공동진화 R3(심장 전기생리)
**GPU HEAD:** 61c6a65
**회신서:** `docs/01-plan/references/avatar-r3-cardiac-ep-confirm-to-twin.md`

---

## 배경

트윈팀이 R3 3건을 확정·요청: 🔴 cardiac-ep 인터페이스(roi=heart·정점정렬 `_LAT`·12-lead ECG·202폴링) · 🟡 다장기 roi(heart/kidney/liver) · 🟢 신경연산자 대리모델(선택, 정보). EP 엔진은 트윈 위임(AI 소유)대로 **표면 eikonal 대리모델**을 채택 — 트윈의 핵심 요구인 **정점 정렬을 구조적으로 보장**한다. R1(생리)·R2(형상)에 이어 **R3(전기생리) 완성**.

---

## 1. 🔴 `POST /api/medical/cardiac-ep` — 심장 EP(표면 LAT + 12-lead ECG)

NIfTI.gz 서명 URL → 백그라운드: 다운로드 → TotalSegmentator(heart) → 표면 메시 → **그 표면 정점 위에서** eikonal LAT + 현상학적 12-lead ECG → glTF(`_LAT` 임베드) S3 presigned + ECG 인라인 JSON. **202+폴링**(organ-mesh 패턴 재사용).

- 요청 `{pixels_url, hr?, conditions?}` → `202 {job_id, status, poll_url}`.
- `GET /api/medical/cardiac-ep/<job_id>` → `{status, mesh_url, lat, ecg, meta}`.
- `GET /api/medical/cardiac-ep-status` → 잡 상태별·격리 venv 가용·엔진.

### 🔑 정점 정렬 — 구조적 보장
- LAT를 **export하는 바로 그 표면 메시 정점에서 직접** 계산(체적솔브→표면투영이 아님) → `lat_ms[i]`는 항상 메시 정점 `i`. **트윈 재매핑 불필요.**
- **1순위 glTF `_LAT`**(per-vertex float, ms) + **2순위 JSON 폴백 `lat.lat_ms[]`**(동일 정점 순서).
- **GLB 바이너리 직접 파싱으로 입증**: 실제 `.glb`의 `_LAT` accessor가 `POSITION`과 동일 순서·정합(max_err 1.5e-5). GLB POSITION 순서 = 우리 메시 fv 순서(max|ΔP|=1.8e-6, float32) → JSON 폴백도 1:1 정합.
- ⚠️ `trimesh` **자체 로더**는 커스텀 `vertex_attributes`를 잘못 재연결하는 버그가 있음 → trimesh로 재읽으면 어긋나 보이나 **GLB 파일 자체는 정상**(three.js/표준 glTF 로더 정상, 트윈 무관).

### EP 엔진 — eikonal 표면 대리모델
- `LAT[v] = (apex 개시부 측지거리) / 유효CV(≈1.0mm/ms, Purkinje 빠른 전파 등가 포함)` — apex 개시 heapq Dijkstra, **CPU·수초~수십초**.
- 12-lead ECG = 활성화 시퀀스 기반 **이동 쌍극자 forward 합성**(현상학적). 표준 12리드, `fs_hz=500`, `duration_ms=2500`, **Einthoven II=I+III 성립**, neurokit2 호환.
- 병태 프리셋: `lbbb/rbbb`=벽 지연(QRS 확장), `lvh/rvh`=CV 둔화, `lqts`=APD 연장(QT↑).
- **정직 프레이밍**: 활성화·ECG 형태는 **자문 보조용 근사**·**진단 아님**(`disclaimer`·`approximate:true` 강제).

### 거버넌스
- 비식별(지오메트리+스칼라장+파형, PHI 0)·temp 폐기·동시성 1(R2 organ-mesh와 **VRAM 락 공유 직렬화**)·감사 `PHI_ACCESS=avatar-cardiac-ep`. 입력 NIfTI.gz만(트윈 합의, dicom_zip 미채택).

### 구현
- `core/cardiac_ep_model.py`(순수 numpy: `compute_lat`+`synthesize_ecg_12lead`, 단위테스트 9) · `scripts/cardiac_ep_run.py`(격리 venv: 세그→메시→LAT/ECG→glTF, body frame=affine RAS 변환) · `core/cardiac_ep.py`(잡 매니저 avatar.db `cardiac_ep_jobs`, R2 `_download`/`_lock`/`_cfg` 재사용).

## 2. 🟡 다장기 organ-mesh roi (heart/kidney/liver)

`POST /api/vision/medical/organ-mesh` 의 `roi`가 4종 지원(기존 lung + heart/kidney/liver). `scripts/avatar_organ_mesh.py ROI_LABELS` 일반화 + `core/avatar_mesh.py SUPPORTED_ROIS` 조기 검증(미지원 400). **호출·응답·202폴링 동일**(트윈 코드변경 0). 다장기 환자특이 3D 아바타(폐+심장+신장+간).

## 3. 🟢 신경연산자 대리모델 — 채택 방향

v1 eikonal로 트윈 요구의 실시간성·정렬 달성. openCARP 풀 monodomain/학습 대리모델은 **출력 인터페이스 불변 전제하에 후속 정밀화 옵션**으로 보존(엔진만 교체, 트윈 무변경). 채택은 AI 소유.

## 🎉 양측 E2E LIVE

트윈 v1.13.8(job `ep-4f5705b9030e`)이 submit/poll/glTF 프록시 오케스트레이션 + 웹 흐름을 결선하고 프로덕션 E2E 검증:
- glTF `_LAT` 프록시 **HTTP 200 · model/gltf-binary · 1.2MB · `_LAT`+`POSITION` 존재** (= 표준 glTF 로더 정상 동작 프로덕션 입증).
- 12-lead ECG 전 12리드 · fs 500 · 2500ms.
- → **아바타 R1(생리)+R2(형상)+R3(전기생리) 전부 LIVE.** 환자 특이 3D 아바타가 형상·생리·전기생리까지 완성.

## 호환성 / 위험

- 신규 엔드포인트 3종(Bearer 인증). organ-mesh roi 확장은 기존 동작 무회귀(lung 불변).
- VRAM: EP 계산은 CPU지만 세그(TotalSegmentator)는 GPU → R2와 VRAM 락 공유 직렬화로 LLM 14b 상주와 공존(12.2/16.3GB).
- 회귀: test_cardiac_ep 9 + test_avatar_mesh 6. 문법훅 218 통과. GPU 배포·HUP 재기동 후 health 200·NRestarts 0.

## 커밋 (주요)

| 커밋 | 내용 |
|---|---|
| 1d55a70 | **R3 cardiac-ep + 다장기 roi**(model/run/잡매니저/엔드포인트3/회신서/테스트) |
| 61c6a65 | 회신서 — GPU 배포·검증 결과(glTF `_LAT` 정합 GLB 파싱 입증) |

## 잔여 (정밀화)

- 심장 FOV 포함 흉부 CT 스테이징 시 정밀 심장 메시(현재 데모는 표면 eikonal 대리 근사). AI 보유 `/tmp` CT는 심장 FOV 미포함(폐만).
- 신경연산자 대리모델 = AI 후속 옵션(실시간 LAT 정밀화·연산 절감·부정맥 기원 역문제).
