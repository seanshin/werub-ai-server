"""M6 고비용 엔드포인트 개별 레이트리밋 (v2.125.38).

임계는 7일 journald 실측 피크(generate 5/min·chat 2·rag 3·counseling 3·translate 5)의
6배 이상으로 잡아 현 연동 무영향·단일 키 독식 차단.

불변식:
  1) limiter 미주입(단독 BP 테스트·배치)이면 데코레이터는 no-op — 기존 동작 불변.
  2) 주입되면 실제로 429를 낸다(초과 요청 차단).
  3) M6 대상 라우트 전수에 데코레이터가 붙어 있다(소스 동결 — 누락 재유입 차단).
"""
import re
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from flask import Flask

import core.rate_limit as rl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (파일, 라우트 데코레이터 줄, 요구 리밋) — M6 대상 동결 목록
TARGETS = [
    ("routes/ollama.py", '@ollama_bp.route("/api/generate", methods=["POST"])', "30/minute"),
    ("routes/ollama.py", '@ollama_bp.route("/api/chat", methods=["POST"])', "30/minute"),
    ("routes/heavy.py", '@heavy_bp.route("/api/chat/heavy", methods=["POST"])', "10/minute"),
    ("routes/rag.py", '@rag_bp.route("/api/rag/search", methods=["POST"])', "30/minute"),
    ("routes/rag.py", '@rag_bp.route("/api/rag/ask", methods=["POST"])', "30/minute"),
    ("routes/counseling.py", '@counseling_bp.route("/api/counseling/chat", methods=["POST"])', "20/minute"),
    ("routes/counseling.py", '@counseling_bp.route("/api/counseling/chat/stream", methods=["POST"])', "20/minute"),
    ("routes/writing.py", '@writing_bp.route("/api/translate", methods=["POST"])', "60/minute"),
    ("routes/meeting.py", '@meeting_bp.route("/api/meeting/analyze", methods=["POST"])', "10/minute"),
    ("routes/image.py", '@image_bp.route("/api/image/generate", methods=["POST"])', "30/minute"),
    ("routes/medical_advanced.py",
     '@medical_advanced_bp.route("/api/medical/reasoning/chain", methods=["POST"])', "20/minute"),
    ("routes/medical_advanced.py",
     '@medical_advanced_bp.route("/api/medical/reasoning/chain/stream", methods=["POST"])', "20/minute"),
    ("routes/medical_advanced.py",
     '@medical_advanced_bp.route("/api/medical/reasoning/ensemble", methods=["POST"])', "20/minute"),
    ("routes/medical_advanced.py",
     '@medical_advanced_bp.route("/api/vision/medical/organ-mesh", methods=["POST"])', "6/minute"),
    ("routes/medical_advanced.py",
     '@medical_advanced_bp.route("/api/medical/cardiac-ep", methods=["POST"])', "6/minute"),
]


def test_noop_without_injection(monkeypatch):
    monkeypatch.setattr(rl, "limiter", None)

    @rl.heavy_limit("2/minute")
    def f():
        return "ok"

    assert f() == "ok"
    assert f._heavy_limit == "2/minute"          # 마커는 항상 남는다


def test_enforces_429_when_injected(monkeypatch):
    from flask_limiter import Limiter

    app = Flask(__name__)
    lim = Limiter(app=app, key_func=lambda: "one-key",
                  storage_uri="memory://", strategy="fixed-window")
    monkeypatch.setattr(rl, "limiter", lim)

    @app.route("/t")
    @rl.heavy_limit("2/minute")
    def t():
        return "ok"

    c = app.test_client()
    assert c.get("/t").status_code == 200
    assert c.get("/t").status_code == 200
    assert c.get("/t").status_code == 429, "3번째 요청이 차단돼야 한다"


def test_all_m6_targets_are_decorated():
    """소스 동결 — 라우트 줄 바로 아래에 요구 리밋의 heavy_limit이 있어야 한다."""
    for path, route_line, rate in TARGETS:
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        needle = route_line + "\n" + f'@heavy_limit("{rate}")'
        assert needle in src, f"{path}: {route_line} 아래 heavy_limit({rate}) 누락"


def test_stt_chunk_is_deliberately_unlimited():
    """실시간 STT는 개별 리밋 제외가 **결정**이다(세그먼트 연쇄 계약) — 붙으면 실패."""
    for fn in ("routes/orchestrator.py", "app.py", "routes/ollama.py"):
        p = os.path.join(ROOT, fn)
        if not os.path.exists(p):
            continue
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r'route\("(/api/stt/[^"]+)"[^\n]*\)\n(@heavy_limit)', src):
            raise AssertionError(f"{fn}: {m.group(1)}에 heavy_limit이 붙었다 — 실시간 계약 위반")
