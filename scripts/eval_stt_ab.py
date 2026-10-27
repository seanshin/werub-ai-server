#!/usr/bin/env python3
"""배치 STT 설정 A/B — 처리 시간 × 전사 일치율 (edu 용량 요청 검증용).

edu가 "동시 4~8 상향"을 요청했으나 실측상 large-v3 float16은 인스턴스당 3.84GB로
현 여유(5.61GB)에서 2개가 안 올라간다(CUDA OOM 실증). 증설 전에 같은 VRAM으로 처리량을
올리는 두 레버가 있어, **실제 강의 파일로 검증**하기 위한 하네스다.

  A. 배치 추론(BatchedInferencePipeline)  — MEETING_STT_BATCHED=1
  B. int8_float16 양자화                  — WHISPER_COMPUTE=int8_float16 (3.84 → 1.93GB)

⚠️ 이 스크립트는 **프로덕션과 별개 프로세스에서 자체 모델을 적재**한다. 상주 LLM·실시간
STT와 VRAM을 다투므로 유휴 시간대(야간)에 돌리고, 사전 점검(--check)을 먼저 하라.

사용:
  python scripts/eval_stt_ab.py --check
  python scripts/eval_stt_ab.py --audio lecture.mp4 --ref lecture.txt
  python scripts/eval_stt_ab.py --audio lecture.mp4 --variants base,batched
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VARIANTS = {
    # name:      (compute_type,      batched)
    "base":      ("float16", False),
    "batched":   ("float16", True),
    "int8":      ("int8_float16", False),
    "int8batch": ("int8_float16", True),
}
# 인스턴스당 실측 VRAM(2026-07-28, RTX 5080 16GB) — 사전 점검 기준값.
VRAM_NEED_GB = {"float16": 3.84, "int8_float16": 1.93}


def free_vram_gb():
    try:
        import torch
        free, _total = torch.cuda.mem_get_info()
        return free / 1e9
    except Exception:                                  # noqa: BLE001 — CPU 환경 등
        return None


def check(compute_types=("float16", "int8_float16")):
    """적재 가능 여부 사전 점검 — 돌리기 전에 OOM 위험을 먼저 알려준다."""
    free = free_vram_gb()
    print(f"free VRAM: {free:.2f} GB" if free is not None else "free VRAM: 조회 불가(CPU?)")
    ok = True
    for ct in compute_types:
        need = VRAM_NEED_GB[ct]
        fits = free is None or free >= need + 0.3      # 0.3GB 여유 마진
        print(f"  {ct:14s} 필요 ~{need:.2f} GB → {'OK' if fits else '부족(OOM 위험)'}")
        ok = ok and fits
    if not ok:
        print("\n⚠️ 여유 부족. 유휴 시간대에 돌리거나, 실시간 STT를 먼저 반납시켜라:")
        print("   python -c \"from core import stt_realtime; print(stt_realtime.unload())\"")
    return ok


def normalize(text):
    """비교용 정규화 — 공백·문장부호 차이는 무시(모델 간 표기 차만 본다)."""
    return re.sub(r"[\s.,!?·…\"'()\[\]]+", "", text or "")


def cer(ref, hyp):
    """문자 오류율(0~1). 참조가 없으면 호출하지 않는다."""
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return None
    sm = difflib.SequenceMatcher(None, r, h)
    return round(1 - sm.ratio(), 4)


def run_variant(audio_path, name, language="ko", hotwords=None):
    """한 변형을 **자식 프로세스처럼 격리된 상태로** 실행 → (초, 텍스트, 세그먼트 수).

    env를 세팅한 뒤 core.meeting_stt를 새로 import해야 모듈 상수가 반영되므로,
    호출자가 변형마다 이 스크립트를 새로 띄우는 --single 모드를 쓴다.
    """
    compute, batched = VARIANTS[name]
    os.environ["WHISPER_COMPUTE"] = compute
    os.environ["MEETING_STT_BATCHED"] = "1" if batched else "0"
    for mod in [m for m in list(sys.modules) if m.startswith("core.meeting_stt")]:
        del sys.modules[mod]
    from core import meeting_stt

    t0 = time.time()
    wav = meeting_stt.extract_audio(audio_path, os.path.dirname(os.path.abspath(audio_path)) or ".")
    text, segs = meeting_stt.transcribe(wav, language=language, hotwords=hotwords)
    return round(time.time() - t0, 2), text, len(segs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", help="강의/회의 오디오·영상 파일")
    ap.add_argument("--ref", help="정답 전사 텍스트 파일(있으면 CER 계산)")
    ap.add_argument("--variants", default="base,batched,int8",
                    help="쉼표 구분: " + ",".join(VARIANTS))
    ap.add_argument("--hotwords", default=None, help="용어 바이어싱 CSV")
    ap.add_argument("--check", action="store_true", help="VRAM 사전 점검만")
    ap.add_argument("--single", help="변형 1개만 실행(내부용 — 프로세스 격리)")
    args = ap.parse_args()

    if args.check:
        sys.exit(0 if check() else 1)
    if not args.audio:
        ap.error("--audio 필요 (또는 --check)")

    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in VARIANTS]
    if unknown:
        ap.error(f"알 수 없는 변형: {unknown} (가능: {list(VARIANTS)})")

    if args.single:
        sec, text, nseg = run_variant(args.audio, args.single, hotwords=args.hotwords)
        print(f"__RESULT__\t{sec}\t{nseg}\t{text}")
        return

    ref = open(args.ref, encoding="utf-8").read() if args.ref else None
    results = {}
    for name in names:
        need = VRAM_NEED_GB[VARIANTS[name][0]]
        free = free_vram_gb()
        if free is not None and free < need + 0.3:
            print(f"[{name}] 여유 {free:.2f}GB < 필요 {need:.2f}GB → 건너뜀(OOM 방지)")
            continue
        # 변형마다 새 프로세스 — 모델·env가 섞이지 않게(같은 프로세스에서 재적재하면
        # 이전 인스턴스 VRAM이 남아 두 번째 변형이 OOM 난다).
        import subprocess
        cmd = [sys.executable, os.path.abspath(__file__), "--audio", args.audio,
               "--single", name]
        if args.hotwords:
            cmd += ["--hotwords", args.hotwords]
        out = subprocess.run(cmd, capture_output=True, text=True)
        line = next((l for l in out.stdout.splitlines() if l.startswith("__RESULT__")), None)
        if not line:
            print(f"[{name}] 실패:\n{out.stderr[-600:]}")
            continue
        _, sec, nseg, text = line.split("\t", 3)
        results[name] = {"sec": float(sec), "segments": int(nseg), "text": text}
        print(f"[{name}] {sec}s · {nseg} segments")

    if not results:
        print("실행된 변형이 없다.")
        return

    base_text = results.get("base", {}).get("text")
    print("\n| 변형 | 처리(초) | 배속 | 세그먼트 | base 대비 일치 | CER |")
    print("|------|----------|------|----------|----------------|-----|")
    base_sec = results.get("base", {}).get("sec")
    for name, r in results.items():
        speed = f"{base_sec / r['sec']:.2f}×" if base_sec else "-"
        agree = ("%.4f" % difflib.SequenceMatcher(
            None, normalize(base_text), normalize(r["text"])).ratio()) if base_text else "-"
        c = cer(ref, r["text"]) if ref else None
        print(f"| {name} | {r['sec']} | {speed} | {r['segments']} | {agree} | {c if c is not None else '-'} |")

    print("\n판독: **처리 시간 단축이 전사 일치를 깨지 않는지**가 핵심이다. "
          "base 대비 일치 0.98 미만이면 품질 변화를 의심하고 원문 대조하라"
          "(CER은 정답 전사가 있을 때만 의미 있다).")


if __name__ == "__main__":
    main()
