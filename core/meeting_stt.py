"""Meeting STT — faster-whisper wrapper for speech-to-text.

Singleton WhisperModel that lazily loads on first use.
Supports chunked transcription for long recordings (30-min segments).
"""

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"
import logging
import os
import subprocess

logger = logging.getLogger("ollama-admin")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL", "large-v3")
# device/compute는 미지정 시 가용 가속기에 맞춰 자동 선택(_resolve_device_compute).
# faster-whisper(CTranslate2)는 CUDA/CPU만 지원 — Apple MPS 미지원이므로
# iMac에서는 cpu로 폴백하거나 mlx-whisper(별도 워커)를 사용한다. [[device]]
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE")    # None이면 auto
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE")  # None이면 device에 맞춰 선택
CHUNK_DURATION_SEC = 1800  # 30분
# 용어 힌트 상한 — whisper prompt는 컨텍스트 윈도(224 토큰)를 넘으면 앞부분이 잘리므로
# 과도한 용어집이 전사 자체를 밀어내지 않도록 자른다.
MAX_INITIAL_PROMPT_CHARS = 600
# 배치 추론·양자화 — 처리량 레버. **config.json 무재배포 토글**(env는 폴백).
# systemd 드롭인은 sudo가 필요해 운영 중 켜고 끄기 어렵다 → 이 프로젝트 관례대로 config로 둔다.
STT_BATCH_SIZE = int(os.environ.get("MEETING_STT_BATCH_SIZE", "8"))


def _cfg(key, default=None):
    try:
        from core.config import load_config
        return load_config().get(key, default)
    except Exception:                      # noqa: BLE001 — config 미가용 시 기본값
        return default


_LANG_CODES_CACHE = {"loaded": False, "codes": None}


def whisper_language_codes():
    """faster-whisper 유효 언어 코드 집합. 미가용(미설치·내부 배치 변경) 시 None(fail-open)."""
    if not _LANG_CODES_CACHE["loaded"]:
        try:
            from faster_whisper.tokenizer import _LANGUAGE_CODES
            _LANG_CODES_CACHE["codes"] = set(_LANGUAGE_CODES)
        except Exception:                  # noqa: BLE001 — 검증 불가 시 현행(무검증)과 동일
            _LANG_CODES_CACHE["codes"] = None
        _LANG_CODES_CACHE["loaded"] = True
    return _LANG_CODES_CACHE["codes"]


def normalize_language(value, default="ko"):
    """language 옵션 정규화 → (정규값, 오류메시지). 오류 시 정규값 None.

    - 미지정 → default("ko")
    - "auto"/"" → None = faster-whisper 자동 감지(language=None). "auto" 문자열을
      그대로 넘기면 tokenizer ValueError — 다운로드·추출을 다 마친 뒤에야 잡이
      실패했다(X7 — 접수 시점 검증이 없던 클래스).
    - 그 외 → whisper 언어 코드면 통과, 아니면 오류(접수 400용). 코드 목록을
      얻지 못하면 통과(fail-open=현행 무검증과 동일 동작).
    """
    if value is None:
        return default, None
    if not isinstance(value, str):
        return None, "language must be a string"
    v = value.strip().lower()
    if v in ("", "auto"):
        return None, None
    codes = whisper_language_codes()
    if codes is not None and v not in codes:
        return None, f"unsupported language '{v}' (whisper 언어 코드 또는 'auto')"
    return v, None


def stt_batched():
    """배치 추론 사용 여부 — config `meeting_stt_batched` > env > False."""
    v = _cfg("meeting_stt_batched")
    if v is None:
        return os.environ.get("MEETING_STT_BATCHED", "0") == "1"
    return bool(v)


def stt_batch_size():
    return int(_cfg("meeting_stt_batch_size") or STT_BATCH_SIZE)


# ---------------------------------------------------------------------------
# Singleton Model
# ---------------------------------------------------------------------------

_model = None
_model_compute = None       # 적재 시 사용한 compute_type — config로 바뀌면 재적재한다
_batched = None


def _resolve_device_compute():
    """faster-whisper용 (device, compute_type) 결정.

    - 명시적 env(WHISPER_DEVICE/WHISPER_COMPUTE)가 있으면 그대로 존중.
    - 없으면 core.device로 cuda 가용성 판단 → cuda(float16) / cpu(int8).
      CTranslate2는 MPS를 못 쓰므로 mps는 cpu로 매핑한다(CPU에서 float16은 크래시 → int8).
    """
    dev = WHISPER_DEVICE
    # config `whisper_compute`가 있으면 우선(무재배포 양자화 전환). 예: int8_float16
    comp = _cfg("whisper_compute") or WHISPER_COMPUTE
    if not dev:
        try:
            from core import device as device_util
            dev = "cuda" if device_util.resolve("auto") == "cuda" else "cpu"
        except Exception:
            dev = "cpu"
    if not comp:
        comp = "float16" if dev == "cuda" else "int8"
    return dev, comp


