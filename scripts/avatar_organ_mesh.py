#!/usr/bin/env python3
"""(격리 venv ~/ts_poc_venv 전용) CT NIfTI → 장기 메시 glTF(.glb) — 아바타 R2.

TotalSegmentator(fast 3mm 기본, roi별 라벨 subset) → 마스크 → skimage.marching_cubes
→ trimesh(>20k면 quadric decimation) → 멀티지오메트리 Scene → .glb 내보내기.

**비식별**: 순수 지오메트리만(DICOM 태그/PatientID/UID 미임베드). 메인 앱 venv가 아닌
격리 venv 파이썬으로 subprocess 호출(core/avatar_mesh.py). 결과는 stdout 마지막 줄 JSON.

  ~/ts_poc_venv/bin/python scripts/avatar_organ_mesh.py input.nii.gz out.glb --mode fast --roi lung
  지원 roi: lung(폐 5엽) | heart(심장) | kidney(좌/우 신장) | liver(간) | heart_chambers(심장 4방+심근+대동맥+폐동맥, ⚠️라이선스)
"""
import argparse
import json
import os
import sys
import tempfile
import time

# TotalSegmentator 'total' 태스크의 roi별 라벨 집합(무라이선스·fast 지원, 다장기 환자특이 3D 아바타)
ROI_LABELS = {
    "lung": ["lung_upper_lobe_left", "lung_lower_lobe_left", "lung_upper_lobe_right",
             "lung_middle_lobe_right", "lung_lower_lobe_right"],
    "heart": ["heart"],                          # R3 심장 EP 표면 메시와 동일 라벨(무료 total task)
    "kidney": ["kidney_left", "kidney_right"],
    "liver": ["liver"],
}

# 별도 task 기반 ROI — 심혈관 11구조급 세분(R3 chamber 정밀화).
# ⚠️ TotalSegmentator 라이선스 필요 + fast 미지원(full-res). 무라이선스 시 graceful 에러.
# 병원=상용이라 무료 학술 라이선스 적용 불확실 → 라이선스 정책 결정 후 활성(roi=heart는 무료 대체).
ROI_TASKS = {
    "heart_chambers": {
        "task": "heartchambers_highres",
        "labels": ["heart_myocardium", "heart_atrium_left", "heart_ventricle_left",
                   "heart_atrium_right", "heart_ventricle_right", "aorta", "pulmonary_artery"],
    },
}


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output_glb")
    ap.add_argument("--mode", default="fast", choices=["fast", "full"])
    ap.add_argument("--roi", default="lung")
    ap.add_argument("--max-faces", type=int, default=20000)
    args = ap.parse_args()

    # 입력은 NIfTI 파일 또는 DICOM 폴더(TotalSegmentator가 둘 다 직접 처리).
    if not os.path.exists(args.input):
        _emit({"ok": False, "error": f"input not found: {args.input}"})
        return 2

    t0 = time.time()
    roi_task = ROI_TASKS.get(args.roi)
    rois = ROI_LABELS.get(args.roi)
    if not roi_task and not rois:
        supported = sorted(list(ROI_LABELS) + list(ROI_TASKS))
        _emit({"ok": False, "error": f"unsupported roi: {args.roi} (지원: {supported})"})
        return 6
    try:
        from totalsegmentator.python_api import totalsegmentator
        import nibabel as nib
        import numpy as np  # noqa: F401
        import trimesh
        from skimage import measure
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "error": f"import failed (격리 venv 확인): {e}"})
        return 3

    # task 기반 ROI(심혈관 세분)는 TS 라이선스 게이트 — 무라이선스 시 명확히 안내(crash 아님)
    if roi_task:
        try:
            from totalsegmentator.config import has_valid_license_offline
            lic_status, _ = has_valid_license_offline()
        except Exception:  # noqa: BLE001
            lic_status = "missing_license"
        if lic_status in ("missing_license", "invalid_license"):
            _emit({"ok": False, "license_required": True,
                   "error": f"roi={args.roi}는 TotalSegmentator 라이선스 필요"
                            f"(task={roi_task['task']}, fast 미지원). status={lic_status}. "
                            f"무라이선스 시 roi=heart(whole-heart, 무료) 사용."})
            return 7

    with tempfile.TemporaryDirectory() as seg_dir:
        try:
            if roi_task:
                # 심혈관 세분 task는 full-res 전용(fast 미지원)
                totalsegmentator(args.input, seg_dir, fast=False,
                                 task=roi_task["task"], quiet=True)
                labels = roi_task["labels"]
            else:
                totalsegmentator(args.input, seg_dir, fast=(args.mode == "fast"),
                                 roi_subset=rois, quiet=True)
                labels = rois
        except Exception as e:  # noqa: BLE001
            _emit({"ok": False, "error": f"segmentation failed: {str(e)[:200]}"})
            return 4

        meshes = []
        for lobe in (labels or []):
            f = os.path.join(seg_dir, lobe + ".nii.gz")
            if not os.path.exists(f):
                continue
            try:
                img = nib.load(f)
                arr = img.get_fdata()
                if arr.max() < 1:
                    continue
                spacing = tuple(float(z) for z in img.header.get_zooms()[:3])
                verts, faces, _, _ = measure.marching_cubes(arr, level=0.5, spacing=spacing)
                m = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                if len(m.faces) > args.max_faces:
                    try:  # trimesh 4.x: 첫 위치인자는 percent → face_count 키워드 필수
                        m = m.simplify_quadric_decimation(face_count=args.max_faces)
                    except Exception as de:  # noqa: BLE001
                        sys.stderr.write(f"lobe {lobe} decimation skipped: {de}\n")
                meshes.append((lobe, m))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"lobe {lobe} mesh error: {e}\n")

        if not meshes:
            _emit({"ok": False, "error": "no organ masks produced (마스크 0 — CT FOV/ROI 확인)"})
            return 5

        scene = trimesh.Scene()
        for name, m in meshes:
            scene.add_geometry(m, geom_name=name)
        scene.export(args.output_glb)  # .glb (binary glTF)

        _emit({
            "ok": True, "glb": args.output_glb, "geometries": len(meshes),
            "vertices": int(sum(len(m.vertices) for _, m in meshes)),
            "faces": int(sum(len(m.faces) for _, m in meshes)),
            "organs": [n for n, _ in meshes],
            "mode": args.mode, "roi": args.roi,
            "seconds": round(time.time() - t0, 1),
            "bytes": os.path.getsize(args.output_glb) if os.path.exists(args.output_glb) else 0,
        })
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
