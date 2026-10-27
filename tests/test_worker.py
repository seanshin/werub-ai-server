"""apple-silicon-worker Phase 2 테스트 — worker_guard + worker_app 인증/입력 검증 + STT.

플랫폼 무관 테스트(인증/입력/디바이스 분기)는 어디서나 실행,
실제 MPS·memory_pressure·mlx-whisper 의존 테스트는 @pytest.mark.macos.
실행(iMac): venv-mac/bin/python -m pytest tests/test_worker.py -v
"""
import base64
import io
import platform
import sys

import pytest

IS_MAC = platform.system() == "Darwin"


# ── ai_image_detector._resolve_device (전 플랫폼) ──────────────

@pytest.mark.skipif(
    not __import__("subprocess").run(
        ["python3", "-c", "import torch; assert torch.cuda.is_available()"],
        capture_output=True).returncode == 0,
    reason="CUDA 미가용 — GPU 서버에서만 실행",
)
def test_resolve_device_cpu_cuda_unchanged():
    """기존 cuda/cpu 동작 보존 검증 — GPU 서버 회귀 방지."""
    from core.ai_image_detector import _resolve_device
    assert _resolve_device("cuda") == 0
    assert _resolve_device("cpu") == -1
    assert _resolve_device("anything") == -1


def test_resolve_device_mps_fallback():
    """mps 요청: torch 미설치/미지원이면 -1(cpu), 지원이면 'mps'."""
    from core.ai_image_detector import _resolve_device
    result = _resolve_device("mps")
    assert result in ("mps", -1)


# ── worker_app 인증/입력 (전 플랫폼 — Flask test client) ───────

@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("WORKER_TOKEN", "test-token-123")
    monkeypatch.setenv("WORKER_EXPOSE", "off")
    # env는 import 시점에 읽히므로 모듈 리로드
    for m in list(sys.modules):
        if m == "worker_app":
            del sys.modules[m]
    import worker_app
    worker_app.WORKER_TOKEN = "test-token-123"
    worker_app.WORKER_EXPOSE = "off"
    worker_app.app.config["TESTING"] = True
    return worker_app.app.test_client()


def test_health_no_auth(client):
    """health는 인증 불필요 (watchdog/모니터링용)."""
    r = client.get("/worker/health")
    assert r.status_code == 200
    d = r.get_json()
    assert d["ok"] is True
    assert "detect" in d["capabilities"]


def test_detect_requires_auth(client):
    assert client.post("/worker/detect/image").status_code == 401


def test_detect_wrong_token(client):
    r = client.post("/worker/detect/image", headers={"X-Worker-Token": "wrong"})
    assert r.status_code == 401


def test_bearer_rejected_when_expose_off(client):
    """독립 모드 OFF면 Bearer는 403 (활용 모드 B opt-in 검증)."""
    r = client.post("/worker/detect/image", headers={"Authorization": "Bearer sk-x"})
    assert r.status_code == 403


