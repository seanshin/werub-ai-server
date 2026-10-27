#!/usr/bin/env python3
"""edu 재시도 요청 전 자체 검증 — 프로덕션 STT 경로를 그대로 태운다(다운로드만 생략).

edu에 "재시도해 주십시오"라고 요청하려면 그 경로가 실제로 동작하는지 먼저 확인해야 한다
(v2.124.42 교훈: 고쳤다는 범위를 끝까지 따라갈 것). 확인 대상 3가지:
  ① 현재 설정(int8_float16 · 배치 off)에서 284초가 OOM 없이 처리되는가
  ② options.vocabulary → hotwords 전달이 실제로 적용되는가(용어 정확도)
  ③ options.diarize → transcript.segments[].speaker 병합이 실제로 채워지는가
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.meeting_pipeline import (merge_speakers_into_segments, stt_options)
from core.meeting_stt import transcribe, stt_batched, stt_batch_size, _resolve_device_compute

AUDIO = sys.argv[1] if len(sys.argv) > 1 else "/tmp/audio.wav"
TERMS = ["요로감염", "재원일수", "항균력", "중심정맥관", "클로르헥시딘", "포도알균", "코호트"]

# edu가 실제로 보내는 형태 그대로
options = {"diarize": True, "vocabulary": TERMS, "language": "ko"}

lang, prompt, hotwords = stt_options(options)
print("== 설정 ==")
print("device/compute:", _resolve_device_compute(), "| batched:", stt_batched(),
      "| batch_size:", stt_batch_size())
print("stt_options → lang=%r prompt=%r hotwords=%r" % (lang, prompt, hotwords))

t0 = time.time()
full_text, segments = transcribe(AUDIO, language=lang, initial_prompt=prompt, hotwords=hotwords)
stt_s = time.time() - t0
print("\n== ① STT ==")
print("소요 %.1fs · 세그먼트 %d · 문자 %d" % (stt_s, len(segments), len(full_text)))

print("\n== ② 용어 정확도(hotwords) ==")
hit = 0
for t in TERMS:
    ok = t in full_text
    hit += ok
    print("  %-12s %s" % (t, "정확" if ok else "미검출"))
print("  → %d/%d" % (hit, len(TERMS)))

print("\n== ③ 화자 병합 ==")
try:
    from core.meeting_diarize import diarize, merge_stt_diarization, is_available
    if not is_available():
        print("  diarize 미가용 — skip")
    else:
        d0 = time.time()
        diar = diarize(AUDIO, num_speakers=None, max_speakers=10)
        spk_segs = merge_stt_diarization(segments, diar)
        merged = merge_speakers_into_segments(segments, spk_segs)
        spk = sorted({s.get("speaker") for s in spk_segs if s.get("speaker")})
        in_seg = sum(1 for s in segments if s.get("speaker"))
        print("  %.1fs · 화자 %s · speaker_segments %d" % (time.time() - d0, spk, len(spk_segs)))
        print("  merge_speakers_into_segments → %s · transcript.segments[].speaker 채워진 수 %d/%d"
              % (merged, in_seg, len(segments)))
        print("  샘플:", json.dumps(segments[0], ensure_ascii=False)[:200] if segments else "-")
except Exception as e:                                          # noqa: BLE001
    print("  diarize 단계 예외:", type(e).__name__, str(e)[:200])

try:
    import torch
    free, total = torch.cuda.mem_get_info()
    print("\nVRAM 여유 %.2f/%.2f GB" % (free / 1e9, total / 1e9))
except Exception:                                               # noqa: BLE001
    pass
print("\n전사 앞부분:", full_text[:300])
