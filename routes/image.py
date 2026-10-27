"""Image API Blueprint — 이미지 생성/조회/관리 + 캐릭터 일관성"""
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import requests as http_requests
from flask import Blueprint, request, jsonify, Response, send_file
from core.rate_limit import heavy_limit
from core.errors import internal_error

logger = logging.getLogger("ollama-admin")

image_bp = Blueprint("image", __name__)

# app.py에서 init_image()로 주입
_get_db = None
_load_config = None
_save_config = None
_worker_event = None
_image_worker_event = None
_validate_webhook_url = None
_image_models_cache = None
_get_model_meta = None
_get_image_dir = None
_get_image_stats = None
_cleanup_images = None
_is_admin_request = None
_COMFYUI_BASE = None
_FLUX_CHECKPOINT = None
_IMAGE_MODELS = None
_IMAGE_STYLE_PRESETS = None
_BASE_DIR = None

# Character directories
CHARACTERS_DIR = None
COMFYUI_INPUT_CHARS = "/home/deploy/ComfyUI/input/characters"


def init_image(*, get_db, load_config, save_config, worker_event, image_worker_event,
               validate_webhook_url, image_models_cache, get_model_meta,
               get_image_dir, get_image_stats, cleanup_images,
               is_admin_request, comfyui_base, flux_checkpoint,
               image_models, image_style_presets, base_dir):
    global _get_db, _load_config, _save_config, _worker_event, _image_worker_event
    global _validate_webhook_url, _image_models_cache, _get_model_meta
    global _get_image_dir, _get_image_stats, _cleanup_images
    global _is_admin_request, _COMFYUI_BASE, _FLUX_CHECKPOINT
    global _IMAGE_MODELS, _IMAGE_STYLE_PRESETS, _BASE_DIR, CHARACTERS_DIR
    _get_db = get_db
    _load_config = load_config
    _save_config = save_config
    _worker_event = worker_event
    _image_worker_event = image_worker_event
    _validate_webhook_url = validate_webhook_url
    _image_models_cache = image_models_cache
    _get_model_meta = get_model_meta
    _get_image_dir = get_image_dir
    _get_image_stats = get_image_stats
    _cleanup_images = cleanup_images
    _is_admin_request = is_admin_request
    _COMFYUI_BASE = comfyui_base
    _FLUX_CHECKPOINT = flux_checkpoint
    _IMAGE_MODELS = image_models
    _IMAGE_STYLE_PRESETS = image_style_presets
    _BASE_DIR = base_dir
    CHARACTERS_DIR = os.path.join(base_dir, "characters")


def _marking_cfg():
    """AI 생성물 표시(마킹) 설정. config.json 키로 제어, 기본 ON(법적 의무)."""
    cfg = _load_config() if _load_config else {}
    return {
        "enabled": cfg.get("ai_marking_enabled", True),
        "visible": cfg.get("ai_marking_visible", True),
        "metadata": cfg.get("ai_marking_metadata", True),
        "label": cfg.get("ai_marking_label") or None,
        "position": cfg.get("ai_marking_position", "br"),
        "opacity": cfg.get("ai_marking_opacity", 0.85),
        "font_scale": cfg.get("ai_marking_font_scale", 0.022),
    }


def _free_comfyui_vram_for_sana():
    """SANA(gen/CN) co-resident 실행 전 ComfyUI VRAM 반납 — 상호배제(best-effort).

    CN co-resident 마진이 극소(~30MB)라 ComfyUI가 on-demand로 상주 중이면 SANA CN이 OOM.
    ComfyUI /free(unload_models)로 모델 캐시만 비운다(프로세스 유지). ComfyUI 미실행 시 no-op.
    ComfyUI 전 SANA 언로드(_unload_sana_sidecar)와 짝이 되는 반대 방향 훅.
    """
    try:
        http_requests.post(f"{_COMFYUI_BASE}/free",
                           json={"unload_models": True, "free_memory": True}, timeout=5)
    except Exception:
        pass


def _apply_marking(img_bytes, model=None, visible_override=None):
    """이미지 바이트에 AI 생성 마킹 적용. 실패/비활성 시 원본 반환.

    순서: 가시 워터마크 + PNG 메타(content_marking) → C2PA 서명(c2pa_sign, opt-in).
    C2PA를 마지막에 해야 매니페스트가 최종 마킹본을 해시·서명함.

    visible_override: 요청별 가시 워터마크 토글. False면 가시 워터마크는 생략하되
    기계판독용 PNG 메타데이터/C2PA는 유지(AI 기본법 §31 투명성 의무). None이면 config 기본값.
    """
    mc = _marking_cfg()
    if not mc["enabled"]:
        return img_bytes
    visible = mc["visible"] if visible_override is None else bool(visible_override)
    try:
        from core.content_marking import mark_image_bytes
        img_bytes = mark_image_bytes(
            img_bytes, generator="WeRU.B AI (ComfyUI)", model=model,
            label=mc["label"], position=mc["position"], opacity=mc["opacity"],
            font_scale=mc["font_scale"], visible=visible, metadata=mc["metadata"],
        )
    except Exception as e:
        logger.warning("AI marking failed (passthrough): %s", e)

    # C2PA Content Credentials 서명 (opt-in, 인증서 있을 때만)
    cfg = _load_config() if _load_config else {}
    if cfg.get("ai_marking_c2pa", False):
        try:
            from core.c2pa_sign import sign_image_bytes
            img_bytes = sign_image_bytes(
                img_bytes,
                cert_path=cfg.get("c2pa_cert_path", ""),
                key_path=cfg.get("c2pa_key_path", ""),
                generator="WeRU.B AI (ComfyUI)", model=model,
            )
        except Exception as e:
            logger.warning("C2PA sign failed (passthrough): %s", e)
    return img_bytes


def _ensure_character_dirs():
    os.makedirs(CHARACTERS_DIR, exist_ok=True)
    os.makedirs(COMFYUI_INPUT_CHARS, exist_ok=True)


def _get_characters_db():
    return _get_db()


