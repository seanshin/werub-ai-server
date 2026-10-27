#!/usr/bin/env python3
"""openCARP 파라미터 스윕 → LAT 데이터셋 생성기 (P3c — 대리모델 학습 ground-truth).

openCARP 컨테이너 내부에서 실행(openCARP 바이너리 + python3 + numpy 필요):
  docker run --rm -v ~/opencarp_poc:/work IMG \
    python3 /work/sweep/opencarp_sweep.py \
      --base /work/sweep/base.par --mesh /work/sweep/mesh/block \
      --out /work/sweep/dataset.npz --tmp /tmp/sweep

각 시뮬: 전도도 배율(g_mult=CV 프록시) × 자극 위치(메시 x-경계 양면)를 스윕 →
openCARP monodomain → 노드별 LAT(init_acts) 추출 → (param_vector, LAT[nnodes]) 누적 → npz.
신경연산자(PINO/BLNM)는 이 (params→LAT) 매핑을 학습한다(P3c 후속).
"""
import argparse
import os
import subprocess
import sys

import numpy as np


def read_pts(path):
    """openCARP .pts: 첫 줄=노드수, 이후 'x y z'(um). → (N,3) float array."""
    with open(path) as f:
        n = int(f.readline().split()[0])
        xyz = np.array([[float(v) for v in f.readline().split()[:3]] for _ in range(n)])
    return xyz


def parse_lat(path, nnodes):
    """init_acts_*-thresh.dat → 길이 nnodes LAT 벡터(ms). 미활성=-1.
    1열(노드순 시간) 또는 2열(node time) 모두 처리."""
    lat = np.full(nnodes, -1.0)
    if not os.path.exists(path):
        return lat
    seq = []
    for ln in open(path):
        toks = ln.split()
        if not toks:
            continue
        try:
            nums = [float(t) for t in toks]
        except ValueError:
            continue
        if len(nums) >= 2:                       # node index + time
            idx = int(nums[0])
            if 0 <= idx < nnodes:
                lat[idx] = nums[-1]
        elif len(nums) == 1:                     # 노드순 단일 시간열
            seq.append(nums[0])
    if seq and not (lat >= 0).any():
        v = np.array(seq)
        lat[:min(nnodes, v.size)] = v[:nnodes]
    return lat


def run_sim(carp, base, mesh, simid, g_il, g_it, stim_box, tend):
    """openCARP 1회 실행(base.par + CLI 오버라이드). 성공 시 True.
    g_il=종방향(주 CV축), g_it=횡방향 세포내 전도도(점 자극 시 이방성 파면 형상 결정)."""
    cmd = [carp, "+F", base, "-meshname", mesh, "-simID", simid,
           "-gregion[0].g_il", str(g_il), "-gregion[0].g_it", str(g_it),
           "-tend", str(tend)]
    for i, v in enumerate(stim_box[:3]):
        cmd += [f"-stim[0].elec.p0[{i}]", str(v)]
    for i, v in enumerate(stim_box[3:6]):
        cmd += [f"-stim[0].elec.p1[{i}]", str(v)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return r.returncode == 0, r.stderr[-300:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)          # base .par
    ap.add_argument("--mesh", required=True)          # meshname(확장자 제외)
    ap.add_argument("--out", required=True)           # 출력 npz
    ap.add_argument("--tmp", default="/tmp/sweep")    # 시뮬 작업 디렉터리
    ap.add_argument("--carp", default="openCARP")
    ap.add_argument("--tend", type=float, default=120.0)
    ap.add_argument("--g-il", default="0.10,0.174,0.30,0.50")   # 종 전도도(주 CV축)
    ap.add_argument("--g-it", default="0.010,0.019,0.040")      # 횡 전도도(이방성)
    ap.add_argument("--n-stim", type=int, default=12)           # 랜덤 자극점 수
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    xyz = read_pts(args.mesh + ".pts")
    nnodes = len(xyz)
    lo = xyz.min(0)
    hi = xyz.max(0)
    extent = hi - lo
    # 점 자극: 메시 내부 K개 랜덤 위치(재현 seed) — 3D 이방성 파면이라 g_il/g_it·자극위치가 모두 의미.
    rng = np.random.default_rng(args.seed)
    margin = 0.12 * extent
    stim_pts = lo + margin + rng.random((args.n_stim, 3)) * (extent - 2 * margin)
    h = 0.06 * float(extent.max())                              # 자극 전극 반치폭
    g_ils = [float(x) for x in args.g_il.split(",")]
    g_its = [float(x) for x in args.g_it.split(",")]

    os.makedirs(args.tmp, exist_ok=True)
    params, lats, meta = [], [], []
    i = 0
    for k, p in enumerate(stim_pts):
        box = [p[0] - h, p[1] - h, p[2] - h, p[0] + h, p[1] + h, p[2] + h]
        for gil in g_ils:
            for git in g_its:
                simid = os.path.join(args.tmp, f"run_{i:03d}")
                ok, err = run_sim(args.carp, args.base, args.mesh, simid, gil, git, box, args.tend)
                lat = parse_lat(os.path.join(simid, "init_acts_activation-thresh.dat"), nnodes)
                act = int((lat >= 0).sum())
                if ok and act > nnodes * 0.5:        # 과반 활성만 채택(자극 실패 케이스 배제)
                    params.append([gil, git, p[0], p[1], p[2]])
                    lats.append(lat)
                    meta.append(dict(g_il=gil, g_it=git, stim_pt=p.tolist(), activated=act,
                                     lat_max=float(lat[lat >= 0].max())))
                    print(f"[{i:03d}] stim{k} g_il={gil} g_it={git} -> act {act}/{nnodes} "
                          f"LAT_max={lat[lat>=0].max():.2f}ms")
                else:
                    print(f"[{i:03d}] stim{k} g_il={gil} g_it={git} SKIP act={act} {err[:80]}")
                i += 1

    if not lats:
        print("no successful sims", file=sys.stderr)
        return 1
    np.savez_compressed(args.out,
                        params=np.array(params), lat=np.array(lats), coords=xyz,
                        param_names=np.array(["g_il", "g_it", "stim_x", "stim_y", "stim_z"]))
    P = np.array(params)
    print(f"\nDATASET: {len(lats)} sims x {nnodes} nodes -> {args.out}")
    print(f"  g_il {P[:,0].min()}-{P[:,0].max()}, g_it {P[:,1].min()}-{P[:,1].max()}, "
          f"{args.n_stim} stim pts")
    print(f"  LAT_max range: {min(m['lat_max'] for m in meta):.1f}-{max(m['lat_max'] for m in meta):.1f} ms "
          f"(이전 슬랩 면자극 3.6-6.9ms 대비 다양성↑)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
