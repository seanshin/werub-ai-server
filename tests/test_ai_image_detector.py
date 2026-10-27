"""ai_image_detector 단위테스트.

순수 로직(_ai_prob_from_scores, is_available, 빈입력)은 항상 실행.
실제 모델 추론은 transformers/torch 필요 → 없으면 skip (iMac은 skip, GPU에서 실행).
실행: venv/bin/python -m pytest tests/test_ai_image_detector.py -q
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import ai_image_detector as d  # noqa: E402


def test_ai_prob_from_scores_maps_artificial():
    scores = [{"label": "human", "score": 0.2}, {"label": "artificial", "score": 0.8}]
    prob, top = d._ai_prob_from_scores(scores)
    assert abs(prob - 0.8) < 1e-6
    assert top == "human"  # top = 첫 항목(파이프라인 내림차순)


def test_ai_prob_from_scores_no_ai_label():
    scores = [{"label": "human", "score": 0.95}, {"label": "real", "score": 0.05}]
    prob, _ = d._ai_prob_from_scores(scores)
    assert prob == 0.0


def test_ai_prob_clamped():
    scores = [{"label": "ai", "score": 0.7}, {"label": "fake", "score": 0.6}]
    prob, _ = d._ai_prob_from_scores(scores)
    assert prob == 1.0  # 합산 1.3 → 1.0 클램프


def test_classify_empty_bytes():
    out = d.classify(b"")
    assert out["available"] is False
    assert out["ai_probability"] is None


def test_classify_when_transformers_missing_returns_unavailable():
    if d.is_available():
        pytest.skip("transformers 설치됨 — 미설치 분기 테스트 불가")
    out = d.classify(b"\x89PNG\r\n")
    assert out["available"] is False


@pytest.mark.skipif(not d.is_available(), reason="transformers/torch 미설치 (GPU에서 실행)")
def test_classify_real_inference():
    from PIL import Image
    im = Image.new("RGB", (256, 256), (120, 150, 180))
    buf = io.BytesIO(); im.save(buf, "PNG")
    out = d.classify(buf.getvalue(), device="cpu")
    # 모델 가중치/프로세서를 HF 허브에서 받지 못하면(오프라인·레이트리밋·캐시 미스)
    # 환경 사유이므로 실패가 아닌 skip — 모델이 로드될 때만 추론 계약을 검증한다.
    if out.get("available") is not True:
        pytest.skip(f"모델 로드 불가(환경): {out.get('error')}")
    assert 0.0 <= out["ai_probability"] <= 1.0
    assert out["scores"]
