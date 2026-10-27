#!/usr/bin/env python3
"""심장 EP 대리모델 A/B + 역문제 검증 (P3d).

① 충실도(fidelity): 미지 파라미터에서 신경 대리 vs eikonal(교사) LAT RMSE.
② 미분가능 역문제(eikonal 불가): 관측 LAT → 경사하강으로 CV 복원.
   eikonal(Dijkstra)은 미분 불가라 역문제·what-if에 비효율 → 신경 대리의 차별 가치.

  venv/bin/python scripts/cardiac_surrogate_ab.py --model heart_surrogate.pt
"""
import argparse
import os
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # scripts/ (형제 모듈 import)
from core import cardiac_ep_model as ep  # noqa: E402
from cardiac_surrogate_train import MLP  # noqa: E402
from eikonal_heart_sweep import heart_surface, COND_CODE  # noqa: E402


def load_model(path):
    ck = torch.load(path, map_location="cpu", weights_only=False)
    hidden = []  # in_dim→1 사이 Linear 추론용: state_dict에서 차원 복원
    m = MLP(ck["in_dim"], _infer_hidden(ck["state_dict"]))
    m.load_state_dict(ck["state_dict"])
    m.eval()
    return m, ck


def _infer_hidden(sd):
    return tuple(sd[k].shape[0] for k in sd if k.endswith(".weight"))[:-1]


def predict(m, ck, params_row, coords):
    x = np.concatenate([np.tile(params_row, (len(coords), 1)), coords], axis=1).astype(np.float32)
    xn = (x - ck["x_mean"]) / ck["x_std"]
    with torch.no_grad():
        y = m(torch.tensor(xn)) * ck["y_std"] + ck["y_mean"]
    return y.numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    V, F = heart_surface()
    m, ck = load_model(args.model)

    print("=== ① 충실도 A/B (미지 파라미터) ===")
    for cv, cname in [(0.55, "none"), (0.65, "lvh")]:
        t0 = time.time()
        lat_eik = np.asarray(ep.compute_lat(V, F, cv_mm_per_ms=cv,
                             conditions=[] if cname == "none" else [cname])["lat_ms"])
        t_eik = time.time() - t0
        t0 = time.time()
        lat_nn = predict(m, ck, np.array([cv, COND_CODE[cname]]), V)
        t_nn = time.time() - t0
        rmse = float(np.sqrt(((lat_eik - lat_nn) ** 2).mean()))
        print(f"  cv={cv} {cname:4s}: RMSE {rmse:.2f}ms (span {lat_eik.max():.0f}ms, "
              f"{100*rmse/lat_eik.max():.1f}%) | eikonal {t_eik*1e3:.1f}ms vs neural {t_nn*1e3:.1f}ms")

    print("=== ② 미분가능 역문제: 관측 LAT → CV 복원 (eikonal 불가) ===")
    cv_true, cname = 0.62, "none"
    lat_obs = torch.tensor(np.asarray(ep.compute_lat(V, F, cv_mm_per_ms=cv_true)["lat_ms"]), dtype=torch.float32)
    cv_var = torch.tensor([0.50], requires_grad=True)   # 초기 추정
    cond_c = torch.tensor([COND_CODE[cname]])
    Vt = torch.tensor(V, dtype=torch.float32)
    xm = torch.tensor(ck["x_mean"]); xs = torch.tensor(ck["x_std"])
    opt = torch.optim.Adam([cv_var], lr=0.02)
    for it in range(300):
        opt.zero_grad()
        params = torch.cat([cv_var, cond_c]).repeat(len(V), 1)
        x = torch.cat([params, Vt], dim=1)
        xn = (x - xm) / xs
        pred = m(xn) * ck["y_std"] + ck["y_mean"]
        loss = ((pred - lat_obs) ** 2).mean()
        loss.backward()
        opt.step()
    print(f"  true CV={cv_true} → 복원 CV={cv_var.item():.4f} "
          f"(오차 {abs(cv_var.item()-cv_true):.4f}) — 경사하강 300스텝")
    print("  ⇒ 신경 대리는 미분가능이라 역문제(부정맥 기원/CV 추정)·instant what-if 가능 = eikonal 대비 차별 가치.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
