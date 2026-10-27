"""Meeting Diarization — SpeechBrain ECAPA-TDNN 기반 화자 분리.

HuggingFace 토큰 불필요. 가속기(CUDA/MPS/CPU)는 core.device로 자동 선택. [[device]]
화자 임베딩 추출 → 스펙트럴 클러스터링 → STT 세그먼트 매핑.
"""
import logging
import os
import tempfile
import time

import numpy as np

logger = logging.getLogger("ollama-admin")

_encoder = None
_device = None  # _get_encoder()에서 resolve된 canonical device("cuda"/"mps"/"cpu")


def is_available():
    """SpeechBrain 화자 분리 사용 가능 여부"""
    try:
        import speechbrain  # noqa: F401
        return True
    except ImportError:
        return False


def _get_encoder():
    """Singleton SpeechBrain ECAPA-TDNN 화자 임베딩 모델.

    가속기는 하드코딩하지 않고 core.device.resolve("auto")로 선택한다
    (Ubuntu→cuda, iMac→mps, 그 외→cpu). 미가용 시 cpu 폴백. [[device]]
    """
    global _encoder, _device
    if _encoder is None:
        from speechbrain.inference.speaker import EncoderClassifier
        from core import device as device_util
        _device = device_util.resolve("auto")
        logger.info("Loading SpeechBrain ECAPA-TDNN speaker encoder (device=%s)...", _device)
        _encoder = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            run_opts={"device": _device},
            savedir=os.path.join(tempfile.gettempdir(), "speechbrain_ecapa"),
        )
        logger.info("SpeechBrain ECAPA-TDNN loaded (device=%s)", _device)
    return _encoder


def diarize(wav_path, *, num_speakers=None, min_speakers=2, max_speakers=10):
    """화자 분리 실행

    Args:
        wav_path: WAV 파일 경로 (16kHz mono)
        num_speakers: 화자 수 힌트 (None=자동)
        min_speakers: 최소 화자 수
        max_speakers: 최대 화자 수

    Returns:
        [{"speaker": "SPEAKER_00", "start": 0.5, "end": 5.3}, ...]
    """
    if not is_available():
        logger.warning("SpeechBrain not available, skipping diarization")
        return []

    t0 = time.time()

    try:
        import torch
        import soundfile as sf
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import pdist

        # WAV 로드 (soundfile — torchaudio 호환 문제 회피)
        audio_np, sr = sf.read(wav_path, dtype="float32")
        if sr != 16000:
            import resampy
            audio_np = resampy.resample(audio_np, sr, 16000)
            sr = 16000

        # 모노로 변환
        if audio_np.ndim > 1:
            audio_np = audio_np.mean(axis=1)

        waveform = torch.from_numpy(audio_np).unsqueeze(0)  # (1, samples)

        duration = waveform.shape[1] / sr
        logger.info(f"Diarize: {duration:.1f}s audio")

        # 윈도우 단위 임베딩 추출 (1.5초 윈도우, 0.75초 스텝)
        window_sec = 1.5
        step_sec = 0.75
        window_samples = int(window_sec * sr)
        step_samples = int(step_sec * sr)

        encoder = _get_encoder()
        embeddings = []
        timestamps = []

        pos = 0
        while pos + window_samples <= waveform.shape[1]:
            chunk = waveform[:, pos:pos + window_samples]
            with torch.no_grad():
                emb = encoder.encode_batch(chunk.to(_device))
                embeddings.append(emb.squeeze().cpu().numpy())
            start_sec = pos / sr
            end_sec = (pos + window_samples) / sr
            timestamps.append((start_sec, end_sec))
            pos += step_samples

        if not embeddings:
            return []

        embeddings = np.array(embeddings)

        # 클러스터링 (Agglomerative)
        if len(embeddings) < 2:
            return [{"speaker": "SPEAKER_00", "start": 0.0, "end": duration}]

        distances = pdist(embeddings, metric="cosine")
        linkage_matrix = linkage(distances, method="ward")

        # 화자 수 결정
        if num_speakers:
            n_clusters = num_speakers
        else:
            # 자동 결정: threshold 기반
            n_clusters = _estimate_num_speakers(
                linkage_matrix, distances,
                min_speakers=min_speakers,
                max_speakers=min(max_speakers, len(embeddings)),
            )

        labels = fcluster(linkage_matrix, t=n_clusters, criterion="maxclust")

        # 세그먼트 생성 (인접한 동일 화자 병합)
        segments = _labels_to_segments(labels, timestamps)

        elapsed = int((time.time() - t0) * 1000)
        n_speakers = len(set(s["speaker"] for s in segments))
        logger.info(f"Diarization done: {n_speakers} speakers, {len(segments)} segments ({elapsed}ms)")
        return segments

    except Exception as e:
        logger.error(f"Diarization failed: {e}", exc_info=True)
        return []


