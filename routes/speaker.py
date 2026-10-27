"""Speaker Blueprint — 성문 등록·대조·파기 (HIS VoiceEMR B2 계약 · v2.125.51)

HIS `stt-chunk.client.ts`가 이미 호출 코드를 갖고 있는 계약을 그대로 구현한다(계약 정본 = HIS 호출부).
오디오는 저장하지 않고 ECAPA 임베딩 합과 표본 수만 둔다(`core/speaker_store.py`).

4 엔드포인트:
  POST   /api/speaker/enroll   — 표본 누적 (body=raw PCM, X-Speaker-Id·X-Sample-Rate·X-Prompt-Id)
  POST   /api/speaker/match    — 등록 화자 대조 (body=raw PCM, X-Speaker-Id·X-Sample-Rate)
  DELETE /api/speaker/<id>     — 성문 파기(철회권) — **없는 id도 200**(HIS는 404를 '파기 못함'으로 읽는다)
  GET    /api/speaker/status   — 모델 가용성·집계(식별자 없음)
"""
import hashlib
import logging
import re

from flask import Blueprint, jsonify, request

from core import speaker_store as S
from core.rate_limit import heavy_limit

logger = logging.getLogger("ollama-admin")
speaker_bp = Blueprint("speaker", __name__)

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@\-]{1,128}$")


def _sid_tag(sid: str) -> str:
    """로그용 — 식별자 원문 대신 짧은 해시."""
    return hashlib.sha256(sid.encode()).hexdigest()[:10]


def _speaker_id(raw):
    sid = (raw or "").strip()
    if not _ID_RE.match(sid):
        return None
    return sid


def _sample_rate():
    try:
        return int(request.headers.get("X-Sample-Rate", S.TARGET_SR))
    except (TypeError, ValueError):
        return -1


def _err(code, msg, status):
    return jsonify({"error": code, "detail": msg}), status


def _run(fn, sid):
    try:
        return fn(sid, request.get_data(cache=False), _sample_rate())
    except S.SampleError as e:
        return _err(e.code, str(e), e.status)
    except S.ModelUnavailable as e:
        logger.warning("[speaker] 모델 미가용: %s", e)
        return _err("speaker_model_unavailable", "성문 임베딩 모델을 불러올 수 없다", 503)
    except Exception as e:  # noqa: BLE001 — 무성 실패 금지: 로그 + 5xx
        logger.exception("[speaker] 처리 실패 sid=%s: %s", _sid_tag(sid), e)
        return _err("speaker_failed", "성문 처리 실패", 500)


@speaker_bp.route("/api/speaker/enroll", methods=["POST"])
@heavy_limit("60 per minute")
def speaker_enroll():
    """Enroll one voice sample
    ---
    tags: [Speaker]
    summary: 성문 표본 1건 등록(누적)
    description: >
      본문은 **raw PCM 16-bit LE mono**(`Content-Type: application/octet-stream`). 헤더
      `X-Speaker-Id`(필수·1~128자 `[A-Za-z0-9_.:@-]`) · `X-Sample-Rate`(기본 16000) · `X-Prompt-Id`(선택·미사용).
      **오디오는 저장하지 않는다** — ECAPA 임베딩 합과 표본 수만 누적한다.
      `quality`는 이 표본과 기존 등록 중심의 코사인 유사도(첫 표본은 null).
    consumes: [application/octet-stream]
    responses:
      200:
        description: 등록됨
        schema:
          type: object
          properties:
            speaker_id: {type: string}
            samples: {type: integer}
            quality: {type: number, x-nullable: true}
      400: {description: "X-Speaker-Id 누락/형식 오류 · 표본 부적합(sample_too_short·sample_silent·bad_pcm·bad_sample_rate)"}
      409: {description: "표본 한도 도달(max_samples)"}
      413: {description: "표본이 너무 길다(sample_too_long·30초 초과)"}
      503: {description: "임베딩 모델 미가용"}
    """
    sid = _speaker_id(request.headers.get("X-Speaker-Id"))
    if not sid:
        return _err("bad_speaker_id", "X-Speaker-Id required (1~128, [A-Za-z0-9_.:@-])", 400)
    r = _run(S.enroll, sid)
    if isinstance(r, tuple):
        return r
    logger.info("[speaker] enroll sid=%s samples=%s", _sid_tag(sid), r["samples"])
    return jsonify(r), 200


