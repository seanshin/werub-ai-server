"""성문(화자 임베딩) 등록·대조 저장소 — HIS VoiceEMR B2 계약 (v2.125.51).

HIS 호출부(`hospital-run/apps/api/src/modules/voice-emr/stt-chunk.client.ts`)가 기대하는 계약:
  - 오디오 = raw PCM 16-bit LE mono (`X-Sample-Rate`, 기본 16000)
  - enroll → {speaker_id, samples, quality}
  - match  → {enrolled, score}   (HIS가 isOwner = enrolled ∧ score ≥ τ(기본 0.7) 판정)
  - delete → 200 (HIS는 404/405/501을 '미가용=파기 못함'으로 읽는다 — **없는 id도 200**)

설계 규율:
  - **오디오는 저장하지 않는다.** 저장하는 것은 L2 정규화 임베딩의 합(192-d float32)과 표본 수뿐.
  - 임베딩 = SpeechBrain ECAPA-TDNN(`core.meeting_diarize._get_encoder` 재사용 — 같은 모델·같은 장치 선택).
  - score = 표본 임베딩과 등록 중심(centroid)의 **코사인 유사도**(-1~1). ⚠️ HIS 기본 τ=0.7은
    ECAPA 코사인 분포에서 **높은 편**일 수 있다 — 실음성으로 보정 전까지는 오거부(본인 불인정) 쪽으로
    기운다. HIS 원칙("성문은 1차 필터일 뿐 인증 아님")상 안전측이다.
  - 임베딩 함수는 주입 가능(`set_embedder`) — 회귀는 모델 없이 계약을 검증한다.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone

import numpy as np

logger = logging.getLogger("ollama-admin")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "speaker.db")
TARGET_SR = 16000
MIN_SEC = 1.0          # HIS는 1.2초 미만을 자체 거절 — 서버는 조금 느슨하게(직접 호출 방어)
MAX_SEC = 30.0
SILENCE_RMS = 1e-3     # 정규화 파형 RMS — 이보다 작으면 무음 표본
MAX_SAMPLES = 20       # HIS 한도 8 — 서버는 여유 두되 무한 누적은 막는다
EMB_DIM = 192

_lock = threading.Lock()
_embedder = None       # callable(float32 mono 16k np.ndarray) -> np.ndarray(EMB_DIM)


class SampleError(ValueError):
    """표본 자체가 부적합(짧음·무음·형식) — 400 계열."""

    def __init__(self, code: str, msg: str, status: int = 400):
        super().__init__(msg)
        self.code, self.status = code, status


class ModelUnavailable(RuntimeError):
    """임베딩 모델 미가용 — 503."""


def set_embedder(fn):
    """테스트·대체 백엔드용 주입. None이면 기본(ECAPA)으로 복귀."""
    global _embedder
    _embedder = fn


def _default_embed(wave: np.ndarray) -> np.ndarray:
    from core import meeting_diarize as md
    if not md.is_available():
        raise ModelUnavailable("speechbrain unavailable")
    import torch
    enc = md._get_encoder()
    with torch.no_grad():
        emb = enc.encode_batch(torch.from_numpy(wave).unsqueeze(0).to(md._device))
    return emb.squeeze().detach().cpu().numpy().astype(np.float32)


def pcm_to_wave(pcm: bytes, sample_rate: int) -> np.ndarray:
    """raw PCM 16-bit LE mono → float32 [-1,1] @16k. 길이·무음 검사 포함."""
    if not pcm or len(pcm) % 2:
        raise SampleError("bad_pcm", "PCM 16-bit LE mono 바이트열이 아니다(길이 0 또는 홀수)")
    if not (8000 <= sample_rate <= 48000):
        raise SampleError("bad_sample_rate", "X-Sample-Rate는 8000~48000")
    wave = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    sec = len(wave) / sample_rate
    if sec < MIN_SEC:
        raise SampleError("sample_too_short", f"표본이 너무 짧다({sec:.2f}s < {MIN_SEC}s)")
    if sec > MAX_SEC:
        raise SampleError("sample_too_long", f"표본이 너무 길다({sec:.1f}s > {MAX_SEC}s)", 413)
    if float(np.sqrt(np.mean(wave ** 2))) < SILENCE_RMS:
        raise SampleError("sample_silent", "무음 표본")
    if sample_rate != TARGET_SR:
        import resampy
        wave = resampy.resample(wave, sample_rate, TARGET_SR).astype(np.float32)
    return wave


def embed(wave: np.ndarray) -> np.ndarray:
    v = (_embedder or _default_embed)(wave)
    v = np.asarray(v, dtype=np.float32).reshape(-1)
    n = float(np.linalg.norm(v))
    if not np.isfinite(n) or n == 0.0:
        raise SampleError("bad_embedding", "임베딩 산출 실패(0 벡터)")
    return v / n


def _db(path: str | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    new = not os.path.exists(p)
    c = sqlite3.connect(p, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    c.execute("""CREATE TABLE IF NOT EXISTS speakers(
        speaker_id TEXT PRIMARY KEY, emb_sum BLOB NOT NULL, samples INTEGER NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    if new:
        try:
            os.chmod(p, 0o600)   # 생체정보 파생물 — 소유자만
        except OSError:
            pass
    return c


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _centroid(emb_sum: bytes) -> np.ndarray:
    v = np.frombuffer(emb_sum, dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)


def enroll(speaker_id: str, pcm: bytes, sample_rate: int = TARGET_SR, db_path: str | None = None) -> dict:
    """표본 1건 누적. 반환 {speaker_id, samples, quality}.

    quality = 이 표본과 **기존** 중심의 코사인(일관성). 첫 표본은 비교 대상이 없어 None.
    """
    v = embed(pcm_to_wave(pcm, sample_rate))          # 무거운 계산은 락 밖
    with _lock:
        c = _db(db_path)
        try:
            row = c.execute("SELECT emb_sum, samples FROM speakers WHERE speaker_id=?",
                            (speaker_id,)).fetchone()
            now = _now()
            if row is None:
                c.execute("INSERT INTO speakers VALUES(?,?,?,?,?)",
                          (speaker_id, v.tobytes(), 1, now, now))
                samples, quality = 1, None
            else:
                if row[1] >= MAX_SAMPLES:
                    raise SampleError("max_samples", f"표본 한도({MAX_SAMPLES}) 도달", 409)
                quality = float(np.dot(_centroid(row[0]), v))
                new_sum = np.frombuffer(row[0], dtype=np.float32) + v
                samples = row[1] + 1
                c.execute("UPDATE speakers SET emb_sum=?, samples=?, updated_at=? WHERE speaker_id=?",
                          (new_sum.astype(np.float32).tobytes(), samples, now, speaker_id))
            c.commit()
        finally:
            c.close()
    return {"speaker_id": speaker_id, "samples": samples,
            "quality": None if quality is None else round(quality, 4)}


def match(speaker_id: str, pcm: bytes, sample_rate: int = TARGET_SR, db_path: str | None = None) -> dict:
    """반환 {enrolled, score, samples}. 미등록이면 {enrolled: False, score: 0.0} — 오류가 아니다."""
    c = _db(db_path)
    try:
        row = c.execute("SELECT emb_sum, samples FROM speakers WHERE speaker_id=?",
                        (speaker_id,)).fetchone()
    finally:
        c.close()
    if row is None:
        # 미등록이어도 표본 형식은 검사한다(잘못된 입력을 '미등록'으로 삼키지 않게)
        pcm_to_wave(pcm, sample_rate)
        return {"enrolled": False, "score": 0.0, "samples": 0}
    v = embed(pcm_to_wave(pcm, sample_rate))
    return {"enrolled": True, "score": round(float(np.dot(_centroid(row[0]), v)), 4), "samples": row[1]}


def delete(speaker_id: str, db_path: str | None = None) -> bool:
    """성문 파기. 있었으면 True. **없어도 예외 없음**(멱등 — 호출자는 200)."""
    with _lock:
        c = _db(db_path)
        try:
            n = c.execute("DELETE FROM speakers WHERE speaker_id=?", (speaker_id,)).rowcount
            c.commit()
        finally:
            c.close()
    return n > 0


def stats(db_path: str | None = None) -> dict:
    """집계만(식별자 없음)."""
    c = _db(db_path)
    try:
        n, s = c.execute("SELECT COUNT(*), COALESCE(SUM(samples),0) FROM speakers").fetchone()
    finally:
        c.close()
    return {"speakers": n, "samples": s}