def _estimate_num_speakers(linkage_matrix, distances, min_speakers=2, max_speakers=10):
    """거리 기반 화자 수 자동 추정"""
    from scipy.cluster.hierarchy import fcluster

    best_k = min_speakers
    best_score = -1

    for k in range(min_speakers, min(max_speakers + 1, len(linkage_matrix) + 1)):
        labels = fcluster(linkage_matrix, t=k, criterion="maxclust")
        if len(set(labels)) < k:
            continue

        # 실루엣 스코어 간이 계산
        try:
            from sklearn.metrics import silhouette_score
            if len(set(labels)) >= 2:
                from scipy.spatial.distance import squareform
                dist_matrix = squareform(distances)
                score = silhouette_score(dist_matrix, labels, metric="precomputed")
                if score > best_score:
                    best_score = score
                    best_k = k
        except Exception:
            best_k = min_speakers
            break

    return best_k


def _labels_to_segments(labels, timestamps):
    """클러스터 라벨 → 화자 세그먼트 (인접 병합)"""
    if not labels.any() or not timestamps:
        return []

    segments = []
    current_speaker = f"SPEAKER_{labels[0] - 1:02d}"
    current_start = timestamps[0][0]
    current_end = timestamps[0][1]

    for i in range(1, len(labels)):
        speaker = f"SPEAKER_{labels[i] - 1:02d}"
        if speaker == current_speaker:
            current_end = timestamps[i][1]
        else:
            segments.append({
                "speaker": current_speaker,
                "start": round(current_start, 2),
                "end": round(current_end, 2),
            })
            current_speaker = speaker
            current_start = timestamps[i][0]
            current_end = timestamps[i][1]

    # 마지막 세그먼트
    segments.append({
        "speaker": current_speaker,
        "start": round(current_start, 2),
        "end": round(current_end, 2),
    })

    return segments


def merge_stt_diarization(stt_segments, diarization_segments):
    """STT 세그먼트와 화자 분리 결과를 타임스탬프 기반으로 병합

    각 STT 세그먼트에 가장 많이 겹치는 화자를 할당.

    Args:
        stt_segments: [{"start": float, "end": float, "text": str}, ...]
        diarization_segments: [{"speaker": str, "start": float, "end": float}, ...]

    Returns:
        [{"speaker": str, "start": float, "end": float, "text": str}, ...]
    """
    if not diarization_segments:
        return [{"speaker": "SPEAKER_00", **seg} for seg in stt_segments]

    result = []
    for seg in stt_segments:
        seg_start = seg["start"]
        seg_end = seg["end"]

        # 겹치는 화자별 duration 계산
        speaker_overlap = {}
        for d in diarization_segments:
            overlap_start = max(seg_start, d["start"])
            overlap_end = min(seg_end, d["end"])
            overlap = max(0, overlap_end - overlap_start)
            if overlap > 0:
                speaker_overlap[d["speaker"]] = speaker_overlap.get(d["speaker"], 0) + overlap

        # 가장 많이 겹치는 화자 선택
        if speaker_overlap:
            best_speaker = max(speaker_overlap, key=speaker_overlap.get)
        else:
            best_speaker = "UNKNOWN"

        result.append({
            "speaker": best_speaker,
            "start": seg_start,
            "end": seg_end,
            "text": seg["text"],
        })

    return result
