#!/usr/bin/env python3
"""심장-class 표면에서 eikonal LAT 스윕 → 대리모델 학습 데이터셋 (P3d).

biophysical openCARP whole-heart는 fine mesh(~0.2mm=수백만 요소) 필요 → HPC-gated.
대신 현 엔진(eikonal-surrogate, core.cardiac_ep_model)을 **교사**로 심장-class 표면에서
파라미터(CV·병태) 스윕 → (params, node_coord → LAT) 데이터셋. 신경 대리모델은 이 eikonal을
미분가능·배치·고속으로 복제(역문제/what-if 가능). cardiac_surrogate_train.py 입력 호환.

  venv/bin/python scripts/eikonal_heart_sweep.py --out heart_eik.npz
"""
import argparse
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import cardiac_ep_model as ep  # noqa: E402


def heart_surface(nlat=26, nlon=36, a=30.0, b=30.0, c=45.0):
    """심장 근사 닫힌 타원체 표면(mm). (V[N,3], F[M,3])."""
    verts = []
    for i in range(nlat + 1):
        w = math.pi * i / nlat
        for j in range(nlon):
            u = 2 * math.pi * j / nlon
            verts.append((a * math.sin(w) * math.cos(u),
                          b * math.sin(w) * math.sin(u), c * math.cos(w)))
    V = np.array(verts, float)
    F = []
    for i in range(nlat):
        for j in range(nlon):
            i00, i01 = i * nlon + j, i * nlon + (j + 1) % nlon
            i10, i11 = (i + 1) * nlon + j, (i + 1) * nlon + (j + 1) % nlon
            F += [(i00, i10, i11), (i00, i11, i01)]
    return V, np.array(F, int)


# 병태 프리셋(eikonal 모델이 해석) → LAT 패턴 다양화
CONDS = {"none": [], "lbbb": ["lbbb"], "rbbb": ["rbbb"], "lvh": ["lvh"], "rvh": ["rvh"]}
COND_CODE = {k: float(i) for i, k in enumerate(CONDS)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cv", default="0.4,0.5,0.6,0.7,0.9")   # 유효 표면 CV(mm/ms)
    ap.add_argument("--nlat", type=int, default=26)
    ap.add_argument("--nlon", type=int, default=36)
    args = ap.parse_args()

    V, F = heart_surface(nlat=args.nlat, nlon=args.nlon)
    cvs = [float(x) for x in args.cv.split(",")]
    params, lats, meta = [], [], []
    for cname, clist in CONDS.items():
        for cv in cvs:
            r = ep.compute_lat(V, F, cv_mm_per_ms=cv, conditions=clist)
            lat = np.asarray(r["lat_ms"], float)
            params.append([cv, COND_CODE[cname]])
            lats.append(lat)
            meta.append((cname, cv, float(lat.max())))
            print(f"cond={cname:5s} cv={cv} -> LAT max {lat.max():.1f}ms (n={len(V)})")

    np.savez_compressed(args.out, params=np.array(params), lat=np.array(lats),
                        coords=V, param_names=np.array(["cv_mm_per_ms", "condition"]))
    P = np.array(params)
    print(f"\nDATASET: {len(lats)} sims x {len(V)} nodes -> {args.out}")
    print(f"  cv {P[:,0].min()}-{P[:,0].max()}, conditions {list(CONDS)}, "
          f"LAT_max {min(m[2] for m in meta):.1f}-{max(m[2] for m in meta):.1f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
