"""SANA 무스왑 fast-lane 회귀 테스트 — route 계층 + 사이드카 순수함수.

route(routes/image.py): engine 검증·ControlNet 게이트/마킹·상호배제(ComfyUI /free)·
settings 토글·status 프록시. 순수(sana_service): _make_control 엣지맵.

실행: venv/bin/python -m pytest tests/test_sana.py -q
"""
import base64
import io
import os
import sqlite3
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


# ── 가짜 requests(사이드카/ComfyUI 프록시 대역) ──────────────────────────────

class _FakeResp:
    def __init__(self, status=200, content=b"", json_data=None):
        self.status_code = status
        self.content = content
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeRequests:
    """URL 패턴별 canned 응답 + 호출 기록. reachable 토글로 예외도 시뮬레이션."""
    def __init__(self):
        self.calls = []
        self.reachable = True
        self.gen_png = _png_bytes(32, 32)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if not self.reachable:
            raise ConnectionError("sidecar down")
        if url.endswith("/free"):
            return _FakeResp(200, json_data={"ok": True})
        if url.endswith("/controlnet"):
            return _FakeResp(200, content=self.gen_png)
        return _FakeResp(200, json_data={"ok": True})

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if not self.reachable:
            raise ConnectionError("sidecar down")
        if url.endswith("/health"):
            return _FakeResp(200, json_data={
                "ok": True, "loaded": True, "mode": "gen",
                "idle_s": 12.3, "vram": {"free_mb": 5200, "total_mb": 16303}})
        return _FakeResp(200, json_data={})


