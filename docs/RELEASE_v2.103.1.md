# Release v2.103.1 — 아바타 R2 DICOM(PACS) 입력 지원

**2026-06-12** | 이전: [v2.103.0](RELEASE_v2.103.0.md)

## 요약

R2 organ-mesh 입력을 NIfTI.gz 외 **DICOM(PACS/Orthanc)**까지 확장. PACS=Orthanc 확인
(openpacs-orthanc, DICOMweb his.example.org/dicom-web). TotalSegmentator가 DICOM 폴더를
직접 처리하므로, dicom_zip → 압축해제 → DICOM 폴더 → TS 경로.

## 변경
- `scripts/avatar_organ_mesh.py`: 입력을 NIfTI 파일 또는 **DICOM 폴더**(os.path.exists) 수용 — TS가 둘 다 직접 처리.
- `core/avatar_mesh.py`:
  - `format=dicom_zip` → ZIP 다운로드 → 임시 폴더 압축해제(zip slip·zip bomb 가드: 파일수≤50000·해제크기 상한) → DICOM 폴더 입력.
  - 로컬 입력에 **DICOM 폴더 경로**(PACS 폴더 접근 시) 허용(allow_local_input 게이트).
  - 정리: 다운로드/해제 산출물만 file/dir 모두 폐기(shutil.rmtree).
- 엔드포인트 docstring: format nifti_gz/dicom_zip 양쪽 명시.

## 검증
- 실제 Orthanc CT 시리즈(133 슬라이스) 아카이브 → DICOM 폴더 → TotalSegmentator(fast 3mm lung) E2E.
