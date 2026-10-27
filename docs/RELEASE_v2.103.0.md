# Release v2.103.0 — 아바타 R2: 영상→장기 메시(glTF) 엔드포인트 라이브

**2026-06-12** | 이전: [v2.102.1](RELEASE_v2.102.1.md)

## 요약

메디컬 디지털 트윈팀 아바타 공동진화 **R2** — 트윈측 페치 경로(서명 URL) 완비, AI측
세그→glTF 엔드포인트만 미공개였던 항목을 **프로덕션 엔드포인트로 공개**. v2.89.0 PoC
(TotalSegmentator fast 3mm·마스크→glTF E2E, VRAM 정책)를 비동기 API로 결선.

## 엔드포인트 (트윈 합의 인터페이스 — avatar-reply4 §Q3)

- `POST /api/vision/medical/organ-mesh` — `{pixels_url(서명URL, NIfTI.gz), format, mode, roi}` → **202 {job_id, status, poll_url}**.
- `GET /api/vision/medical/organ-mesh/<job_id>` — 폴링(5~10s): status pending→downloading→segmenting→uploading→done, done 시 `mesh_url`(presigned glTF).
- `GET /api/vision/medical/organ-mesh-status` — 파이프라인 현황(잡 상태별·격리 venv 가용).

## 파이프라인 (`core/avatar_mesh.py` + `scripts/avatar_organ_mesh.py`)

백그라운드 워커(요청핸들러 비블로킹):
1. 서명 URL → AI 다운로드(SSRF `resolve_host_is_public` 워커검증, 크기 상한).
2. **격리 venv `~/ts_poc_venv`** 파이썬으로 `scripts/avatar_organ_mesh.py` subprocess 호출 —
   TotalSegmentator(fast 3mm, lung 5엽) → skimage.marching_cubes(voxel spacing) →
   trimesh(>20k면 quadric decimation) → 멀티지오메트리 Scene → `.glb`.
3. S3(Wasabi) presigned URL(짧은 TTL) → 잡 done.

## 안전·거버넌스
- **동시성 1**(VRAM 직렬화) · **fast 3mm 기본**(full=1.5mm OOM 위험 → 기본 차단·fast 강등, config `avatar_allow_full`).
- **비식별**: glTF 지오메트리만(DICOM 태그/PatientID/UID 미임베드), temp 처리 후 폐기.
- 감사 `PHI_ACCESS endpoint=avatar-mesh`. 입력은 공인 https 서명 URL만(로컬 입력은 테스트 게이트).

## 설정(GPU)
- 격리 venv `avatar_ts_python`(기본 `~/ts_poc_venv/bin/python`), `avatar_mesh_ttl_s`,
  `avatar_allow_full`(기본 false), `avatar_allow_local_input`(테스트, 기본 false). S3(s3_enabled) 필요.

## 검증
- iMac: `pytest tests/test_avatar_mesh.py` → 6 passed(결과파싱·SSRF·로컬차단).
- GPU(배포 후): 예제 CT로 세그→메시 E2E + 엔드포인트 202→폴링→done(mesh_url presigned) 확증.
