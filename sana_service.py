#!/usr/bin/env python
"""SANA 무스왑 이미지 생성 사이드카 (127.0.0.1:8189).

프로덕션 venv 재사용(torch/diffusers/bitsandbytes, 신규 설치 0). 상시 running이나 모델은
**지연로드 + idle-unload**로 VRAM 자율 반납 → co-resident: 상주 qwen2.5:14b를 밀지 않고
4-bit Gemma TE + VAE 타일로 ~4.4GB peak, 1024² warm ~1.1s (PoC 2026-07-04 실측).

VRAM 상호배제: 메인 앱이 ComfyUI 대형잡(SDXL/Flux) 기동 전 POST /unload로 SANA를 비운다.
내부 전용(127.0.0.1) — 외부 노출 없음, 동시성 1(_lock)로 GPU 직렬화.
"""
import os
import io
import time
import base64
import threading
import logging
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify, send_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [sana] %(message)s")
log = logging.getLogger("sana")

MODEL_PATH = os.environ.get("SANA_MODEL", "/home/deploy/sana_models/Sana_600M_1024px")
CN_MODEL = os.environ.get("SANA_CN_MODEL", "/home/deploy/sana_poc/Sana_CN_diffusers")  # ControlNet(diffusers)
IDLE_TIMEOUT = int(os.environ.get("SANA_IDLE_TIMEOUT", "300"))  # 유휴 후 VRAM 반납(초), 0=상시 유지
HOST = os.environ.get("SANA_HOST", "127.0.0.1")
PORT = int(os.environ.get("SANA_PORT", "8189"))
MAX_STEPS = 40
MAX_DIM = 1536

# 프래그멘테이션 완화(co-resident 타이트 예산)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

app = Flask(__name__)
# 🔑 단일 영속 워커 스레드: 모든 CUDA 작업을 같은 스레드에서 실행 → cuDNN/CUDA 워밍업 유지.
# (Flask threaded=True는 요청마다 새 스레드 → 워밍업 리셋으로 매 호출 콜드=4.3s. 단일 워커=warm 1.1s.)
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sana-gen")
_pipe = None
_mode = None      # 'gen' | 'cn' — VRAM 예산상 한 번에 한 파이프라인만(모드 스왑)
_torch = None
_last_used = 0.0


def _load(mode="gen"):
    """지연로드 — 4-bit Gemma TE + VAE 타일(co-resident 레시피). 워커 스레드 내 호출.

    mode='gen'(SanaPipeline) | 'cn'(SanaControlNetPipeline). 모드 전환 시 기존 언로드.
    """
    global _pipe, _mode, _torch
    import torch
    _torch = torch
    if _pipe is not None and _mode == mode:
        return _pipe
    if _pipe is not None and _mode != mode:  # 모드 스왑 → 기존 완전 반납(순간 peak 방지)
        import gc
        _pipe = None
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
    from transformers import AutoModel, BitsAndBytesConfig
    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16)
    t = time.time()
    if mode == "cn":
        from diffusers import SanaControlNetPipeline
        te = AutoModel.from_pretrained(CN_MODEL, subfolder="text_encoder", quantization_config=bnb,
                                       torch_dtype=torch.bfloat16, device_map="cuda")
        try:
            pipe = SanaControlNetPipeline.from_pretrained(CN_MODEL, variant="fp16",
                                                          text_encoder=te, torch_dtype=torch.bfloat16)
        except Exception:
            pipe = SanaControlNetPipeline.from_pretrained(CN_MODEL, text_encoder=te, torch_dtype=torch.bfloat16)
        pipe.controlnet.to("cuda")
        src = CN_MODEL
    else:
        from diffusers import SanaPipeline
        te = AutoModel.from_pretrained(MODEL_PATH, subfolder="text_encoder", quantization_config=bnb,
                                       torch_dtype=torch.bfloat16, device_map="cuda")
        pipe = SanaPipeline.from_pretrained(MODEL_PATH, text_encoder=te, torch_dtype=torch.bfloat16)
        src = MODEL_PATH
    pipe.transformer.to("cuda")
    pipe.vae.to("cuda", torch.bfloat16)
    pipe.vae.enable_tiling()
    pipe.vae.enable_slicing()
    _pipe = pipe
    _mode = mode
    log.info("loaded %s in %.1fs (%s)", mode, time.time() - t, os.path.basename(src))
    return _pipe


def _do_unload():
    """VRAM 반납 (워커 스레드에서 실행). 생성과 같은 스레드라 mid-generation 충돌 없음.

    ⚠️ `gc.collect()`가 **필수**다. diffusers 파이프라인은 순환 참조가 많아 참조만 끊으면
    (`_pipe = None`) 객체가 즉시 해제되지 않고, 그러면 `empty_cache()`가 회수할 블록도 없다.
    2026-07-29 실측: `loaded:false`인데도 **2.36GB를 계속 점유**해 배치 STT(large-v3)가
    CUDA OOM으로 죽었다(모드 스왑 경로에만 gc가 있고 이 경로엔 없었던 비대칭).

    또 `_pipe`가 이미 None이어도 **캐시 반납은 시도**한다 — 명시적 `/unload` 호출은
    "지금 VRAM을 비워라"는 뜻이지 "파이프라인이 있으면 비워라"가 아니다.
    """
    global _pipe, _mode
    had = _pipe is not None
    _pipe = None
    _mode = None
    import gc
    gc.collect()
    if _torch is not None:
        try:
            _torch.cuda.empty_cache()
            _torch.cuda.synchronize()
        except Exception:                      # noqa: BLE001 — 반납 실패가 서비스를 죽이지 않는다
            log.exception("empty_cache 실패")
    log.info("unloaded (VRAM freed, had_pipe=%s)", had)


