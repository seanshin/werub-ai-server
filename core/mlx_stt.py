"""Apple Silicon STT — mlx-whisper wrapper (iMac Worker Phase 2).

faster-whisper(CTranslate2)는 MPS 미지원 + gunicorn fork 크래시 문제가 있어
iMac Worker에서는 사용 불가. mlx-whisper는 MLX 프레임워크로 Metal/ANE에서
직접 실행되므로 fork-safe하고 메모리 효율적 (~1.5-2GB, large-v3 기준).

기본 모델: mlx-community/whisper-large-v3-mlx (Korean 최적)
환경변수: MLX_WHISPER_MODEL — HuggingFace 경로 또는 로컬 경로 오버라이드
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile

logger = logging.getLogger("worker")

MLX_WHISPER_MODEL = os.environ.get(
    "MLX_WHISPER_MODEL",
    "mlx-community/whisper-large-v3-mlx",
)
CHUNK_DURATION_SEC = 1800  # 30분 — 긴 회의 녹화 대비
MAX_AUDIO_BYTES = 200 * 1024 * 1024  # 200 MB


def is_available() -> bool:
    try:
        import mlx_whisper  # noqa: F401
        return True
    except ImportError:
        return False


_FFMPEG = None


def _find_ffmpeg() -> str:
    """ffmpeg 바이너리 경로 탐색 (Homebrew + 시스템 PATH)."""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    import shutil
    # Homebrew on Apple Silicon 기본 경로 먼저 확인
    for candidate in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.isfile(candidate):
            _FFMPEG = candidate
            return _FFMPEG
    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG = found
    return _FFMPEG or "ffmpeg"


def ffmpeg_available() -> bool:
    try:
        subprocess.run([_find_ffmpeg(), "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _to_wav(src: str, out_dir: str) -> str:
    """임의 오디오/비디오 → 16kHz mono WAV (이미 WAV면 그대로)."""
    if src.lower().endswith(".wav"):
        return src
    dst = os.path.join(out_dir, "audio.wav")
    subprocess.run(
        [_find_ffmpeg(), "-i", src,
         "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
         dst, "-y", "-loglevel", "warning"],
        check=True, timeout=600,
    )
    return dst


def _get_duration(path: str) -> float:
    # ffprobe는 ffmpeg와 같은 디렉터리에 있음
    ffmpeg = _find_ffmpeg()
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe") if os.path.isabs(ffmpeg) else "ffprobe"
    try:
        r = subprocess.run(
            [ffprobe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _split_wav(wav: str, out_dir: str) -> list[str]:
    """30분 초과 시 청킹 — 짧으면 그대로 반환."""
    duration = _get_duration(wav)
    if duration <= CHUNK_DURATION_SEC:
        return [wav]
    chunks: list[str] = []
    offset, idx = 0.0, 0
    while offset < duration:
        chunk = os.path.join(out_dir, f"chunk_{idx:03d}.wav")
        subprocess.run(
            [_find_ffmpeg(), "-i", wav,
             "-ss", str(offset), "-t", str(CHUNK_DURATION_SEC),
             "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
             chunk, "-y", "-loglevel", "warning"],
            check=True, timeout=300,
        )
        chunks.append(chunk)
        offset += CHUNK_DURATION_SEC
        idx += 1
    logger.info("split audio → %d chunks (%.0fs total)", len(chunks), duration)
    return chunks


def transcribe(audio_bytes: bytes, filename: str = "audio.bin", language: str = "ko") -> dict:
    """audio bytes → {"text", "segments", "language", "model", "duration_ms"}.

    MLX(Metal)는 gunicorn fork 이후 MTLCompilerService에 접근 불가.
    subprocess.run(spawn)으로 새 프로세스를 띄워 Metal 컨텍스트를 깨끗이 초기화한다.
    """
    import json
    import sys
    import time

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        # 파일명은 확장자(ffmpeg 포맷 힌트)만 필요하다 — 경로 순회/절대경로 방지를 위해
        # basename+정규화 후 확장자만 취해 결정적 이름으로 쓴다(형제 4곳이 쓰는 패턴).
        # 클라이언트 filename이 `../../x`·`/etc/y`여도 tmp 밖으로 못 나간다(v2.125.23).
        ext = os.path.splitext(os.path.basename(filename or ""))[1]
        if not re.match(r"^\.[A-Za-z0-9]{1,8}$", ext):
            ext = ".bin"
        raw = os.path.join(tmp, "input" + ext)
        with open(raw, "wb") as f:
            f.write(audio_bytes)

        wav = _to_wav(raw, tmp)
        chunks = _split_wav(wav, tmp)

        # ffmpeg_dir을 PATH에 포함시켜 서브프로세스에 전달
        env = os.environ.copy()
        ffmpeg_dir = os.path.dirname(_find_ffmpeg())
        if ffmpeg_dir and ffmpeg_dir not in env.get("PATH", ""):
            env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

        all_segs: list[dict] = []
        all_text: list[str] = []
        time_offset = 0.0

        _INLINE = (
            "import sys,json,mlx_whisper\n"
            "r=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo=sys.argv[2],"
            "language=sys.argv[3],word_timestamps=False)\n"
            "print(json.dumps(r))"
        )

        for chunk in chunks:
            proc = subprocess.run(
                [sys.executable, "-c", _INLINE, chunk, MLX_WHISPER_MODEL, language],
                capture_output=True, text=True, timeout=CHUNK_DURATION_SEC + 300,
                env=env,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or "mlx_whisper subprocess failed")
            result = json.loads(proc.stdout)
            for seg in result.get("segments", []):
                text = seg.get("text", "").strip()
                if text:
                    all_segs.append({
                        "start": round(float(seg["start"]) + time_offset, 2),
                        "end": round(float(seg["end"]) + time_offset, 2),
                        "text": text,
                    })
                    all_text.append(text)
            time_offset += CHUNK_DURATION_SEC

    full_text = " ".join(all_text)
    duration_ms = round((time.time() - t0) * 1000)
    logger.info("mlx-whisper done: %d segs, %d chars, %dms", len(all_segs), len(full_text), duration_ms)
    return {
        "text": full_text,
        "segments": all_segs,
        "language": language,
        "model": MLX_WHISPER_MODEL,
        "duration_ms": duration_ms,
    }