@image_bp.route("/api/image/generate", methods=["POST"])
@heavy_limit("30/minute")
def api_image_generate():
    """Generate image via queue -> ComfyUI
    ---
    tags: [Image]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt]
          properties:
            prompt:          {type: string, description: "텍스트 프롬프트"}
            model:           {type: string, description: "체크포인트명 또는 모델 ID (기본: flux-schnell)"}
            style:           {type: string, description: "스타일 프리셋", enum: [none, photorealistic, illustration, anime, oil-painting, concept-art, watercolor, pixel-art, pixel-art-character, pixel-art-sprite, pixel-art-scene, logo, high-detail, lcm-fast, flux-realism]}
            negative_prompt: {type: string, description: "제외할 요소 (SDXL 전용)"}
            width:           {type: integer, default: 1024}
            height:          {type: integer, default: 1024}
            steps:           {type: integer}
            cfg:             {type: number, default: 7.0}
            seed:            {type: integer}
            character_id:    {type: string}
            ip_adapter_strength: {type: number, default: 0.7}
            ip_adapter_mode: {type: string, default: "full"}
            lcm_mode:        {type: boolean, default: false}
            priority:        {type: integer, default: 5}
            auto_resize:     {type: boolean, default: false}
            no_cache:        {type: boolean, default: false}
            watermark:       {type: boolean, default: true, description: "가시 'AI 생성' 워터마크 표시 여부. false면 가시 워터마크 생략(기계판독 메타데이터는 유지)"}
            webhook_url:     {type: string}
    responses:
      200:
        description: 큐에 등록됨
        schema:
          type: object
          properties:
            job_id:   {type: string}
            status:   {type: string}
            position: {type: integer}
            model:    {type: string}
            style:    {type: string}
      400:
        description: prompt 누락
    """
    data = request.json or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "prompt is required"}), 400

    model_input = data.get("model", "")
    model = _FLUX_CHECKPOINT
    if model_input:
        # 1) exact id match
        for ckpt, meta in _IMAGE_MODELS.items():
            if meta["id"] == model_input:
                model = ckpt
                break
        else:
            # 2) exact checkpoint filename match
            if model_input in _IMAGE_MODELS:
                model = model_input
            else:
                # 3) fuzzy match: normalize input and compare against id/ckpt
                norm = model_input.lower().replace("_", "-").replace(" ", "-")
                matched = False
                for ckpt, meta in _IMAGE_MODELS.items():
                    ckpt_norm = ckpt.lower().replace("_", "-")
                    mid = meta["id"].lower().replace("_", "-")
                    if norm in (mid, ckpt_norm) or ckpt_norm.startswith(norm) or mid.startswith(norm):
                        model = ckpt
                        matched = True
                        break
                if not matched:
                    available = [m["id"] for m in _IMAGE_MODELS.values()]
                    return jsonify({"error": f"unknown model '{model_input}'", "available": available}), 400

    style = data.get("style", "none")
    if style and style not in _IMAGE_STYLE_PRESETS:
        return jsonify({"error": f"unknown style '{style}'. available: {list(_IMAGE_STYLE_PRESETS.keys())}"}), 400

    if data.get("auto_resize", False) or "Mobile" in request.headers.get("User-Agent", ""):
        if "width" not in data and "height" not in data:
            style_preset = _IMAGE_STYLE_PRESETS.get(style, {})
            if not style_preset.get("default_size"):
                data["width"] = 768
                data["height"] = 768

    character_id = data.get("character_id")
    if character_id:
        conn_ch = _get_db()
        try:
            ch_row = conn_ch.execute("SELECT id FROM characters WHERE id=?", (character_id,)).fetchone()
        finally:
            conn_ch.close()
        if not ch_row:
            return jsonify({"error": f"character not found: {character_id}"}), 404

    opts = {k: data[k] for k in ("width", "height", "steps", "seed", "style", "negative_prompt", "cfg",
                                    "character_id", "ip_adapter_strength", "ip_adapter_mode") if k in data}
    # SANA 무스왑 fast-lane 엔진 선택 (기본 comfyui). sana_enabled=true일 때만 허용.
    engine = data.get("engine")
    if engine == "sana":
        if not (_load_config() or {}).get("sana_enabled", False):
            return jsonify({"error": "sana engine is disabled (set sana_enabled=true)"}), 400
        opts["engine"] = "sana"
    elif engine and engine != "comfyui":
        return jsonify({"error": f"unknown engine '{engine}' (use 'comfyui' or 'sana')"}), 400
    # 가시 워터마크 요청별 토글. False면 가시 표시 생략(메타데이터 마킹은 유지=§31 충족).
    if data.get("watermark") is False:
        opts["watermark"] = False
    webhook = data.get("webhook_url")
    if webhook and not _validate_webhook_url(webhook):
        return jsonify({"error": "invalid webhook_url (must be public http/https)"}), 400

    conn = _get_db()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        if pending >= 10000:
            return jsonify({"error": "queue full (10,000 pending jobs)"}), 429

        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat() + "Z"
        conn.execute(
            "INSERT INTO jobs (id, batch_id, model, prompt, priority, created_at, options, webhook_url, job_type) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, None, model, prompt, data.get("priority", 5), now,
             json.dumps(opts) if opts else None, webhook, "image"),
        )
        position = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    _worker_event.set()
    _image_worker_event.set()
    meta = _get_model_meta(model)
    return jsonify({
        "job_id": job_id, "status": "pending", "position": position,
        "model": meta["id"], "model_name": meta["name"], "style": style,
    })


@image_bp.route("/api/image/sana/controlnet", methods=["POST"])
def api_sana_controlnet():
    """SANA ControlNet 구조 제어 생성 (동기) — control_image 윤곽대로 프롬프트 렌더링.
    ---
    tags: [Image]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [prompt, control_image]
          properties:
            prompt:        {type: string}
            control_image: {type: string, description: "base64 PNG/JPEG (data: 접두 허용)"}
            preprocess:    {type: boolean, default: true, description: "true면 PIL 엣지맵 추출, false면 이미 엣지맵"}
            negative:      {type: string}
            steps:         {type: integer, default: 20}
            seed:          {type: integer, default: 0}
            controlnet_conditioning_scale: {type: number, default: 1.0}
            watermark:     {type: boolean, default: true}
    responses:
      200: {description: PNG image}
      400: {description: 입력 오류/비활성}
      502: {description: 사이드카 오류}
    """
    if not (_load_config() or {}).get("sana_enabled", False):
        return jsonify({"error": "sana engine is disabled (set sana_enabled=true)"}), 400
    data = request.json or {}
    prompt = (data.get("prompt") or "").strip()
    control = data.get("control_image")
    if not prompt or not control:
        return jsonify({"error": "prompt and control_image are required"}), 400
    # 상호배제: ComfyUI가 상주 중이면 VRAM 반납(CN 마진 ~30MB → 동시상주 OOM 방지)
    _free_comfyui_vram_for_sana()
    base = (_load_config() or {}).get("sana_service_url", "http://127.0.0.1:8189")
    payload = {k: data[k] for k in ("prompt", "control_image", "negative", "steps", "seed",
                                    "controlnet_conditioning_scale", "preprocess") if k in data}
    try:
        r = http_requests.post(f"{base}/controlnet", json=payload, timeout=180)
    except Exception as e:
        return jsonify({"error": f"sana sidecar unreachable: {str(e)[:150]}"}), 503
    if r.status_code != 200:
        return jsonify({"error": f"sana controlnet error({r.status_code})"}), 502
    # AI 생성물 표시 의무(§31) — 반환 전 마킹
    visible_override = False if data.get("watermark") is False else None
    body = _apply_marking(r.content, model="sana-controlnet", visible_override=visible_override)
    return Response(body, mimetype="image/png")