def _png_bytes(w=64, h=64, color=(120, 160, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


# ── route 픽스처: image_bp 단독 등록 + 주입 대역 ─────────────────────────────

@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    import routes.image as img

    db_path = str(tmp_path / "queue.db")
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, batch_id TEXT, model TEXT, prompt TEXT, "
        "priority INTEGER, created_at TEXT, options TEXT, webhook_url TEXT, job_type TEXT, "
        "status TEXT DEFAULT 'pending')")
    con.commit()
    con.close()

    cfg = {"sana_enabled": False, "sana_service_url": "http://127.0.0.1:8189"}

    class _Ev:
        def set(self):
            pass

    meta = {"id": "flux-dev", "name": "Flux Dev", "default_size": (1024, 1024),
            "default_steps": 20, "max_steps": 40}

    img.init_image(
        get_db=lambda: sqlite3.connect(db_path),
        load_config=lambda: cfg,
        save_config=lambda c: cfg.update(c),
        worker_event=_Ev(), image_worker_event=_Ev(),
        validate_webhook_url=lambda u: True,
        image_models_cache={}, get_model_meta=lambda m: meta,
        get_image_dir=lambda: str(tmp_path), get_image_stats=lambda: {},
        cleanup_images=lambda *a, **k: None,
        is_admin_request=lambda: True,
        comfyui_base="http://127.0.0.1:8188", flux_checkpoint="flux.safetensors",
        image_models={"flux.safetensors": meta}, image_style_presets={"none": {}},
        base_dir=str(tmp_path),
    )

    fake = _FakeRequests()
    monkeypatch.setattr(img, "http_requests", fake)

    app = Flask(__name__)
    app.register_blueprint(img.image_bp)
    return type("Ctx", (), {"client": app.test_client(), "cfg": cfg, "fake": fake})()


# ── engine 검증 ──────────────────────────────────────────────────────────

def test_generate_sana_disabled_400(ctx):
    r = ctx.client.post("/api/image/generate", json={"prompt": "cat", "engine": "sana"})
    assert r.status_code == 400
    assert "disabled" in r.get_json()["error"]


def test_generate_unknown_engine_400(ctx):
    r = ctx.client.post("/api/image/generate", json={"prompt": "cat", "engine": "midjourney"})
    assert r.status_code == 400
    assert "unknown engine" in r.get_json()["error"]


def test_generate_sana_enabled_queues_job(ctx):
    ctx.cfg["sana_enabled"] = True
    r = ctx.client.post("/api/image/generate", json={"prompt": "cat", "engine": "sana"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "pending"


def test_generate_comfyui_default_ok(ctx):
    # engine 미지정 = 기존 comfyui 경로(하위호환), sana 비활성이어도 정상 큐잉
    r = ctx.client.post("/api/image/generate", json={"prompt": "cat"})
    assert r.status_code == 200


def test_generate_watermark_false_recorded(ctx):
    import json as _j
    ctx.cfg["sana_enabled"] = True
    r = ctx.client.post("/api/image/generate",
                        json={"prompt": "cat", "engine": "sana", "watermark": False})
    job_id = r.get_json()["job_id"]
    # options에 watermark=False가 기록됐는지 확인(get_db 대역이 같은 파일을 열므로 재조회)
    import routes.image as img
    c = img._get_db()
    row = c.execute("SELECT options FROM jobs WHERE id=?", (job_id,)).fetchone()
    c.close()
    opts = _j.loads(row[0])
    assert opts["engine"] == "sana" and opts["watermark"] is False


# ── ControlNet 게이트 + 마킹 + 상호배제 ──────────────────────────────────────

def test_controlnet_disabled_400(ctx):
    r = ctx.client.post("/api/image/sana/controlnet",
                        json={"prompt": "cat", "control_image": "x"})
    assert r.status_code == 400
    assert "disabled" in r.get_json()["error"]


def test_controlnet_missing_fields_400(ctx):
    ctx.cfg["sana_enabled"] = True
    r = ctx.client.post("/api/image/sana/controlnet", json={"prompt": "cat"})
    assert r.status_code == 400
    assert "required" in r.get_json()["error"]


def test_controlnet_success_marks_png(ctx):
    ctx.cfg["sana_enabled"] = True
    b64 = base64.b64encode(_png_bytes()).decode()
    r = ctx.client.post("/api/image/sana/controlnet",
                        json={"prompt": "cat", "control_image": b64})
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    # 마킹 후에도 유효 PNG (§31 — 반환 전 _apply_marking)
    Image.open(io.BytesIO(r.data)).verify()


def test_controlnet_frees_comfyui_before_sidecar(ctx):
    """상호배제: CN은 사이드카 호출 전 ComfyUI /free를 먼저 친다(마진 ~30MB OOM 방지)."""
    ctx.cfg["sana_enabled"] = True
    b64 = base64.b64encode(_png_bytes()).decode()
    ctx.client.post("/api/image/sana/controlnet",
                    json={"prompt": "cat", "control_image": b64})
    posts = [c[1] for c in ctx.fake.calls if c[0] == "POST"]
    free_i = next(i for i, u in enumerate(posts) if u.endswith("/free"))
    cn_i = next(i for i, u in enumerate(posts) if u.endswith("/controlnet"))
    assert free_i < cn_i  # free가 controlnet보다 먼저


def test_controlnet_sidecar_unreachable_503(ctx):
    ctx.cfg["sana_enabled"] = True
    ctx.fake.reachable = False
    b64 = base64.b64encode(_png_bytes()).decode()
    r = ctx.client.post("/api/image/sana/controlnet",
                        json={"prompt": "cat", "control_image": b64})
    assert r.status_code == 503


# ── settings 토글 ────────────────────────────────────────────────────────

def test_settings_toggle_sana(ctx):
    r = ctx.client.post("/api/image/settings",
                        json={"sana_enabled": True, "sana_service_url": "http://127.0.0.1:9999"})
    s = r.get_json()["settings"]
    assert s["sana_enabled"] is True
    assert s["sana_service_url"] == "http://127.0.0.1:9999"
    assert ctx.cfg["sana_enabled"] is True


# ── status 프록시 ────────────────────────────────────────────────────────

def test_status_reachable(ctx):
    ctx.cfg["sana_enabled"] = True
    r = ctx.client.get("/api/image/sana/status")
    d = r.get_json()
    assert d["enabled"] is True and d["reachable"] is True
    assert d["sidecar"]["mode"] == "gen"
    assert d["sidecar"]["vram"]["free_mb"] == 5200


def test_status_unreachable_graceful(ctx):
    ctx.fake.reachable = False
    r = ctx.client.get("/api/image/sana/status")
    d = r.get_json()
    assert r.status_code == 200
    assert d["reachable"] is False and d["sidecar"] is None


# ── 사이드카 순수함수: _make_control ─────────────────────────────────────────

def test_make_control_edge_map():
    import sana_service as ss
    b64 = base64.b64encode(_png_bytes(500, 700)).decode()
    out = ss._make_control(b64, preprocess=True)
    assert out.mode == "RGB"
    assert out.size == (1024, 1024)  # 비-1024 입력은 리사이즈


def test_make_control_strips_data_prefix():
    import sana_service as ss
    b64 = "data:image/png;base64," + base64.b64encode(_png_bytes()).decode()
    out = ss._make_control(b64, preprocess=False)  # 전처리 없이도 data: 접두 제거
    assert out.size == (1024, 1024)