@speaker_bp.route("/api/speaker/match", methods=["POST"])
@heavy_limit("120 per minute")
def speaker_match():
    """Match a voice sample against an enrolled speaker
    ---
    tags: [Speaker]
    summary: 등록 화자 대조
    description: >
      본문·헤더는 enroll과 같다. 반환 `score`는 등록 중심과의 **코사인 유사도**(-1~1).
      미등록이면 오류가 아니라 `{"enrolled": false, "score": 0}`. 본인 판정 임계(τ)는 **호출자가** 적용한다
      (HIS 기본 0.7). 성문은 **1차 필터이지 인증이 아니다**.
    consumes: [application/octet-stream]
    responses:
      200:
        description: 대조 결과
        schema:
          type: object
          properties:
            enrolled: {type: boolean}
            score: {type: number}
            samples: {type: integer}
      400: {description: "X-Speaker-Id 누락/형식 오류 · 표본 부적합"}
      413: {description: "표본이 너무 길다"}
      503: {description: "임베딩 모델 미가용"}
    """
    sid = _speaker_id(request.headers.get("X-Speaker-Id"))
    if not sid:
        return _err("bad_speaker_id", "X-Speaker-Id required (1~128, [A-Za-z0-9_.:@-])", 400)
    r = _run(S.match, sid)
    if isinstance(r, tuple):
        return r
    return jsonify(r), 200


@speaker_bp.route("/api/speaker/<speaker_id>", methods=["DELETE"])
def speaker_delete(speaker_id):
    """Delete an enrolled voiceprint
    ---
    tags: [Speaker]
    summary: 성문 파기(철회권)
    description: >
      **멱등**: 없는 id도 200(`deleted: false`). HIS는 404를 '미가용=파기 못함'으로 해석하므로 404를 쓰지 않는다.
    parameters:
      - {in: path, name: speaker_id, type: string, required: true}
    responses:
      200:
        description: 파기 처리(있었으면 deleted=true)
        schema:
          type: object
          properties:
            speaker_id: {type: string}
            deleted: {type: boolean}
      400: {description: "speaker_id 형식 오류"}
    """
    sid = _speaker_id(speaker_id)
    if not sid:
        return _err("bad_speaker_id", "speaker_id 형식 오류", 400)
    try:
        existed = S.delete(sid)
    except Exception as e:  # noqa: BLE001 — 파기 실패를 파기로 보이게 하지 않는다
        logger.exception("[speaker] 파기 실패 sid=%s: %s", _sid_tag(sid), e)
        return _err("speaker_delete_failed", "성문 파기 실패", 500)
    logger.info("[speaker] delete sid=%s existed=%s", _sid_tag(sid), existed)
    return jsonify({"speaker_id": sid, "deleted": existed}), 200


@speaker_bp.route("/api/speaker/status")
def speaker_status():
    """Speaker service status
    ---
    tags: [Speaker]
    summary: 성문 서비스 상태(집계만)
    responses:
      200:
        description: 상태
        schema:
          type: object
          properties:
            model_available: {type: boolean}
            speakers: {type: integer}
            samples: {type: integer}
            stores_audio: {type: boolean}
    """
    from core import meeting_diarize as md
    try:
        st = S.stats()
    except Exception as e:  # noqa: BLE001
        logger.warning("[speaker] stats 실패: %s", e)
        st = {"speakers": None, "samples": None}
    return jsonify({"model_available": bool(md.is_available()), **st,
                    "stores_audio": False, "embedding": "speechbrain/spkrec-ecapa-voxceleb",
                    "score": "cosine(-1~1)", "contract": "HIS voice-emr stt-chunk.client.ts B2"}), 200
