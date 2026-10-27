"""일반 비전 사고형 모델 예산 (v2.125.10) — "빈 응답"의 정체는 비호환이 아니라 사고 예산이었다.

qwen3-vl:8b는 `think:false`를 무시하고 사고(300~3,000자)를 먼저 쓰며 그 토큰이 `num_predict`에서 빠진다.
300으로 부르면 본문 0자(done_reason=length), 2048이면 정상(2026-08-19 실측). 고정하는 전제:
  - qwen3-vl 계열 → num_predict ≥ 2048 · think=False 동반
  - 비사고형(minicpm-v) → 호출자 값 그대로 · think 키 없음
  - VISION_MODEL=qwen3-vl:8b · 폴백=minicpm-v (PoC 사전 등록 5기준 통과 근거는 릴리즈 v2.125.10)
  - meeting_vision 프레임 분석도 같은 예산(같은 클래스 전 경로).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import vision as V  # noqa: E402


def test_thinking_model_gets_budget_and_think_false():
    p = V._vision_payload("qwen3-vl:8b", "설명", ["b64"], 500)
    assert p["options"]["num_predict"] >= 2048 and p["think"] is False
    assert p["model"] == "qwen3-vl:8b" and p["images"] == ["b64"] and p["stream"] is False


def test_thinking_model_keeps_larger_caller_budget():
    assert V._vision_payload("qwen3-vl:8b", "x", [], 4096)["options"]["num_predict"] == 4096


def test_non_thinking_model_untouched():
    p = V._vision_payload("minicpm-v:latest", "설명", ["b64"], 200)
    assert p["options"] == {"num_predict": 200} and "think" not in p


def test_default_and_fallback_models():
    assert V.VISION_MODEL == "qwen3-vl:8b"
    assert V.VISION_FALLBACK_MODEL == "minicpm-v:latest"
    assert V._is_thinking_vision(V.VISION_MODEL) and not V._is_thinking_vision(V.VISION_FALLBACK_MODEL)


def test_meeting_vision_frame_budget(tmp_path):
    from core import meeting_vision as MV
    img = tmp_path / "f.png"; img.write_bytes(b"\x89PNG fake")
    sent = {}

    class _R:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "슬라이드 텍스트"}, "done_reason": "stop"}

    class _S:
        def post(self, url, json=None, timeout=None):
            sent.update(json); sent["_timeout"] = timeout; return _R()

    out = MV.analyze_frames([{"path": str(img), "timestamp": 0}], ollama_session=_S(), ollama_base="http://x")
    assert sent["options"]["num_predict"] >= 2048 and sent["think"] is False and sent["_timeout"] >= 120
    assert out and out[0]["description"] == "슬라이드 텍스트"
