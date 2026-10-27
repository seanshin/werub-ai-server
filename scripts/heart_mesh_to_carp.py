#!/usr/bin/env python3
"""심장-class 표면 메시 → tetrahedral 볼륨 → openCARP CARP 메시(.pts/.elem/.lon) (P3d).

ts_poc_venv 전용(pyvista+tetgen+numpy). 닫힌 표면(심장 근사 타원체 또는 실 분할 표면)을
tetgen으로 체적화 + 규칙기반 원주 섬유장 → openCARP 볼륨 시뮬 입력.
좌표 단위 μm(openCARP 관례). 실 심장 표면(R2/R3 메시)도 동일 경로로 체적화 가능.

  ~/ts_poc_venv/bin/python scripts/heart_mesh_to_carp.py --out /work/sweep/heart --edge 2500
"""
import argparse
import os

import numpy as np


def ellipsoid_surface(a, b, c, res):
    import pyvista as pv
    s = pv.ParametricEllipsoid(a, b, c, u_res=res, v_res=res, w_res=res)
    return s.triangulate().clean()


def circumferential_fibers(centroids):
    """장축(z) 기준 원주방향 섬유 f = norm(zaxis × radial). (M,3) → (M,3) 단위벡터."""
    z = np.array([0.0, 0.0, 1.0])
    radial = centroids.copy()
    radial[:, 2] = 0.0
    rn = np.linalg.norm(radial, axis=1, keepdims=True)
    radial = np.where(rn > 1e-6, radial / np.maximum(rn, 1e-9), np.array([1.0, 0, 0]))
    f = np.cross(np.tile(z, (len(centroids), 1)), radial)
    fn = np.linalg.norm(f, axis=1, keepdims=True)
    return np.where(fn > 1e-6, f / np.maximum(fn, 1e-9), np.array([1.0, 0, 0]))


def write_carp(pts_um, tets, fibers, out):
    """openCARP .pts(μm)/.elem(Tt)/.lon(1축) 작성."""
    with open(out + ".pts", "w") as f:
        f.write(f"{len(pts_um)}\n")
        for p in pts_um:
            f.write(f"{p[0]:.4f} {p[1]:.4f} {p[2]:.4f}\n")
    with open(out + ".elem", "w") as f:
        f.write(f"{len(tets)}\n")
        for t in tets:
            f.write(f"Tt {t[0]} {t[1]} {t[2]} {t[3]} 1\n")
    with open(out + ".lon", "w") as f:
        f.write("1\n")
        for v in fibers:
            f.write(f"{v[0]:.5f} {v[1]:.5f} {v[2]:.5f}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--a", type=float, default=30000.0)   # 단축 반경(μm)=30mm
    ap.add_argument("--b", type=float, default=30000.0)
    ap.add_argument("--c", type=float, default=45000.0)   # 장축 반경(μm)=45mm
    ap.add_argument("--res", type=int, default=40)
    ap.add_argument("--mindihedral", type=float, default=18.0)
    ap.add_argument("--minratio", type=float, default=1.6)
    args = ap.parse_args()

    import tetgen
    surf = ellipsoid_surface(args.a, args.b, args.c, args.res)
    tet = tetgen.TetGen(surf)
    tet.tetrahedralize(order=1, mindihedral=args.mindihedral, minratio=args.minratio)
    grid = tet.grid
    pts = np.asarray(grid.points, dtype=float)                 # μm
    cells = grid.cells_dict[10]                                # VTK_TETRA=10 → (M,4)
    centroids = pts[cells].mean(axis=1)
    fibers = circumferential_fibers(centroids)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    write_carp(pts, cells, fibers, args.out)
    print(f"CARP mesh -> {args.out}.{{pts,elem,lon}}")
    print(f"  nodes={len(pts)} tets={len(cells)} "
          f"bbox_um x[{pts[:,0].min():.0f},{pts[:,0].max():.0f}] z[{pts[:,2].min():.0f},{pts[:,2].max():.0f}]")
    # apex(장축 하단) 노드 인덱스 — 자극 위치 기준
    apex = int(np.argmin(pts[:, 2]))
    print(f"  apex_node={apex} at {pts[apex].round(0).tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