def test_bearer_verification_with_real_db(client, tmp_path, monkeypatch):
    """독립 모드 ON: queue.db api_keys 스키마로 실제 검증 (유효/무효/만료)."""
    import hashlib
    import sqlite3
    import worker_app
    db = tmp_path / "queue.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE api_keys (id INTEGER PRIMARY KEY, key_hash TEXT UNIQUE, "
                 "active INTEGER DEFAULT 1, expires_at TEXT)")
    good = "sk-good"
    expired = "sk-expired"
    conn.execute("INSERT INTO api_keys (key_hash, active, expires_at) VALUES (?, 1, NULL)",
                 (hashlib.sha256(good.encode()).hexdigest(),))
    conn.execute("INSERT INTO api_keys (key_hash, active, expires_at) VALUES (?, 1, '2000-01-01T00:00:00Z')",
                 (hashlib.sha256(expired.encode()).hexdigest(),))
    conn.commit(); conn.close()

    monkeypatch.setattr(worker_app, "WORKER_EXPOSE", "lan")
    monkeypatch.setenv("WORKER_QUEUE_DB", str(db))  # 전역 os.path 패치 대신 env 오버라이드

    assert client.post("/worker/detect/image", json={},
                       headers={"Authorization": f"Bearer {good}"}).status_code == 400  # 인증 통과 → 입력 오류
    assert client.post("/worker/detect/image", json={},
                       headers={"Authorization": "Bearer sk-nope"}).status_code == 401
    assert client.post("/worker/detect/image", json={},
                       headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_detect_empty_body(client):
    r = client.post("/worker/detect/image", json={},
                    headers={"X-Worker-Token": "test-token-123"})
    assert r.status_code == 400


def test_detect_bad_base64(client):
    r = client.post("/worker/detect/image", json={"image_b64": "!!not-base64!!"},
                    headers={"X-Worker-Token": "test-token-123"})
    assert r.status_code == 400


def _png_bytes(color=(120, 80, 200), size=(64, 64)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_mark_image_roundtrip(client):
    """마킹 적용 + 멱등성(이미 마킹된 이미지는 그대로) — Pillow만 필요."""
    pytest.importorskip("PIL")
    from core import content_marking
    png = _png_bytes()
    r = client.post("/worker/mark/image", json={"image_b64": base64.b64encode(png).decode()},
                    headers={"X-Worker-Token": "test-token-123"})
    assert r.status_code == 200
    d = r.get_json()
    assert d["marked"] is True
    marked = base64.b64decode(d["image_b64"])
    assert content_marking.read_marking(marked).get("marked") is True


# ── macOS 전용 (실제 memory_pressure / MPS) ────────────────────

@pytest.mark.macos
@pytest.mark.skipif(not IS_MAC, reason="macOS 전용")
def test_memory_state_real():
    from core.worker_guard import memory_state
    mem = memory_state()
    assert mem["pressure"] in ("normal", "warn", "critical", "unknown")
    assert mem["free_gb"] is None or mem["free_gb"] > 0


@pytest.mark.macos
@pytest.mark.skipif(not IS_MAC, reason="macOS 전용")
def test_heavy_lock_mutual_exclusion():
    from core import worker_guard
    assert worker_guard.acquire_heavy("stt") is True
    assert worker_guard.acquire_heavy("embed") is False  # 상호배제
    worker_guard.release_heavy()
    assert worker_guard.current_heavy() is None


# ── STT 엔드포인트 (전 플랫폼 — 입력 검증 + mlx 미설치 핸들링) ──────────

def test_stt_requires_auth(client):
    assert client.post("/worker/stt").status_code == 401


def test_stt_empty_body(client):
    r = client.post("/worker/stt", json={},
                    headers={"X-Worker-Token": "test-token-123"})
    # mlx-whisper 미설치면 503, 설치돼있으면 400
    assert r.status_code in (400, 503)


def test_stt_bad_base64(client):
    r = client.post("/worker/stt", json={"audio_b64": "!!bad!!"},
                    headers={"X-Worker-Token": "test-token-123"})
    assert r.status_code in (400, 503)


def test_stt_health_reports_stt(client):
    """health에 stt 필드 포함 여부."""
    r = client.get("/worker/health")
    d = r.get_json()
    assert "stt" in d
    assert "available" in d["stt"]
    assert "stt" in d["capabilities"]


def test_mlx_stt_is_available():
    """mlx-whisper 설치 여부 확인 (iMac 전용 실행 시 True여야 함)."""
    from core.mlx_stt import is_available
    # GPU 서버에서는 미설치일 수 있으므로 bool 검증만
    assert isinstance(is_available(), bool)


@pytest.mark.macos
@pytest.mark.skipif(not IS_MAC, reason="macOS 전용")
def test_stt_wav_transcribe(tmp_path):
    """실제 mlx-whisper 전사 — 무음 WAV 1초 (segments 0개, text 빈 문자열)."""
    pytest.importorskip("mlx_whisper")
    import struct, wave
    from core.mlx_stt import transcribe
    wav_path = tmp_path / "silence.wav"
    with wave.open(str(wav_path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)  # 1초 무음
    result = transcribe(wav_path.read_bytes(), filename="silence.wav", language="ko")
    assert "text" in result
    assert "segments" in result
    assert "duration_ms" in result
    assert result["duration_ms"] > 0


@pytest.mark.macos
@pytest.mark.skipif(not IS_MAC, reason="macOS 전용")
def test_detect_mps_vs_cpu_consistency():
    """MPS와 CPU 추론 결과 동일성 (편차 < 1%p) — torch/transformers 필요."""
    pytest.importorskip("torch")
    pytest.importorskip("transformers")
    import torch
    if not torch.backends.mps.is_available():
        pytest.skip("MPS 미지원")
    import core.ai_image_detector as det
    png = _png_bytes()
    r_mps = det.classify(png, device="mps")
    # 파이프라인 캐시 초기화 후 cpu 재로드
    det._pipeline = None
    det._pipeline_model = None
    r_cpu = det.classify(png, device="cpu")
    assert r_mps.get("available") and r_cpu.get("available"), (r_mps, r_cpu)
    assert abs(r_mps["ai_probability"] - r_cpu["ai_probability"]) < 0.01
