"""Meeting Vision — 키프레임 추출 + Vision LLM 슬라이드 분석.

MP4에서 장면 변화 기반 키프레임을 추출하고,
qwen3-vl:8b로 슬라이드 내용을 텍스트로 변환한다.
"""
import base64
import logging
import os
import subprocess

import requests

from core import model_router
from core.model_router import OllamaAdapter

logger = logging.getLogger("ollama-admin")

VISION_MODEL = "qwen3-vl:8b"
MAX_FRAMES = 20  # 최대 분석 프레임 수


def extract_keyframes(mp4_path, output_dir, *, interval_sec=30, scene_detect=True):
    """MP4에서 키프레임 추출

    Args:
        mp4_path: MP4 파일 경로
        output_dir: 프레임 저장 디렉토리
        interval_sec: 기본 추출 간격 (초)
        scene_detect: True면 ffmpeg scene 변화 감지 (슬라이드 전환)

    Returns:
        [{"path": str, "timestamp": float}, ...]
    """
    frames_dir = os.path.join(output_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    if scene_detect:
        # 장면 변화 감지 + 최소 간격 보장
        cmd = [
            "ffmpeg", "-i", mp4_path,
            "-vf", f"select='gt(scene,0.3)',showinfo",
            "-vsync", "vfr",
            "-frame_pts", "1",
            "-q:v", "5",
            os.path.join(frames_dir, "scene_%04d.jpg"),
            "-y", "-loglevel", "warning",
        ]
        try:
            subprocess.run(cmd, check=True, timeout=300, capture_output=True)
            frames = _collect_frames(frames_dir, "scene_")
            if frames:
                # 최소 간격 필터 (너무 빈번한 프레임 제거)
                frames = _filter_min_interval(frames, min_interval=10)
                if len(frames) <= MAX_FRAMES:
                    logger.info(f"Scene detect: {len(frames)} keyframes")
                    return frames[:MAX_FRAMES]
        except Exception as e:
            logger.debug(f"Scene detect fallback to interval: {e}")

    # 고정 간격 추출
    cmd = [
        "ffmpeg", "-i", mp4_path,
        "-vf", f"fps=1/{interval_sec}",
        "-q:v", "5",
        os.path.join(frames_dir, "frame_%04d.jpg"),
        "-y", "-loglevel", "warning",
    ]
    subprocess.run(cmd, check=True, timeout=300)

    frames = []
    idx = 0
    for fname in sorted(os.listdir(frames_dir)):
        if fname.startswith("frame_") and fname.endswith(".jpg"):
            fpath = os.path.join(frames_dir, fname)
            frames.append({
                "path": fpath,
                "timestamp": round(idx * interval_sec, 1),
            })
            idx += 1

    logger.info(f"Interval extract: {len(frames)} keyframes ({interval_sec}s)")
    return frames[:MAX_FRAMES]


def _collect_frames(frames_dir, prefix):
    """디렉토리에서 프레임 파일 수집 + 타임스탬프 추정"""
    frames = []
    for fname in sorted(os.listdir(frames_dir)):
        if fname.startswith(prefix) and fname.endswith(".jpg"):
            fpath = os.path.join(frames_dir, fname)
            # 파일명에서 인덱스 추출
            try:
                idx = int(fname.replace(prefix, "").replace(".jpg", ""))
            except ValueError:
                idx = len(frames)
            frames.append({"path": fpath, "timestamp": float(idx)})
    return frames


def _filter_min_interval(frames, min_interval=10):
    """최소 간격 미만 프레임 필터링"""
    if not frames:
        return frames
    filtered = [frames[0]]
    for f in frames[1:]:
        if f["timestamp"] - filtered[-1]["timestamp"] >= min_interval:
            filtered.append(f)
    return filtered


def analyze_frames(frames, *, ollama_session=None, ollama_base=None, model=None):
    """키프레임을 Vision LLM으로 분석

    Args:
        frames: extract_keyframes() 결과
        ollama_session: requests.Session
        ollama_base: Ollama base URL
        model: Vision 모델명

    Returns:
        [{"timestamp": float, "description": str, "slide_text": str}, ...]
    """
    if not frames:
        return []

    session = ollama_session or requests.Session()
    base = ollama_base or "http://localhost:11434"
    model = model or VISION_MODEL

    results = []
    prev_description = ""

    for frame in frames:
        try:
            # 이미지를 base64로 인코딩
            with open(frame["path"], "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
            # 종전 계약: 전송 예외·HTTP 오류(raise_for_status)=예외 → 아래 except가 프레임을 건너뛴다.
            res = model_router.chat(
                "meeting.vision_frame",
                [
                    {
                        "role": "user",
                        "content": "이 화면을 분석하세요. 슬라이드나 문서가 보이면 텍스트를 추출하고, "
                                   "그렇지 않으면 화면 내용을 간단히 설명하세요. "
                                   "반드시 한국어로 답변하세요. 3줄 이내로 간결하게.",
                        "images": [img_b64],
                    },
                ],
                model=model,
                # ⚠️ qwen3-vl:8b는 think:false를 무시하고 사고(300~3,000자)를 먼저 쓰며 그 토큰이
                # num_predict에서 빠진다. 300이면 사고만 쓰다 잘려 content가 빈 문자열 → 아래 `if content`가
                # 조용히 프레임을 버렸다(200인데 빈 결과 클래스). 2048로 올리고 빈 응답은 로그에 남긴다(v2.125.10).
                think=False,
                options={"temperature": 0.2, "num_predict": 2048},
                timeout=180,
                adapter=OllamaAdapter(base=base, session=session),
            )
            if not res.ok:
                raise RuntimeError(res.error or "vision request failed")
            _j = res.raw
            content = _j.get("message", {}).get("content", "").strip()
            if not content:
                logger.warning("Vision frame %ss: empty content (done_reason=%s, thinking=%d자)",
                               frame["timestamp"], _j.get("done_reason"),
                               len((_j.get("message") or {}).get("thinking") or ""))

            # 이전 프레임과 동일하면 건너뛰기 (중복 슬라이드)
            if content and content != prev_description:
                results.append({
                    "timestamp": frame["timestamp"],
                    "description": content,
                    "slide_text": _extract_slide_text(content),
                })
                prev_description = content

        except Exception as e:
            logger.warning(f"Vision analysis failed for frame {frame['timestamp']}s: {e}")
            continue

    logger.info(f"Vision analysis: {len(results)} unique frames analyzed")
    return results


def _extract_slide_text(description):
    """Vision 설명에서 슬라이드 텍스트 부분 추출 (단순 휴리스틱)"""
    # 따옴표 안의 텍스트 추출
    import re
    quoted = re.findall(r'["\u201c\u201d](.+?)["\u201c\u201d]', description)
    if quoted:
        return " / ".join(quoted)
    return ""