@image_bp.route("/api/image/sana/status")
def api_sana_status():
    """SANA 무스왑 fast-lane 상태 — config 토글 + 사이드카 health 프록시.
    ---
    tags: [Image]
    responses:
      200:
        schema:
          type: object
          properties:
            enabled:      {type: boolean, description: sana_enabled config 토글}
            service_url:  {type: string}
            reachable:    {type: boolean, description: 사이드카 도달 여부}
            sidecar:      {type: object, description: "사이드카 /health (loaded/mode/vram/idle_s)"}
    """
    cfg = _load_config() or {}
    base = cfg.get("sana_service_url", "http://127.0.0.1:8189")
    out = {
        "enabled": bool(cfg.get("sana_enabled", False)),
        "service_url": base,
        "reachable": False,
        "sidecar": None,
    }
    try:
        r = http_requests.get(f"{base}/health", timeout=3)
        if r.status_code == 200:
            out["reachable"] = True
            out["sidecar"] = r.json()
    except Exception:
        pass
    return jsonify(out)


@image_bp.route("/api/image/status/<job_or_prompt_id>")
def api_image_status(job_or_prompt_id):
    """Check image generation status
    ---
    tags: [Image]
    security: [{Bearer: []}]
    parameters:
      - {name: job_or_prompt_id, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            status:   {type: string, enum: [pending, running, completed, failed]}
            position: {type: integer}
            images:   {type: array}
    """
    conn = _get_db()
    try:
        row = conn.execute("SELECT status, response, error FROM jobs WHERE id=?", (job_or_prompt_id,)).fetchone()
    finally:
        conn.close()
    if row:
        status, response, error = row
        if status == "completed" and response:
            try:
                resp = json.loads(response)
                images = []
                fn = resp.get("filename")
                if fn:
                    img = {"filename": fn, "subfolder": resp.get("subfolder", "")}
                    # S3 자동 업로드 (v2.70.0) — 아직 s3_key가 없으면 업로드 시도
                    s3_url = resp.get("s3_url")
                    s3_key = resp.get("s3_key")
                    if not s3_url:
                        s3_url, s3_key = _auto_s3_upload(fn, resp.get("subfolder", ""), job_or_prompt_id)
                    if s3_url:
                        img["s3_url"] = s3_url
                        img["s3_key"] = s3_key
                    images = [img]
                return jsonify({"status": "completed", "images": images})
            except (json.JSONDecodeError, TypeError):
                return jsonify({"status": "completed", "images": []})
        elif status == "failed":
            return jsonify({"status": "failed", "error": error or "unknown error"})
        elif status == "pending":
            conn2 = _get_db()
            try:
                created = conn2.execute("SELECT created_at FROM jobs WHERE id=?", (job_or_prompt_id,)).fetchone()
                pos = 0
                queue_pending = 0
                if created:
                    pos = conn2.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status='pending' AND created_at < ?",
                        (created[0],)
                    ).fetchone()[0] + 1
                    queue_pending = conn2.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            finally:
                conn2.close()
            return jsonify({"status": "pending", "position": pos, "queue_pending": queue_pending})
        elif status == "running":
            conn2 = _get_db()
            try:
                queue_pending = conn2.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
            finally:
                conn2.close()
            return jsonify({"status": "running", "position": 0, "queue_pending": queue_pending})

    # ComfyUI prompt_id fallback
    prompt_id = job_or_prompt_id
    try:
        q = http_requests.get(f"{_COMFYUI_BASE}/queue", timeout=5).json()
        running_ids = [item[1] for item in q.get("queue_running", [])]
        pending_ids = [item[1] for item in q.get("queue_pending", [])]
        if prompt_id in running_ids:
            return jsonify({"status": "running"})
        if prompt_id in pending_ids:
            return jsonify({"status": "pending"})

        h = http_requests.get(f"{_COMFYUI_BASE}/history/{prompt_id}", timeout=5).json()
        if prompt_id not in h:
            return jsonify({"status": "pending"})
        outputs = h[prompt_id].get("outputs", {})
        for node_out in outputs.values():
            images = node_out.get("images", [])
            if images:
                filename = images[0]["filename"]
                subfolder = images[0].get("subfolder", "")
                return jsonify({"status": "done", "filename": filename, "subfolder": subfolder})
        return jsonify({"status": "error", "error": "출력 이미지 없음"})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)})


