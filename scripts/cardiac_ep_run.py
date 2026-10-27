#!/usr/bin/env python3
"""(격리 venv ~/ts_poc_venv 전용) CT NIfTI → 심장 EP glTF(_LAT) + 12-lead ECG — 아바타 R3.

TotalSegmentator(heart 라벨) → marching_cubes 표면 메시 → (decimation) →
**그 표면 정점 위에서** eikonal LAT + 현상학적 12-lead ECG(core/cardiac_ep_model) →
glTF(.glb)에 per-vertex `_LAT` 어트리뷰트 임베드 → stdout 마지막 줄 JSON(ECG 인라인).

정점 정렬: LAT는 export하는 바로 그 메시 정점에서 계산 → 트윈 재매핑 불요(구조적 보장).
**비식별**: 순수 지오메트리+스칼라장(DICOM 태그 미임베드). 자문 보조·진단 아님.

  ~/ts_poc_venv/bin/python scripts/cardiac_ep_run.py in.nii.gz out.glb --hr 75 --conditions lbbb
"""
import argparse
import json
import os
import sys
import tempfile
import time

# core.cardiac_ep_model(순수 numpy)를 격리 venv에서 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def _to_body_frame(verts, spacing, affine):
    """spacing-스케일 메시 정점 → body frame(x=left,y=foot,z=anterior). 실패 시 원본."""
    import numpy as np
    try:
        vox = verts / np.asarray(spacing, dtype=float)        # 복셀 인덱스 복원
        ras = (affine[:3, :3] @ vox.T).T + affine[:3, 3]      # → RAS 월드 mm
        # RAS(+x Right,+y Anterior,+z Superior) → body(left,foot,anterior)
        return np.column_stack([-ras[:, 0], -ras[:, 2], ras[:, 1]])
    except Exception:  # noqa: BLE001
        return verts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output_glb")
    ap.add_argument("--mode", default="fast", choices=["fast", "full"])
    ap.add_argument("--hr", type=float, default=75.0)
    ap.add_argument("--conditions", default="")     # 콤마구분(lbbb,lvh 등)
    ap.add_argument("--fs", type=int, default=500)
    ap.add_argument("--duration-ms", type=int, default=2500)
    ap.add_argument("--cv", type=float, default=None)        # 유효 표면 CV 기하평균(미지정=모델기본 이방성)
    ap.add_argument("--n-init", type=int, default=5)         # 심첨 다중 개시부 수
    ap.add_argument("--max-faces", type=int, default=60000)
    ap.add_argument("--engine", default="eikonal-surrogate-v1.1")  # 솔버 선택(후속 대리모델 드롭인)
    args = ap.parse_args()

    if not os.path.exists(args.input):
        _emit({"ok": False, "error": f"input not found: {args.input}"})
        return 2
    conditions = [c.strip().lower() for c in args.conditions.split(",") if c.strip()]

    # 엔진 디스패치 — 현재 eikonal surrogate 단일. 후속 대리모델(openCARP 학습 BLNM/LNODE 등)은
    # 동일 출력 계약(_LAT 정점속성 + 12-lead ECG)으로 이 지점에 compute 분기를 추가한다.
    _SUPPORTED_ENGINES = {"eikonal-surrogate-v1.1"}
    if args.engine not in _SUPPORTED_ENGINES:
        _emit({"ok": False, "error": f"unsupported engine: {args.engine} (지원: {sorted(_SUPPORTED_ENGINES)})"})
        return 8

    t0 = time.time()
    try:
        from totalsegmentator.python_api import totalsegmentator
        import nibabel as nib
        import numpy as np
        import trimesh
        from skimage import measure
        from core.cardiac_ep_model import compute_lat, synthesize_ecg_12lead
    except Exception as e:  # noqa: BLE001
        _emit({"ok": False, "error": f"import failed (격리 venv 확인): {e}"})
        return 3

    with tempfile.TemporaryDirectory() as seg_dir:
        try:
            totalsegmentator(args.input, seg_dir, fast=(args.mode == "fast"),
                             roi_subset=["heart"], quiet=True)
        except Exception as e:  # noqa: BLE001
            _emit({"ok": False, "error": f"segmentation failed: {str(e)[:200]}"})
            return 4

        mask_f = os.path.join(seg_dir, "heart.nii.gz")
        if not os.path.exists(mask_f):
            _emit({"ok": False, "error": "no heart mask (CT FOV에 심장 미포함?)"})
            return 5
        img = nib.load(mask_f)
        arr = img.get_fdata()
        if arr.max() < 1:
            _emit({"ok": False, "error": "empty heart mask"})
            return 5
        spacing = tuple(float(z) for z in img.header.get_zooms()[:3])
        verts, faces, _, _ = measure.marching_cubes(arr, level=0.5, spacing=spacing)
        m = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        if len(m.faces) > args.max_faces:
            try:
                m = m.simplify_quadric_decimation(face_count=args.max_faces)
            except Exception as de:  # noqa: BLE001
                sys.stderr.write(f"decimation skipped: {de}\n")

        fv = np.asarray(m.vertices, dtype=float)     # export될 최종 정점(정렬 기준)
        ff = np.asarray(m.faces)
        lat_res = compute_lat(fv, ff, cv_mm_per_ms=args.cv, n_init_sites=args.n_init,
                              conditions=conditions)
        lat = lat_res["lat_ms"]

        v_body = _to_body_frame(fv, spacing, np.asarray(img.affine, dtype=float))
        ecg = synthesize_ecg_12lead(v_body, lat, hr_bpm=args.hr, fs_hz=args.fs,
                                    duration_ms=args.duration_ms, conditions=conditions)

        # per-vertex _LAT 임베드(glTF 커스텀 어트리뷰트, float32)
        m.vertex_attributes["_LAT"] = lat.astype(np.float32)
        m.export(args.output_glb)

        # JSON 폴백(트윈 계약): _LAT 정점속성과 동일 정점 순서의 LAT 배열
        lat_fallback = {
            "lat_ms": [round(float(x), 2) for x in lat],
            "min_ms": round(lat_res["min_ms"], 3),
            "max_ms": round(lat_res["max_ms"], 3),
            "unit": "ms", "vertex_count": int(len(fv)),
            "note": "glTF _LAT 정점속성과 동일 정점 순서(인덱스 정합)",
        }
        _emit({
            "ok": True, "glb": args.output_glb,
            "vertex_count": int(len(fv)), "faces": int(len(ff)),
            "lat_min_ms": round(lat_res["min_ms"], 3),
            "lat_max_ms": round(lat_res["max_ms"], 3),
            "init_index": lat_res["init_index"],
            "n_init_sites": lat_res["n_init_sites"],
            "cv_long_mm_per_ms": lat_res["cv_long"],
            "cv_trans_mm_per_ms": lat_res["cv_trans"],
            "cv_mm_per_ms": lat_res["cv_mm_per_ms"],
            "unreachable": lat_res["unreachable"],
            "conditions": conditions, "mode": args.mode,
            "lat": lat_fallback,
            "ecg_12lead": ecg,
            "engine": args.engine,
            "engine_note": "다중 개시부(심첨 breakthrough 등가)+이방성 전도(섬유 종>횡)",
            "seconds": round(time.time() - t0, 1),
            "bytes": os.path.getsize(args.output_glb) if os.path.exists(args.output_glb) else 0,
            "disclaimer": "표면 EP 대리모델 — 자문 보조용 근사, 진단 아님",
        })
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