def _get_model():
    global _model, _model_compute, _batched
    dev, comp = _resolve_device_compute()
    if _model is not None and _model_compute != comp:
        logger.info("whisper compute 변경(%s → %s) → 재적재", _model_compute, comp)
        _model, _batched = None, None      # 배치 파이프라인도 새 모델로 다시 만든다
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Loading faster-whisper: {WHISPER_MODEL_SIZE} ({dev}/{comp})")
        _model = WhisperModel(WHISPER_MODEL_SIZE, device=dev, compute_type=comp)
        _model_compute = comp
        logger.info("faster-whisper loaded")
    return _model


def _get_engine():
    """전사 엔진 반환 — 배치 추론(opt-in) 또는 기본 모델.

    BatchedInferencePipeline은 VAD로 자른 구간을 묶어 디코딩해 **같은 VRAM에서 처리 시간을
    줄인다**(모델 인스턴스 추가 없음). 품질 영향이 파일마다 다를 수 있어 기본 off —
    `MEETING_STT_BATCHED=1`로 켜고 `MEETING_STT_BATCH_SIZE`(기본 8)로 조절한다.
    """
    global _batched
    model = _get_model()
    if not stt_batched():
        return model, False
    if _batched is None:
        try:
            from faster_whisper import BatchedInferencePipeline
            _batched = BatchedInferencePipeline(model=model)
            logger.info("faster-whisper batched pipeline enabled (batch_size=%d)", stt_batch_size())
        except Exception as e:  # noqa: BLE001 — 미지원 버전이면 조용히 기본 경로
            logger.warning("batched pipeline unavailable → 기본 경로: %s", e)
            return model, False
    return _batched, True


def is_available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def ffmpeg_available():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def extract_audio(mp4_path, output_dir=None):
    """MP4 → WAV (16kHz mono)."""
    if output_dir is None:
        output_dir = os.path.dirname(mp4_path)
    wav_path = os.path.join(output_dir, "audio.wav")
    cmd = [
        "ffmpeg", "-i", mp4_path,
        "-vn", "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
        wav_path, "-y", "-loglevel", "warning",
    ]
    subprocess.run(cmd, check=True, timeout=600)
    return wav_path


def get_audio_duration(audio_path):
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _split_audio(audio_path, chunk_sec=CHUNK_DURATION_SEC):
    duration = get_audio_duration(audio_path)
    if duration <= chunk_sec:
        return [audio_path]

    chunks = []
    output_dir = os.path.dirname(audio_path)
    offset = 0.0
    idx = 0
    while offset < duration:
        chunk_path = os.path.join(output_dir, f"chunk_{idx:03d}.wav")
        cmd = [
            "ffmpeg", "-i", audio_path,
            "-ss", str(offset), "-t", str(chunk_sec),
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            chunk_path, "-y", "-loglevel", "warning",
        ]
        subprocess.run(cmd, check=True, timeout=300)
        chunks.append(chunk_path)
        offset += chunk_sec
        idx += 1
    logger.info(f"Split audio: {len(chunks)} chunks ({duration:.0f}s)")
    return chunks


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe(audio_path, language="ko", initial_prompt=None, hotwords=None):
    """Transcribe → (full_text, segments). Auto-chunks for long audio.

    initial_prompt: 앞선 문맥으로 주입되는 도메인 프롬프트(문체·주제 바이어스).
    hotwords: 용어 단위 바이어싱(약품·검사명 등). 디코딩 시 해당 표기를 선호하게 만든다.
      initial_prompt보다 용어 교정에 직접적이라 용어집은 이쪽을 쓴다(faster-whisper >=1.1).
    둘 다 미지정이면 기존 동작 불변.
    """
    chunks = _split_audio(audio_path)
    engine, batched = _get_engine()

    all_segments = []
    all_text = []
    offset = 0.0
    prompt = (initial_prompt or "").strip()[:MAX_INITIAL_PROMPT_CHARS] or None
    hot = (hotwords or "").strip()[:MAX_INITIAL_PROMPT_CHARS] or None
    extra = {"hotwords": hot} if hot else {}

    if batched:
        extra["batch_size"] = stt_batch_size()

    for chunk_path in chunks:
        segs, info = engine.transcribe(
            chunk_path, language=language, beam_size=5,
            initial_prompt=prompt, **extra,
            word_timestamps=False, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        for seg in segs:
            text = seg.text.strip()
            if text:
                all_segments.append({
                    "start": round(seg.start + offset, 2),
                    "end": round(seg.end + offset, 2),
                    "text": text,
                })
                all_text.append(text)
        offset += CHUNK_DURATION_SEC

    full_text = " ".join(all_text)
    logger.info(f"STT done: {len(all_segments)} segs, {len(full_text)} chars")
    return full_text, all_segments