def _auto_s3_upload(filename, subfolder, job_id):
    """ComfyUI 이미지를 S3에 자동 업로드하고 DB에 s3_url 저장. (s3_url, s3_key) 반환."""
    from core.s3_storage import is_enabled, upload_bytes, get_presigned_url
    if not is_enabled():
        return None, None
    try:
        # SANA(subfolder='sana')는 sana_out 디스크에서, ComfyUI는 /view에서 원본 바이트 취득
        is_sana = (subfolder == "sana")
        if is_sana:
            sana_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "sana_out", filename)
            if not os.path.exists(sana_path):
                return None, None
            with open(sana_path, "rb") as _f:
                raw = _f.read()
            raw_ct = "image/png"
        else:
            r = http_requests.get(f"{_COMFYUI_BASE}/view",
                             params={"filename": filename, "subfolder": subfolder, "type": "output"},
                             timeout=30)
            if r.status_code != 200:
                return None, None
            raw = r.content
            raw_ct = r.headers.get("Content-Type", "image/png")

        # 요청별 가시 워터마크 토글 (job.options.watermark=false → 가시 생략, 메타는 유지)
        visible_override = None
        try:
            jrow = _get_db()
            try:
                orow = jrow.execute("SELECT options FROM jobs WHERE id=?", (job_id,)).fetchone()
            finally:
                jrow.close()
            if orow and orow[0]:
                import json as _json
                jopts = _json.loads(orow[0])
                if jopts.get("watermark") is False:
                    visible_override = False
        except Exception:
            pass

        # AI 생성물 표시 의무(AI 기본법 §31): 업로드 전 마킹 적용
        img_bytes = _apply_marking(raw, model=("sana" if is_sana else None), visible_override=visible_override)
        # 마킹은 PNG로 정규화되므로 content_type 동기화
        marked = img_bytes is not raw
        content_type = "image/png" if marked else raw_ct
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        s3_filename = f"{ts}_{filename}"

        s3_meta = {
            "source": "sana" if is_sana else "comfyui",
            "job_id": str(job_id),
            "original_filename": filename,
        }
        if marked:
            s3_meta["ai_generated"] = "true"
        result = upload_bytes(img_bytes, s3_filename, content_type, metadata=s3_meta)
        if "error" in result:
            return None, None

        s3_url = result["url"]
        s3_key = result["key"]

        # DB에 s3_url/s3_key 저장 (다음 조회 시 재업로드 방지)
        try:
            conn = _get_db()
            row = conn.execute("SELECT response FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row and row[0]:
                import json as _json
                resp = _json.loads(row[0])
                resp["s3_url"] = s3_url
                resp["s3_key"] = s3_key
                conn.execute("UPDATE jobs SET response=? WHERE id=?",
                             (_json.dumps(resp, ensure_ascii=False), job_id))
                conn.commit()
            conn.close()
        except Exception:
            pass

        logger.info("S3 auto-upload: %s → %s", filename, s3_key)
        return s3_url, s3_key
    except Exception as e:
        logger.warning("S3 auto-upload failed: %s", e)
        return None, None


def _caller_tag():
    """404 로그용 호출자 식별 — 구 이미지 참조를 쥔 클라이언트를 찾기 위해(v2.125.70).
    2026-10-07: api_activity 창이 짧아 09-28~10-03 요청의 키 이름이 이미 사라져 있었다 — 그래서 로그에 남긴다.
    키 원문이 아니라 이름(관리 콘솔 발급명)·UA 앞부분만."""
    from flask import g
    return "key=%s ua=%s" % (getattr(g, "act_key_name", None) or "?",
                             (request.headers.get("User-Agent", "") or "")[:40])


@image_bp.route("/api/image/view/<path:filename>")
def api_image_view(filename):
    """Proxy image from ComfyUI output (S3 있으면 리다이렉트)
    ---
    tags: [Image]
    security: [{Bearer: []}]
    parameters:
      - {name: filename, in: path, type: string, required: true}
      - {name: subfolder, in: query, type: string}
    responses:
      200:
        description: PNG image
      302:
        description: Redirect to S3 presigned URL
    """
    from core.s3_storage import is_enabled, find_image_key, get_presigned_url
    subfolder = request.args.get("subfolder", "")

    # 🔴 경로 순회 봉합(v2.125.17) — `<path:filename>`은 `../`를 **정규화하지 않는다**.
    #    2026-08-25 라이브 재현: `/api/image/view/../.secret_key?subfolder=sana` → 200 + Flask
    #    SECRET_KEY 32바이트(→ 세션 위조 → 관리자 판정 통과). 형제 경로 4곳(`image.py:753`·
    #    `training.py:1902/1920/1938`)은 이미 `basename`을 쓰고 있었고 **여기만 빠져 있었다**
    #    (같은 클래스 전 경로 쓸기 누락). subfolder도 경로 조립에 쓰이므로 함께 고정한다.
    filename = os.path.basename(filename)
    if subfolder and (os.path.sep in subfolder or ".." in subfolder):
        return jsonify({"error": "invalid subfolder"}), 400

    # S3에 해당 이미지가 있으면 presigned URL로 리다이렉트
    # v2.125.70: 파일명 **정확 일치·최신 키·페이지네이션**(core.s3_storage.find_image_key 주석 — 종전 부분 일치·
    #   앞 200키는 카운터 리셋 뒤 엉뚱한 이미지로 갈 수 있었다). 조회 실패는 로그를 남기고 디스크/ComfyUI로 폴백.
    if is_enabled():
        key, err = find_image_key(filename)
        if key:
            from flask import redirect
            return redirect(get_presigned_url(key))
        if err:
            logger.warning("[image/view] S3 조회 실패 → 폴백: %s (%s)", filename, err)

    # SANA(subfolder='sana' 또는 sana_ 파일)는 sana_out 디스크에서 서빙 (마킹 적용)
    if subfolder == "sana" or filename.startswith("sana_"):
        sana_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "sana_out", filename)
        if os.path.exists(sana_path):
            with open(sana_path, "rb") as _f:
                body = _apply_marking(_f.read(), model="sana")
            return Response(body, mimetype="image/png")
        return jsonify({"error": "image not found"}), 404

    # ComfyUI 프록시 — S3 미사용/미스 시. 서빙 바이트에도 마킹 적용.
    # 🔴 v2.125.70 이전: ①ComfyUI는 on-demand(이미지 생성 때만 기동 — 09-09 이후 상시 inactive)라 연결이 거부되면
    #    `except` → **503 + 로그 0줄**이었다. 2026-09-28~10-03 node 클라이언트의 구 이미지 요청 3건이 전부 이 503 —
    #    그 파일은 S3(Wasabi 폐지로 소실)에도 디스크에도 없었다 = 맞는 답은 404.
    #    ②ComfyUI가 떠 있어도 **상태 코드를 안 봐서** 404 본문을 200으로 낼 수 있었다.
    # ⚠️ 이미지 조회 때문에 ComfyUI를 기동하지 않는다(16GB VRAM — 상주 14b 축출). 꺼져 있으면 디스크를 직접 본다.
    try:
        r = http_requests.get(f"{_COMFYUI_BASE}/view",
                         params={"filename": filename, "subfolder": subfolder, "type": "output"},
                         timeout=30, stream=True)
    except http_requests.exceptions.RequestException as e:
        img_dir = _get_image_dir() if _get_image_dir else None
        disk = os.path.join(img_dir, subfolder, filename) if img_dir else None
        if disk and os.path.isfile(disk):
            with open(disk, "rb") as _f:
                body = _apply_marking(_f.read(), model=None)
            return Response(body, mimetype="image/png")
        logger.info("[image/view] 없음(S3·디스크 미스, ComfyUI 미기동) → 404: %s %s (%s)",
                    filename, _caller_tag(), type(e).__name__)
        return jsonify({"error": "image not found"}), 404
    if r.status_code == 404:
        logger.info("[image/view] 없음(ComfyUI 404) → 404: %s %s", filename, _caller_tag())
        return jsonify({"error": "image not found"}), 404
    if r.status_code != 200:
        logger.warning("[image/view] ComfyUI %s → 502: %s", r.status_code, filename)
        return jsonify({"error": f"ComfyUI fetch failed: {r.status_code}"}), 502
    body = _apply_marking(r.content, model=None)
    mime = "image/png" if body is not r.content else r.headers.get("Content-Type", "image/png")
    return Response(body, mimetype=mime)


