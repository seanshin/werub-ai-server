# Release v2.116.1 — SANA 사이드카 언로드 게이트 (품질 refinement)

## 변경
v2.116.0에서 ComfyUI 이미지 잡 실행 전 `_unload_sana_sidecar()`(VRAM 상호배제)를 **무조건** 호출하던 것을, **`sana_enabled=true`일 때만** 호출하도록 게이트를 좁혔다.

## 근거 (하위 호환 강화)
- **기능 비활성(기본)이면 기존 ComfyUI 경로에 신규 호출이 아예 없음** → "SANA 기능 OFF 시 기존 동작 100% 불변"을 코드로 강제.
- 기능 활성 시에는 VRAM 상호배제(`SANA + SDXL/Flux + qwen` 동시 불가 방지)가 그대로 유지. `/unload`는 멱등(미로드 시 no-op)이라 추가 복잡도 0.

## 검증
- `app.py` `image_worker_loop`: `if not _sana_engine:` 블록 내 `_unload_sana_sidecar()`를 `if load_config().get("sana_enabled", False):`로 게이트.
- graceful HUP NRestarts0·health200·engine=sana 잡 정상·qwen 100% GPU 무스왑 유지.

## 하위 호환 요약 (v2.115.0~v2.116.1)
| 항목 | sana OFF(기본) | sana ON |
|---|---|---|
| ComfyUI 잡 경로 | **완전 무변경** | LLM 언로드 후 SANA 언로드(멱등 상호배제) |
| `/api/image/generate` | engine 미전송=동일 응답/폴링/webhook | engine=sana opt-in |
| 신규 엔드포인트 | 게이트로 400 | `/api/image/sana/controlnet` 등 |
