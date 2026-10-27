#!/usr/bin/env python3
"""심장 EP 대리모델 학습 (P3c-2) — openCARP 스윕 데이터셋 → 신경연산자.

coord-conditioned MLP(DeepONet-lite): 입력 [sim 파라미터, 노드 좌표(x,y,z)] → 출력 노드 LAT(ms).
좌표를 입력으로 받아 공간을 일반화(고정 메시 가정 완화) — PINO/DeepONet 계열의 경량 형태.
**sim 단위 held-out**으로 미지 파라미터 조합에 대한 일반화를 검증(노드 누수 방지).

  venv/bin/python scripts/cardiac_surrogate_train.py --data dataset.npz --out surrogate.pt

출력: 학습 가중치 + 정규화 통계(.pt). cardiac-ep 엔진으로 등록(P3d) 시 이 추론기를 SUPPORTED_ENGINES에 연결.
"""
import argparse

import numpy as np
import torch
import torch.nn as nn


def build_samples(params, lat, coords):
    """(sims, P)·(sims, N)·(N,3) → per-(sim,node) 샘플. lat<0(미활성) 제외.
    반환 X[M, P+3], y[M], sim_id[M]."""
    S, N = lat.shape
    X, y, sid = [], [], []
    for s in range(S):
        for j in range(N):
            if lat[s, j] < 0:
                continue
            X.append(np.concatenate([params[s], coords[j]]))
            y.append(lat[s, j])
            sid.append(s)
    return np.array(X, np.float32), np.array(y, np.float32), np.array(sid)


class MLP(nn.Module):
    def __init__(self, in_dim, hidden=(128, 128, 128)):
        super().__init__()
        dims = [in_dim, *hidden, 1]
        layers = []
        for a, b in zip(dims[:-1], dims[1:]):
            layers += [nn.Linear(a, b), nn.SiLU()]
        self.net = nn.Sequential(*layers[:-1])  # 마지막 활성화 제거

    def forward(self, x):
        return self.net(x).squeeze(-1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=int, default=600)
    ap.add_argument("--batch", type=int, default=8192)
    ap.add_argument("--hidden", default="256,256,256,256")   # 더 큰 문제용 용량
    ap.add_argument("--test-frac", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    d = np.load(args.data)
    params, lat, coords = d["params"].astype(np.float32), d["lat"].astype(np.float32), d["coords"].astype(np.float32)
    pnames = d["param_names"].tolist()
    S = lat.shape[0]

    # sim 단위 train/test 분할(파라미터 일반화 검증 — 노드 누수 방지)
    perm = rng.permutation(S)
    n_test = max(1, int(round(S * args.test_frac)))
    test_sims, train_sims = set(perm[:n_test].tolist()), set(perm[n_test:].tolist())

    X, y, sid = build_samples(params, lat, coords)
    # 정규화 통계는 train에서만 산출
    tr = np.isin(sid, list(train_sims))
    xm, xs = X[tr].mean(0), X[tr].std(0) + 1e-8
    ym, ys = y[tr].mean(), y[tr].std() + 1e-8
    Xn = (X - xm) / xs
    yn = (y - ym) / ys

    dev = torch.device("cpu")  # 작은 모델 — LLM VRAM 무경합
    Xt = torch.tensor(Xn, device=dev)
    yt = torch.tensor(yn, device=dev)
    tr_t = torch.tensor(tr)
    te_t = ~tr_t

    hidden = tuple(int(v) for v in args.hidden.split(","))
    model = MLP(X.shape[1], hidden).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, args.epochs)
    lossf = nn.MSELoss()
    tr_idx = torch.nonzero(tr_t).squeeze(1)
    yt_full = torch.tensor(y)
    eval_every = max(1, args.epochs // 12)
    for ep in range(args.epochs):
        model.train()
        perm_b = tr_idx[torch.randperm(len(tr_idx))]
        for b in range(0, len(perm_b), args.batch):     # 미니배치 SGD(대규모 데이터 일반화·속도)
            bi = perm_b[b:b + args.batch]
            opt.zero_grad()
            lossf(model(Xt[bi]), yt[bi]).backward()
            opt.step()
        sched.step()
        if (ep + 1) % eval_every == 0:
            model.eval()
            with torch.no_grad():
                pred = model(Xt) * ys + ym       # 역정규화(ms)
            rmse_tr = float((((pred[tr_t] - yt_full[tr_t]) ** 2).mean()) ** 0.5)
            rmse_te = float((((pred[te_t] - yt_full[te_t]) ** 2).mean()) ** 0.5)
            print(f"ep {ep+1:4d}  train RMSE {rmse_tr:.3f}ms  test RMSE {rmse_te:.3f}ms")

    model.eval()
    with torch.no_grad():
        pred = (model(Xt) * ys + ym).cpu().numpy()
    rmse_te = float(np.sqrt(((pred[~tr] - y[~tr]) ** 2).mean()))
    lat_span = float(y.max() - y.min())
    baseline = float(np.sqrt(((y[~tr] - y[tr].mean()) ** 2).mean()))  # 평균예측 베이스라인
    print(f"\nSURROGATE: test RMSE {rmse_te:.3f}ms (LAT span {lat_span:.2f}ms, "
          f"baseline(mean) {baseline:.3f}ms) | {len(train_sims)} train / {len(test_sims)} test sims")
    print(f"  rel.error {100*rmse_te/lat_span:.1f}% of LAT span | held-out params: "
          f"{[params[s].tolist() for s in sorted(test_sims)]}")

    torch.save({"state_dict": model.state_dict(), "in_dim": X.shape[1],
                "x_mean": xm, "x_std": xs, "y_mean": float(ym), "y_std": float(ys),
                "param_names": pnames, "test_rmse_ms": rmse_te}, args.out)
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
