#!/usr/bin/env python3
"""X10-B — STT 한국어 WER 열화 실측 하네스.

사양(x10-delegation-equivalence-gate.plan.md §B): WER(iMac mlx) − WER(GPU faster-whisper
CUDA large-v3) ≤ 2%p. 정답 전사 대비 각 엔진 WER을 구해 차이를 본다(GPU 전사를 의사
정답으로 쓰지 않는다 — 그건 "불일치율"이지 "열화"가 아니다).

🔴 두 엔진이 서로 다른 하드웨어를 요구한다(GPU=CUDA·iMac=mlx). 그래서 3단계:
  1) GPU에서:   python scripts/verify_stt_wer.py transcribe-gpu  --audio <dir> --out gpu.json
  2) iMac에서:  python scripts/verify_stt_wer.py transcribe-imac --audio <dir> --out imac.json
  3) 어디서든:  python scripts/verify_stt_wer.py compare --ref <dir> --gpu gpu.json --imac imac.json

입력: <audio dir>에 회의 3건(wav/mp3/mp4/m4a) · <ref dir>에 같은 basename의 .txt 정답 전사.
🔑 정답 전사는 **사람 검수본**(운영자). TTS 합성 음성 금지(실 회의 WER을 외삽 불가).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEGRADATION_MAX_PP = 2.0          # 사양: 열화 ≤ 2%p
_AUDIO_EXT = (".wav", ".mp3", ".mp4", ".m4a", ".flac")


def _audio_files(d: str) -> list[str]:
    out = []
    for ext in _AUDIO_EXT:
        out += glob.glob(os.path.join(d, "*" + ext))
    return sorted(out)


def _transcribe_gpu(path: str) -> str:
    from core.meeting_stt import transcribe
    full_text, _ = transcribe(path, language="ko")
    return full_text


def _transcribe_imac(path: str) -> str:
    from core import mlx_stt
    with open(path, "rb") as f:
        res = mlx_stt.transcribe(f.read(), filename=os.path.basename(path), language="ko")
    return res.get("text", "")


def _run_transcribe(engine: str, args) -> int:
    fn = _transcribe_gpu if engine == "gpu" else _transcribe_imac
    files = _audio_files(args.audio)
    if not files:
        print(f"오디오 0건 in {args.audio}", file=sys.stderr)
        return 2
    out = {}
    for p in files:
        key = os.path.splitext(os.path.basename(p))[0]
        print(f"[x10-stt:{engine}] 전사 {key} …")
        try:
            out[key] = _transcribe_gpu(p) if engine == "gpu" else _transcribe_imac(p)
        except Exception as e:                 # 무성 실패 금지 — 명시 기록
            out[key] = None
            print(f"  🔴 실패: {e}", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"engine": engine, "transcripts": out}, f, ensure_ascii=False, indent=2)
    print(f"[x10-stt:{engine}] → {args.out} ({len(out)}건)")
    return 0


def _compare(args) -> int:
    from core.wer import wer_report

    refs = {}
    for p in glob.glob(os.path.join(args.ref, "*.txt")):
        with open(p, encoding="utf-8") as f:
            refs[os.path.splitext(os.path.basename(p))[0]] = f.read()
    if not refs:
        print(f"정답 전사 0건 in {args.ref} (같은 basename의 .txt 필요)", file=sys.stderr)
        return 2
    gpu = json.load(open(args.gpu, encoding="utf-8"))["transcripts"]
    imac = json.load(open(args.imac, encoding="utf-8"))["transcripts"]

    rows = []
    worst_pp = None
    for key, ref in sorted(refs.items()):
        g, m = gpu.get(key), imac.get(key)
        if g is None or m is None:             # 결측=명시 실패
            rows.append({"key": key, "error": "missing transcript",
                         "gpu": g is not None, "imac": m is not None})
            worst_pp = float("inf")
            continue
        rg = wer_report(ref, g)
        rm = wer_report(ref, m)
        delta_pp = (rm["wer"] - rg["wer"]) * 100
        worst_pp = delta_pp if worst_pp is None else max(worst_pp, delta_pp)
        rows.append({"key": key, "ref_words": rg["ref_words"],
                     "gpu_wer": rg["wer"], "imac_wer": rm["wer"],
                     "gpu_cer": rg["cer"], "imac_cer": rm["cer"],
                     "degradation_pp": round(delta_pp, 3)})

    passed = worst_pp is not None and worst_pp <= DEGRADATION_MAX_PP
    result = {
        "spec": "WER(iMac) - WER(GPU) <= %.1f%%p · 회의 %d건" % (DEGRADATION_MAX_PP, len(refs)),
        "worst_degradation_pp": None if worst_pp in (None, float("inf")) else round(worst_pp, 3),
        "passed": passed, "rows": rows,
    }
    out = args.out or "logs/x10_stt_wer.json"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("[x10-stt] worst_degradation=%s%%p → %s · %s" % (
        result["worst_degradation_pp"], "PASS" if passed else "FAIL", out))
    return 0 if passed else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("transcribe-gpu", "transcribe-imac"):
        s = sub.add_parser(name)
        s.add_argument("--audio", required=True)
        s.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--ref", required=True)
    c.add_argument("--gpu", required=True)
    c.add_argument("--imac", required=True)
    c.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.cmd == "transcribe-gpu":
        return _run_transcribe("gpu", args)
    if args.cmd == "transcribe-imac":
        return _run_transcribe("imac", args)
    return _compare(args)


if __name__ == "__main__":
    sys.exit(main())