def _idle_watch():
    while True:
        time.sleep(30)
        try:
            if _pipe is not None and IDLE_TIMEOUT > 0 and _last_used and (time.time() - _last_used) > IDLE_TIMEOUT:
                _executor.submit(_do_unload).result(timeout=60)
        except Exception:
            log.exception("idle_watch")


@app.post("/generate")
def generate():
    global _last_used
    d = request.get_json(force=True, silent=True) or {}
    prompt = (d.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "prompt required"}), 400
    steps = max(1, min(int(d.get("steps", 20)), MAX_STEPS))
    w = max(256, min(int(d.get("width", 1024)), MAX_DIM))
    h = max(256, min(int(d.get("height", 1024)), MAX_DIM))
    # SANA는 8의 배수 요구 → 내림 정렬
    w -= w % 8
    h -= h % 8
    seed = int(d.get("seed", 0))
    neg = d.get("negative") or ""
    guidance = float(d.get("guidance_scale", 4.5))

    def _work():
        global _last_used
        pipe = _load()
        g = _torch.Generator("cuda").manual_seed(seed)
        kw = dict(prompt=prompt, height=h, width=w, num_inference_steps=steps,
                  guidance_scale=guidance, generator=g)
        if neg:
            kw["negative_prompt"] = neg
        t = time.time()
        img = pipe(**kw).images[0]
        dt = time.time() - t
        _last_used = time.time()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf, dt

    try:
        # 단일 워커 스레드에서 실행(워밍업 유지 + VRAM 직렬화)
        buf, dt = _executor.submit(_work).result()
    except Exception as e:  # OOM 포함 → 500, 워커 retry
        log.exception("generate failed")
        return jsonify({"error": str(e)[:300]}), 500
    resp = send_file(buf, mimetype="image/png")
    resp.headers["X-Sana-Sec"] = f"{dt:.2f}"
    return resp


def _make_control(b64, preprocess=True):
    """제어 이미지 준비. preprocess=True면 PIL 엣지맵(HED 전처리기 미설치 대체)."""
    from PIL import Image, ImageFilter, ImageOps
    raw = base64.b64decode(b64.split(",")[-1])  # data: 접두 제거
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.size != (1024, 1024):
        img = img.resize((1024, 1024))
    if preprocess:
        # 그레이스케일 → FIND_EDGES → RGB (HED보다 거칠지만 구조 제어엔 유효)
        return ImageOps.grayscale(img).filter(ImageFilter.FIND_EDGES).convert("RGB")
    return img


@app.post("/controlnet")
def controlnet():
    """구조 제어 생성 — control_image(base64) 엣지/윤곽대로 프롬프트 렌더링."""
    global _last_used
    d = request.get_json(force=True, silent=True) or {}
    prompt = (d.get("prompt") or "").strip()
    cb64 = d.get("control_image") or ""
    if not prompt or not cb64:
        return jsonify({"error": "prompt and control_image required"}), 400
    steps = max(1, min(int(d.get("steps", 20)), MAX_STEPS))
    seed = int(d.get("seed", 0))
    cn_scale = float(d.get("controlnet_conditioning_scale", 1.0))
    preprocess = d.get("preprocess", True)
    neg = d.get("negative") or ""
    try:
        control = _make_control(cb64, preprocess)
    except Exception as e:
        return jsonify({"error": f"bad control_image: {str(e)[:150]}"}), 400

    def _work():
        global _last_used
        pipe = _load("cn")
        g = _torch.Generator("cuda").manual_seed(seed)
        kw = dict(prompt=prompt, control_image=control, num_inference_steps=steps,
                  guidance_scale=4.5, controlnet_conditioning_scale=cn_scale, generator=g)
        if neg:
            kw["negative_prompt"] = neg
        t = time.time()
        img = pipe(**kw).images[0]
        dt = time.time() - t
        _last_used = time.time()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf, dt

    try:
        buf, dt = _executor.submit(_work).result()
    except Exception as e:
        log.exception("controlnet failed")
        return jsonify({"error": str(e)[:300]}), 500
    resp = send_file(buf, mimetype="image/png")
    resp.headers["X-Sana-Sec"] = f"{dt:.2f}"
    return resp


@app.get("/health")
def health():
    vram = None
    try:
        if _torch is not None:
            free, tot = _torch.cuda.mem_get_info()
            vram = {"free_mb": free // (1024 * 1024), "total_mb": tot // (1024 * 1024)}
    except Exception:
        pass
    return jsonify({
        "ok": True,
        "loaded": _pipe is not None,
        "mode": _mode,
        "idle_s": round(time.time() - _last_used, 1) if _last_used else None,
        "model": os.path.basename(MODEL_PATH),
        "idle_timeout": IDLE_TIMEOUT,
        "vram": vram,
    })


@app.post("/unload")
def unload():
    try:
        _executor.submit(_do_unload).result(timeout=60)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:200]}), 500
    return jsonify({"ok": True, "loaded": False})


if __name__ == "__main__":
    threading.Thread(target=_idle_watch, daemon=True, name="sana-idle").start()
    log.info("SANA sidecar on %s:%d model=%s idle=%ds", HOST, PORT, MODEL_PATH, IDLE_TIMEOUT)
    app.run(host=HOST, port=PORT, threaded=True)
