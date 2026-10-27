#!/usr/bin/env python3
"""X10-A — detect cpu↔mps 동일성 실측 하네스.

사양(x10-delegation-equivalence-gate.plan.md §A): 같은 이미지 100장을 같은 모델
(Organika/sdxl-detector)에 device만 cpu/mps로 태워 `ai_probability` 절대 편차 < 0.01
전부 만족해야 통과. 판정 임계값은 이 스크립트가 보유한다(사전 등록·코드가 기준).

🔴 mps는 iMac(Apple Silicon)에만 있다 → **iMac에서 실행**. GPU 서버(CUDA)에서는
`--sanity`로 cpu-cpu(같은 device 두 번=편차 0)를 돌려 하네스 로직만 검증할 수 있다.

사용:
  # iMac(확정): 합성 100장
  python scripts/verify_detect_equivalence.py --n 100
  # iMac(확정): 운영자 실이미지
  python scripts/verify_detect_equivalence.py --image-dir /path/to/100imgs
  # GPU(하네스 검증만): cpu vs cpu = 편차 0이어야
  python scripts/verify_detect_equivalence.py --n 20 --sanity
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

THRESHOLD = 0.01          # 사양 원문: |p_mps - p_cpu| < 0.01 (1%p)


def _synth_images(n: int) -> list[bytes]:
    """결정적 합성 n장 — ai_probability가 여러 대역을 갖도록 패턴을 섞는다.
    목적은 판별 정확도가 아니라 device 일관성이므로 합성으로 충분(문서 §A 한계 명시).
    시드 고정 = 재현 가능."""
    from PIL import Image
    import random

    imgs: list[bytes] = []
    for i in range(n):
        rnd = random.Random(1000 + i)          # 장별 결정적 시드
        w = h = 128
        kind = i % 5
        img = Image.new("RGB", (w, h))
        px = img.load()
        if kind == 0:                          # 그라디언트
            for y in range(h):
                for x in range(w):
                    px[x, y] = (x * 2 % 256, y * 2 % 256, (x + y) % 256)
        elif kind == 1:                        # 랜덤 노이즈
            for y in range(h):
                for x in range(w):
                    px[x, y] = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
        elif kind == 2:                        # 색블록 체커
            c1 = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
            c2 = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
            for y in range(h):
                for x in range(w):
                    px[x, y] = c1 if (x // 16 + y // 16) % 2 else c2
        elif kind == 3:                        # 주파수 줄무늬
            period = rnd.randint(2, 20)
            for y in range(h):
                for x in range(w):
                    v = 255 if (x // period) % 2 else 0
                    px[x, y] = (v, (y * 3) % 256, 255 - v)
        else:                                  # 도형 + 텍스트
            from PIL import ImageDraw
            img = Image.new("RGB", (w, h), (rnd.randint(0, 255),) * 3)
            d = ImageDraw.Draw(img)
            for _ in range(6):
                x0, y0 = rnd.randint(0, w), rnd.randint(0, h)
                x1, y1 = rnd.randint(0, w), rnd.randint(0, h)
                col = (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
                d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=col)
            d.text((10, 10), f"img{i}", fill=(255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        imgs.append(buf.getvalue())
    return imgs


def _load_dir(path: str) -> list[bytes]:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    out = []
    for name in sorted(os.listdir(path)):
        if os.path.splitext(name)[1].lower() in exts:
            with open(os.path.join(path, name), "rb") as f:
                out.append(f.read())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100, help="합성 이미지 장수(기본 100)")
    ap.add_argument("--image-dir", help="실이미지 디렉터리(있으면 합성 대신 사용)")
    ap.add_argument("--sanity", action="store_true",
                    help="mps 대신 cpu를 두 번 — 하네스 검증용(편차 0 기대)")
    ap.add_argument("--out", default=None, help="결과 JSON 경로")
    args = ap.parse_args()

    from core import ai_image_detector as det

    if args.image_dir:
        images = _load_dir(args.image_dir)
        source = f"dir:{args.image_dir}"
    else:
        images = _synth_images(args.n)
        source = f"synth:{args.n}"
    if not images:
        print("이미지 0장 — 중단", file=sys.stderr)
        return 2

    dev_b = "cpu" if args.sanity else "mps"
    print(f"[x10-detect] {len(images)}장 · device_a=cpu device_b={dev_b} · thr={THRESHOLD}")

    rows = []
    exceed = 0
    missing = 0
    max_delta = 0.0
    sum_delta = 0.0
    for idx, data in enumerate(images):
        ra = det.classify(data, device="cpu")
        rb = det.classify(data, device=dev_b)
        pa, pb = ra.get("ai_probability"), rb.get("ai_probability")
        if pa is None or pb is None:           # 결측=명시 실패(0으로 세지 않는다)
            missing += 1
            rows.append({"i": idx, "p_cpu": pa, "p_dev": pb, "delta": None,
                         "err_cpu": ra.get("error"), "err_dev": rb.get("error")})
            continue
        delta = abs(pa - pb)
        max_delta = max(max_delta, delta)
        sum_delta += delta
        if delta >= THRESHOLD:
            exceed += 1
        rows.append({"i": idx, "p_cpu": round(pa, 4), "p_dev": round(pb, 4),
                     "delta": round(delta, 4)})

    measured = len(images) - missing
    result = {
        "spec": "detect cpu vs %s · |ai_probability| < %.3f · n=%d" % (dev_b, THRESHOLD, len(images)),
        "source": source, "device_b": dev_b,
        "n": len(images), "measured": measured, "missing": missing,
        "exceed": exceed,
        "max_delta": round(max_delta, 5),
        "mean_delta": round(sum_delta / measured, 5) if measured else None,
        "passed": (exceed == 0 and missing == 0 and measured == len(images)),
        "rows": rows,
    }
    out = args.out or "logs/x10_detect_equiv_%s.json" % dev_b
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("[x10-detect] measured=%d missing=%d exceed=%d max_delta=%.5f → %s" % (
        measured, missing, exceed, max_delta,
        "PASS" if result["passed"] else "FAIL"))
    print("[x10-detect] 결과: %s" % out)
    if args.sanity and max_delta > 0:
        print("⚠️ sanity(cpu-cpu)인데 편차>0 — 비결정성 존재(하네스가 잡음)", file=sys.stderr)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