@image_bp.route("/api/image/models")
def api_image_models():
    """List available image models, styles, LoRAs
    ---
    tags: [Image]
    security: [{Bearer: []}]
    responses:
      200:
        description: "모델, 스타일, LoRA 정보"
    """
    cached_models = _image_models_cache.get("models")
    if cached_models:
        return jsonify(cached_models)

    try:
        r = http_requests.get(f"{_COMFYUI_BASE}/object_info/CheckpointLoaderSimple", timeout=5).json()
        installed = r["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]

        models = []
        for ckpt in installed:
            meta = _get_model_meta(ckpt)
            models.append({
                "checkpoint": ckpt,
                "id": meta["id"],
                "name": meta["name"],
                "family": meta.get("family", "unknown"),
                "description": meta.get("description", ""),
                "quality": meta.get("quality", "standard"),
                "speed": meta.get("speed", "normal"),
                "default_steps": meta.get("default_steps", 20),
                "max_steps": meta.get("max_steps", 50),
                "default_size": meta.get("default_size", [1024, 1024]),
                "capabilities": meta.get("capabilities", ["text-to-image"]),
            })

        styles = {}
        for k, v in _IMAGE_STYLE_PRESETS.items():
            style_info = {"description": v["description"]}
            if v.get("lora"):
                style_info["lora"] = v["lora"]
                style_info["lora_strength"] = v.get("lora_strength", 0.8)
            if v.get("lcm_mode"):
                style_info["lcm_mode"] = True
                style_info["recommended_steps"] = "4-8"
                style_info["recommended_cfg"] = 1.8
            if v.get("default_size"):
                style_info["default_size"] = v["default_size"]
            if v.get("negative"):
                style_info["has_negative"] = True
            styles[k] = style_info

        result = {"models": models, "styles": styles, "online": True}
        _image_models_cache.set("models", result)
        return jsonify(result)
    except Exception:
        return jsonify({"models": [], "styles": {}, "online": False})


@image_bp.route("/api/image/storage")
def api_image_storage():
    """Get image storage statistics
    ---
    tags: [Image]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            stats: {type: object}
            settings: {type: object}
    """
    stats = _get_image_stats()
    stats.pop("files", None)
    cfg = _load_config()
    return jsonify({
        "stats": stats,
        "settings": {
            "image_retention_days": cfg.get("image_retention_days", 7),
            "image_max_count": cfg.get("image_max_count", 500),
            "image_max_size_mb": cfg.get("image_max_size_mb", 2000),
            "auto_purge_days": cfg.get("auto_purge_days", 7),
        }
    })


@image_bp.route("/api/image/cleanup", methods=["POST"])
def api_image_cleanup():
    """Cleanup old images manually
    ---
    tags: [Image]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            retention_days:  {type: integer}
            max_count:       {type: integer}
            max_size_mb:     {type: integer}
    responses:
      200:
        schema:
          type: object
          properties:
            deleted: {type: integer}
            freed_mb: {type: number}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    data = request.json or {}
    result = _cleanup_images(
        force_days=data.get("retention_days"),
        force_max_count=data.get("max_count"),
        force_max_size_mb=data.get("max_size_mb"),
    )
    return jsonify(result)


@image_bp.route("/api/image/gallery")
def api_image_gallery():
    """Image gallery with file list
    ---
    tags: [Image]
    security: [{Bearer: []}]
    responses:
      200:
        schema: {type: object}
    """
    stats = _get_image_stats()
    return jsonify(stats)


@image_bp.route("/api/image/delete/<path:filename>", methods=["DELETE"])
def api_image_delete(filename):
    """Delete a generated image
    ---
    tags: [Image]
    security: [{Bearer: []}]
    parameters:
      - {name: filename, in: path, type: string, required: true}
    responses:
      200:
        schema: {type: object}
    """
    img_dir = _get_image_dir()
    if not img_dir:
        return jsonify({"error": "이미지 디렉토리 없음"}), 404
    safe = os.path.basename(filename)
    path = os.path.join(img_dir, safe)
    if not os.path.exists(path):
        return jsonify({"error": "파일 없음"}), 404
    os.remove(path)
    return jsonify({"ok": True, "deleted": safe})


@image_bp.route("/api/image/settings", methods=["POST"])
def api_image_settings():
    """Update image storage settings
    ---
    tags: [Image]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            image_retention_days: {type: integer}
            image_max_count:      {type: integer}
            image_max_size_mb:    {type: integer}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            settings: {type: object}
    """
    if not _is_admin_request():
        return jsonify({"error": "unauthorized"}), 403
    data = request.json or {}
    cfg = _load_config()
    for k in ("image_retention_days", "image_max_count", "image_max_size_mb"):
        if k in data:
            cfg[k] = int(data[k])
    # SANA 무스왑 fast-lane 토글
    if "sana_enabled" in data:
        cfg["sana_enabled"] = bool(data["sana_enabled"])
    if "sana_service_url" in data:
        cfg["sana_service_url"] = str(data["sana_service_url"])
    _save_config(cfg)
    return jsonify({
        "ok": True,
        "settings": {
            "image_retention_days": cfg.get("image_retention_days", 7),
            "image_max_count": cfg.get("image_max_count", 500),
            "image_max_size_mb": cfg.get("image_max_size_mb", 2000),
            "sana_enabled": cfg.get("sana_enabled", False),
            "sana_service_url": cfg.get("sana_service_url", "http://127.0.0.1:8189"),
        }
    })


# ---------------------------------------------------------------------------
# Character Consistency System
# ---------------------------------------------------------------------------

@image_bp.route("/api/image/character/create", methods=["POST"])
def api_character_create():
    """Register a character with reference image
    ---
    tags: [Character]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - {name: image, in: formData, type: file, required: true}
      - {name: name, in: formData, type: string, required: true}
      - {name: description, in: formData, type: string}
      - {name: tags, in: formData, type: string}
    responses:
      200:
        description: 캐릭터 등록 성공
        schema:
          type: object
          properties:
            character_id: {}
            name: {}
            reference_image: {}
            thumbnail: {}
            tags: {}
      400:
        description: 필수 파라미터 누락
    """
    _ensure_character_dirs()

    if "image" not in request.files:
        return jsonify({"error": "image file is required"}), 400
    file = request.files["image"]
    name = request.form.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    description = request.form.get("description", "").strip()
    tags_raw = request.form.get("tags", "")

    try:
        tags = json.loads(tags_raw) if tags_raw.startswith("[") else [t.strip() for t in tags_raw.split(",") if t.strip()]
    except (json.JSONDecodeError, TypeError):
        tags = []

    char_id = str(uuid.uuid4())
    ext = os.path.splitext(file.filename)[1].lower() if file.filename else ".png"
    _ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
    if ext not in _ALLOWED_IMG_EXT:
        return jsonify({"error": f"unsupported image format: {ext}"}), 400
    safe_name = char_id + ext
    thumb_name = char_id + "_thumb.png"

    ref_path = os.path.join(COMFYUI_INPUT_CHARS, safe_name)
    file.save(ref_path)

    try:
        from PIL import Image as PILImage
        img = PILImage.open(ref_path)
        img.thumbnail((256, 256))
        thumb_path = os.path.join(CHARACTERS_DIR, thumb_name)
        img.save(thumb_path, "PNG")
    except Exception:
        thumb_name = None

    now = datetime.now(timezone.utc).isoformat() + "Z"
    conn = _get_characters_db()
    try:
        conn.execute(
            "INSERT INTO characters (id, name, description, reference_image, thumbnail, tags, created_at) VALUES (?,?,?,?,?,?,?)",
            (char_id, name, description, f"characters/{safe_name}", thumb_name, json.dumps(tags), now),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "character_id": char_id,
        "name": name,
        "reference_image": f"characters/{safe_name}",
        "thumbnail": thumb_name,
        "tags": tags,
    })


@image_bp.route("/api/image/characters")
def api_characters_list():
    """List all registered characters
    ---
    tags: [Character]
    security: [{Bearer: []}]
    responses:
      200:
        description: 캐릭터 목록
        schema:
          type: object
          properties:
            characters: {}
            count: {type: integer}
    """
    conn = _get_characters_db()
    try:
        rows = conn.execute("SELECT id, name, description, reference_image, thumbnail, tags, created_at FROM characters ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()
    characters = []
    for r in rows:
        characters.append({
            "id": r[0], "name": r[1], "description": r[2],
            "reference_image": r[3], "thumbnail": r[4],
            "tags": json.loads(r[5]) if r[5] else [],
            "created_at": r[6],
        })
    return jsonify({"characters": characters, "count": len(characters)})


@image_bp.route("/api/image/character/<char_id>")
def api_character_detail(char_id):
    """Get character detail
    ---
    tags: [Character]
    security: [{Bearer: []}]
    parameters:
      - {name: char_id, in: path, type: string, required: true}
    responses:
      200:
        description: 캐릭터 상세
        schema:
          type: object
          properties:
            id: {}
            name: {}
            description: {}
            reference_image: {}
            thumbnail: {}
            tags: {}
            created_at: {}
            metadata: {}
      404:
        description: 캐릭터 없음
    """
    conn = _get_characters_db()
    try:
        r = conn.execute("SELECT id, name, description, reference_image, thumbnail, tags, created_at, metadata FROM characters WHERE id=?", (char_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        return jsonify({"error": "character not found"}), 404
    return jsonify({
        "id": r[0], "name": r[1], "description": r[2],
        "reference_image": r[3], "thumbnail": r[4],
        "tags": json.loads(r[5]) if r[5] else [],
        "created_at": r[6],
        "metadata": json.loads(r[7]) if r[7] else {},
    })


@image_bp.route("/api/image/character/<char_id>", methods=["DELETE"])
def api_character_delete(char_id):
    """Delete a character and its files
    ---
    tags: [Character]
    security: [{Bearer: []}]
    parameters:
      - {name: char_id, in: path, type: string, required: true}
    responses:
      200:
        description: 삭제 성공
        schema:
          type: object
          properties:
            ok: {type: boolean}
            deleted: {}
      404:
        description: 캐릭터 없음
    """
    conn = _get_characters_db()
    try:
        r = conn.execute("SELECT reference_image, thumbnail FROM characters WHERE id=?", (char_id,)).fetchone()
        if not r:
            return jsonify({"error": "character not found"}), 404

        ref_path = os.path.join("/home/deploy/ComfyUI/input", r[0]) if r[0] else None
        thumb_path = os.path.join(CHARACTERS_DIR, r[1]) if r[1] else None
        if ref_path and os.path.exists(ref_path):
            os.remove(ref_path)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)

        conn.execute("DELETE FROM characters WHERE id=?", (char_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "deleted": char_id})


@image_bp.route("/api/image/character/<char_id>/view")
def api_character_view(char_id):
    """View character reference image
    ---
    tags: [Character]
    security: [{Bearer: []}]
    parameters:
      - {name: char_id, in: path, type: string, required: true}
    responses:
      200:
        description: 이미지 파일
      404:
        description: 캐릭터 없음
    """
    conn = _get_characters_db()
    try:
        r = conn.execute("SELECT reference_image FROM characters WHERE id=?", (char_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        return jsonify({"error": "character not found"}), 404
    ref_path = os.path.join("/home/deploy/ComfyUI/input", r[0])
    if not os.path.exists(ref_path):
        return jsonify({"error": "image file not found"}), 404
    return send_file(ref_path, mimetype="image/png")


@image_bp.route("/api/image/character/<char_id>/images")
def api_character_images(char_id):
    """List images generated for a specific character
    ---
    tags: [Character]
    security: [{Bearer: []}]
    parameters:
      - {name: char_id, in: path, type: string, required: true}
      - {name: limit, in: query, type: integer, default: 50}
    responses:
      200:
        description: 해당 캐릭터로 생성된 이미지 목록
        schema:
          type: object
          properties:
            character_id: {}
            character_name: {}
            generated_images: {}
            count: {type: integer}
      404:
        description: 캐릭터 없음
    """
    conn = _get_characters_db()
    try:
        ch = conn.execute("SELECT id, name FROM characters WHERE id=?", (char_id,)).fetchone()
        if not ch:
            return jsonify({"error": "character not found"}), 404

        limit = min(int(request.args.get("limit", 50)), 200)

        rows = conn.execute("""
            SELECT id, status, prompt, response, options, created_at, duration_ms
            FROM jobs
            WHERE job_type='image' AND options LIKE ?
            ORDER BY created_at DESC LIMIT ?
        """, (f'%"{char_id}"%', limit)).fetchall()
    finally:
        conn.close()

    images = []
    for r in rows:
        job_id, status, prompt, response, options, created_at, duration_ms = r
        result_images = []
        if response:
            try:
                resp = json.loads(response)
                result_images = resp.get("images", [])
            except (json.JSONDecodeError, TypeError):
                pass
        if not result_images and response:
            try:
                resp = json.loads(response)
                fn = resp.get("filename")
                if fn:
                    result_images = [{"filename": fn, "subfolder": resp.get("subfolder", "")}]
            except (json.JSONDecodeError, TypeError):
                pass

        images.append({
            "job_id": job_id,
            "status": status,
            "prompt": prompt,
            "images": result_images,
            "created_at": created_at,
            "duration_ms": duration_ms,
            "view_urls": [f"/api/image/view/{img.get('filename', '')}" for img in result_images],
            "s3_urls": [img.get("s3_url") for img in result_images if img.get("s3_url")],
        })

    return jsonify({
        "character_id": char_id,
        "character_name": ch[1],
        "generated_images": images,
        "count": len(images),
    })


# ---------------------------------------------------------------------------
# S3 Image Storage API (v2.70.0)
# ---------------------------------------------------------------------------

@image_bp.route("/api/image/s3/upload/<path:filename>", methods=["POST"])
def api_image_s3_upload(filename):
    """Download image from ComfyUI and upload to S3, return presigned URL
    ---
    tags: [Image, S3 Storage]
    security: [{Bearer: []}]
    parameters:
      - {name: filename, in: path, type: string, required: true}
      - {name: subfolder, in: query, type: string}
    responses:
      200:
        schema:
          type: object
          properties:
            key: {type: string}
            url: {type: string}
            size: {type: integer}
    """
    from core.s3_storage import is_enabled, upload_bytes
    if not is_enabled():
        return jsonify({"error": "S3 storage not configured"}), 503

    subfolder = request.args.get("subfolder", "")
    try:
        r = http_requests.get(f"{_COMFYUI_BASE}/view",
                         params={"filename": filename, "subfolder": subfolder, "type": "output"},
                         timeout=30)
        if r.status_code != 200:
            return jsonify({"error": f"ComfyUI fetch failed: {r.status_code}"}), 502

        # AI 생성물 표시 의무(AI 기본법 §31): 업로드 전 마킹 적용 (ComfyUI 생성물)
        img_bytes = _apply_marking(r.content, model=None)
        marked = img_bytes is not r.content
        content_type = "image/png" if marked else r.headers.get("Content-Type", "image/png")
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        s3_filename = f"{ts}_{filename}"

        s3_meta = {"source": "comfyui", "original_filename": filename}
        if marked:
            s3_meta["ai_generated"] = "true"
        result = upload_bytes(img_bytes, s3_filename, content_type, metadata=s3_meta)
        if "error" in result:
            return jsonify(result), 500
        return jsonify(result)
    except Exception as e:
        return internal_error(e)


@image_bp.route("/api/image/s3/upload-raw", methods=["POST"])
def api_image_s3_upload_raw():
    """Upload raw image file to S3
    ---
    tags: [Image, S3 Storage]
    security: [{Bearer: []}]
    consumes: [multipart/form-data]
    parameters:
      - name: image
        in: formData
        type: file
        required: true
      - name: filename
        in: formData
        type: string
    responses:
      200:
        schema:
          type: object
          properties:
            key: {type: string}
            url: {type: string}
    """
    from core.s3_storage import is_enabled, upload_bytes
    if not is_enabled():
        return jsonify({"error": "S3 storage not configured"}), 503

    if "image" not in request.files:
        return jsonify({"error": "image file required"}), 400

    f = request.files["image"]
    custom_name = request.form.get("filename", "")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    ext = os.path.splitext(f.filename)[1] or ".png"
    s3_filename = f"{ts}_{custom_name or f.filename}" if custom_name else f"{ts}_{f.filename}"

    content_type = f.content_type or "image/png"
    data = f.read()
    result = upload_bytes(data, s3_filename, content_type, metadata={
        "source": "upload",
        "original_filename": f.filename,
    })
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result)


@image_bp.route("/api/detect/image", methods=["POST"])
def api_detect_image():
    """AI 생성 이미지 판별 (메타데이터/출처 기반 — B1)
    ---
    tags: [Image, Detect]
    security: [{Bearer: []}]
    consumes: [multipart/form-data, application/json]
    parameters:
      - {name: image, in: formData, type: file, required: false, description: "판별할 이미지(권장)"}
      - {name: ml, in: formData, type: string, required: false, description: "ML 화소 분류 on/off (기본 config)"}
      - in: body
        name: body
        required: false
        schema:
          type: object
          properties:
            url: {type: string, description: "이미지 URL (공개 http/https). multipart 대신 사용 가능"}
            ml: {type: boolean, description: "ML 화소 분류 on/off (기본 config ai_detector_enabled)"}
    responses:
      200:
        description: 판별 결과
        schema:
          type: object
          properties:
            ai_generated: {type: boolean, description: "메타/출처 기반 AI 생성 판단 (확정 아님)"}
            our_marking:  {type: object, description: "우리(WeRU.B) 마킹 판독 결과"}
            provenance:   {type: object, description: "외부 출처(C2PA/타툴 메타) 휴리스틱"}
            confidence:   {type: string, enum: [high, low, none]}
            method:       {type: string}
            ai_probability: {type: number, description: "ML 분류기 확률(B2/B3 도입 시). 현재 null"}
            disclaimer:   {type: string}
      400: {description: "이미지 누락 또는 무효 URL"}
    """
    img_bytes = None
    if "image" in request.files:
        img_bytes = request.files["image"].read()
    else:
        body = request.json if request.is_json else {}
        url = (body or {}).get("url", "").strip()
        if url:
            if not _validate_webhook_url(url):
                return jsonify({"error": "invalid url (must be public http/https)"}), 400
            try:
                # SSRF: 리다이렉트 추종 금지(공인 도메인이 302로 사설/메타데이터 IP 우회 차단).
                # validate_webhook_url은 IP리터럴만 차단하므로 리다이렉트 차단이 핵심 방어.
                r = http_requests.get(url, timeout=10, stream=True, allow_redirects=False)
                if r.is_redirect or 300 <= r.status_code < 400:
                    return jsonify({"error": "redirect not allowed (SSRF 차단)"}), 400
                # 20MB 상한 (전역 MAX_CONTENT_LENGTH와 정합)
                img_bytes = r.raw.read(20 * 1024 * 1024 + 1, decode_content=True)
                if len(img_bytes) > 20 * 1024 * 1024:
                    return jsonify({"error": "image too large (>20MB)"}), 400
            except Exception as e:
                return jsonify({"error": f"fetch failed: {e}"}), 502
    if not img_bytes:
        return jsonify({"error": "image file or url required"}), 400

    try:
        from core.content_marking import read_marking, detect_provenance
        marking = read_marking(img_bytes)
        prov = detect_provenance(img_bytes)
    except Exception as e:
        return jsonify({"error": f"detect failed: {e}"}), 500

    # 실제 C2PA 매니페스트 판독 (휴리스틱 보강)
    try:
        from core.c2pa_sign import read_manifest, is_c2pa_installed
        if is_c2pa_installed():
            from PIL import Image as _PILImage
            import io as _io
            mime = "image/png"
            try:
                with _PILImage.open(_io.BytesIO(img_bytes)) as _im:
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg",
                            "WEBP": "image/webp"}.get(_im.format, "image/png")
            except Exception:
                pass
            man = read_manifest(img_bytes, mime)
            if man:
                prov["c2pa"] = True
                prov["c2pa_manifest"] = {
                    "active_manifest": man.get("active_manifest"),
                    "claim_generator": (man.get("manifests", {}) or {}).get(
                        man.get("active_manifest", ""), {}).get("claim_generator"),
                }
    except Exception as e:
        logger.debug("c2pa read in detect failed: %s", e)

    ours = bool(marking.get("marked"))
    external = bool(prov.get("c2pa") or prov.get("ai_metadata") or
                    any(h.startswith("software:") for h in prov.get("hints", [])))

    # ML 화소 분류 (B2/B3) — config 게이트 + 요청별 ml 오버라이드
    cfg = _load_config() if _load_config else {}
    ml_default = cfg.get("ai_detector_enabled", True)
    if "image" in request.files:
        ml_req = request.form.get("ml")
    else:
        ml_req = (request.json or {}).get("ml") if request.is_json else None
    if ml_req is None:
        run_ml = ml_default
    else:
        run_ml = str(ml_req).lower() in ("1", "true", "yes", "on")

    ml = {"available": False, "ai_probability": None}
    threshold = cfg.get("ai_detector_threshold", 0.7)
    if run_ml:
        # Node Orchestrator 위임 (node-orchestrator Phase 1) — 노드 미설정/미도달 시 local_fn 폴백.
        # engine ∈ {"imac-worker", "gpu-local"}. classify는 자체 try/except로 no-raise(local_fn 안전).
        def _local_classify():
            from core.ai_image_detector import classify
            return classify(
                img_bytes,
                model=cfg.get("ai_detector_model", "Organika/sdxl-detector"),
                device=cfg.get("ai_detector_device", "cpu"),
                threads=cfg.get("ai_detector_threads", 4),
            )
        try:
            from core.orchestrator import dispatch
            ml = dispatch.run("detect", img_bytes, local_fn=_local_classify)
        except Exception as e:
            ml = {"available": False, "ai_probability": None, "error": str(e)}

    ml_prob = ml.get("ai_probability")
    ml_positive = ml_prob is not None and ml_prob >= threshold

    ai_generated = bool(ours or external or ml_positive)
    if ours or prov.get("c2pa"):
        confidence = "high"            # 마킹/C2PA = 확정적 신호
    elif external:
        confidence = "medium"          # 타툴 메타/소프트웨어 힌트
    elif ml_positive:
        confidence = "low"             # ML 추정만 (오탐 가능)
    else:
        confidence = "none"

    methods = ["metadata"]
    if run_ml and ml.get("available"):
        methods.append("ml")

    return jsonify({
        "ai_generated": ai_generated,
        "our_marking": marking,
        "provenance": prov,
        "ml": ml,
        "ai_probability": ml_prob,
        "confidence": confidence,
        "method": "+".join(methods),
        "disclaimer": (
            "메타데이터/출처 + ML 화소 추정 기반입니다. 메타데이터 제거·변조 시 미탐지될 수 있고 "
            "ML 추정치는 신규 생성기·압축·편집본에 오탐/미탐이 있습니다. "
            "본 결과는 법적 판단의 단독 근거가 될 수 없습니다."
        ),
    })


@image_bp.route("/api/image/s3/list")
def api_image_s3_list():
    """List images in S3 bucket
    ---
    tags: [Image, S3 Storage]
    security: [{Bearer: []}]
    parameters:
      - {name: prefix, in: query, type: string, description: "S3 key prefix filter"}
      - {name: limit, in: query, type: integer, default: 50}
    responses:
      200:
        schema:
          type: object
          properties:
            images: {type: array}
            count: {type: integer}
    """
    from core.s3_storage import is_enabled, list_objects_ex
    if not is_enabled():
        return jsonify({"error": "S3 storage not configured"}), 503

    prefix = request.args.get("prefix")
    limit = min(int(request.args.get("limit", 50)), 200)
    # 조회 실패를 빈 목록으로 흘리지 않는다(200+빈 결과=무성 실패). 2026-09-09 Wasabi 폐지
    # 당시 이 경로가 AccountProblem을 삼키고 count:0을 반환하고 있었다.
    res = list_objects_ex(prefix=prefix, max_keys=limit)
    if res["error"]:
        return jsonify({"error": res["error"], "images": [], "count": 0}), 502
    return jsonify({"images": res["items"], "count": len(res["items"])})


@image_bp.route("/api/image/s3/url/<path:key>")
def api_image_s3_url(key):
    """Get presigned URL for S3 image
    ---
    tags: [Image, S3 Storage]
    security: [{Bearer: []}]
    parameters:
      - {name: key, in: path, type: string, required: true}
      - {name: ttl, in: query, type: integer, default: 3600}
    responses:
      200:
        schema:
          type: object
          properties:
            url: {type: string}
            ttl: {type: integer}
    """
    from core.s3_storage import is_enabled, get_presigned_url, key_allowed
    if not is_enabled():
        return jsonify({"error": "S3 storage not configured"}), 503
    if not key_allowed(key):  # 버킷 임의객체 presign 차단(M8)
        return jsonify({"error": "key out of allowed prefix"}), 403

    # v2.125.70: 존재 확인 먼저 — 종전엔 없는 키에도 서명해 **200 + 열면 NoSuchKey인 URL**을 줬다
    #   (2026-09-12~09-30 node 클라이언트가 Wasabi 폐지로 사라진 06-30 키를 9회 받아 감 · 반복 클래스 #1).
    from core.s3_storage import object_exists
    exists = object_exists(key)
    if exists is False:
        logger.info("[image/s3/url] 없음 → 404: %s %s", key, _caller_tag())
        return jsonify({"error": "object not found", "key": key}), 404
    if exists is None:
        return jsonify({"error": "S3 storage error"}), 502

    ttl = min(int(request.args.get("ttl", 3600)), 86400)
    url = get_presigned_url(key, ttl=ttl)
    if not url:
        return jsonify({"error": "failed to generate URL"}), 500
    return jsonify({"key": key, "url": url, "ttl": ttl})


@image_bp.route("/api/image/s3/delete/<path:key>", methods=["DELETE"])
def api_image_s3_delete(key):
    """Delete image from S3
    ---
    tags: [Image, S3 Storage]
    security: [{Bearer: []}]
    parameters:
      - {name: key, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
    """
    from core.s3_storage import is_enabled, delete_object, key_allowed
    if not is_enabled():
        return jsonify({"error": "S3 storage not configured"}), 503
    if not key_allowed(key):  # 버킷 임의객체 삭제 차단(M8)
        return jsonify({"error": "key out of allowed prefix"}), 403

    ok = delete_object(key)
    return jsonify({"ok": ok, "key": key})


@image_bp.route("/api/image/s3/status")
def api_image_s3_status():
    """S3 storage status
    ---
    tags: [Image, S3 Storage]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
    """
    from core.s3_storage import get_status
    return jsonify(get_status())
