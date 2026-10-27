"""아바타 R3 — 심장 EP(eikonal LAT + 12-lead ECG) 비동기 잡 매니저.

트윈 서명 URL(NIfTI.gz) → 다운로드 → TotalSegmentator(heart) → 표면 메시 →
표면 정점 eikonal LAT + 현상학적 12-lead ECG(core/cardiac_ep_model) →
glTF(_LAT 임베드) S3 presigned + ECG 인라인 JSON. 202+폴링(organ-mesh 패턴 재사용).

거버넌스: 비식별(지오메트리+스칼라장만)·temp 폐기·비블로킹 워커·감사 로그(PHI_ACCESS=
avatar-cardiac-ep). EP=CPU(openCARP 아님)지만 세그는 GPU → R2 메시와 VRAM 락 **공유 직렬화**.
자문 보조·진단 아님(disclaimer 강제). 입력 NIfTI.gz만(트윈 합의, dicom_zip 미채택).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime, timezone

# R2와 다운로드/SSRF/설정/VRAM 락/결과파싱 재사용(중복 제거·GPU 직렬화 공유)
from core.avatar_mesh import (AVATAR_DB_PATH, _cfg, _download, _lock,
                              _parse_result, _init_lock)

logger = logging.getLogger("ollama-admin")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(BASE_DIR, "scripts", "cardiac_ep_run.py")

_DDL = """
CREATE TABLE IF NOT EXISTS cardiac_ep_jobs (
  job_id     TEXT PRIMARY KEY,
  status     TEXT,             -- pending|downloading|segmenting|uploading|done|failed
  pixels_url TEXT,
  hr         REAL,
  conditions TEXT,             -- JSON 배열
  mesh_url   TEXT,             -- glTF(_LAT) presigned
  lat        TEXT,             -- LAT JSON 폴백 {lat_ms[],min_ms,max_ms} (정점 정합)
  ecg        TEXT,             -- 12-lead ECG JSON
  meta       TEXT,             -- LAT 범위/정점수/엔진 등 JSON
  engine     TEXT,             -- LAT/ECG 솔버 엔진(기본 eikonal-surrogate-v1.1, 후속 대리모델 드롭인)
  error      TEXT,
  created_at TEXT,
  updated_at TEXT
);
"""

# 엔진 레지스트리 — 출력 인터페이스(glTF _LAT + 12-lead ECG)는 락. 후속 대리모델
# (openCARP 학습 BLNM/LNODE 등)은 동일 인터페이스로 여기에 등록하면 드롭인된다(트윈 무변경).
DEFAULT_ENGINE = "eikonal-surrogate-v1.1"
SUPPORTED_ENGINES = {"eikonal-surrogate-v1.1"}


def _db():
    conn = sqlite3.connect(AVATAR_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    with _init_lock:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_DDL)
        # 자동 마이그레이션: engine 컬럼(엔진 선택 플러밍, 기존 잡은 NULL→기본 엔진 해석)
        have = {r[1] for r in conn.execute("PRAGMA table_info(cardiac_ep_jobs)")}
        if "engine" not in have:
            conn.execute("ALTER TABLE cardiac_ep_jobs ADD COLUMN engine TEXT")
        conn.commit()
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat() + "Z"


def _set(job_id, **fields):
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = _db()
    try:
        conn.execute(f"UPDATE cardiac_ep_jobs SET {cols} WHERE job_id=?",
                     (*fields.values(), job_id))
        conn.commit()
    finally:
        conn.close()


def get_job(job_id) -> dict | None:
    conn = _db()
    try:
        r = conn.execute("SELECT * FROM cardiac_ep_jobs WHERE job_id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = dict(r)
    d["conditions"] = json.loads(d["conditions"]) if d.get("conditions") else []
    d["lat"] = json.loads(d["lat"]) if d.get("lat") else None
    d["ecg"] = json.loads(d["ecg"]) if d.get("ecg") else None
    d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
    return d


def submit(pixels_url, hr=75.0, conditions=None, engine=None) -> dict:
    """잡 생성 + 백그라운드 처리. 즉시 202용 dict. NIfTI.gz 서명 URL(SSRF 1차검사).

    engine: LAT/ECG 솔버(기본 eikonal-surrogate-v1.1). 출력 인터페이스 불변 — 후속
    대리모델은 SUPPORTED_ENGINES 등록 시 트윈 무변경으로 드롭인.
    """
    pixels_url = (pixels_url or "").strip()
    if not pixels_url:
        return {"error": "pixels_url required"}
    engine = (engine or DEFAULT_ENGINE).strip()
    if engine not in SUPPORTED_ENGINES:
        return {"error": f"unsupported engine: {engine} (지원: {sorted(SUPPORTED_ENGINES)})"}
    is_local = os.path.isabs(pixels_url) and os.path.isfile(pixels_url)
    if not is_local:
        from urllib.parse import urlparse
        if urlparse(pixels_url).scheme != "https":
            return {"error": "pixels_url must be https"}
        from core.security import validate_webhook_url
        if not validate_webhook_url(pixels_url):
            return {"error": "pixels_url must be a public https URL (사설IP 차단)"}
    elif not _cfg("allow_local_input"):
        return {"error": "local input not allowed (avatar_allow_local_input=false)"}

    try:
        hr = float(hr)
    except (TypeError, ValueError):
        hr = 75.0
    hr = min(max(hr, 20.0), 220.0)
    conditions = [str(c).strip().lower() for c in (conditions or []) if str(c).strip()][:8]

    job_id = "ep-" + uuid.uuid4().hex[:12]
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO cardiac_ep_jobs (job_id,status,pixels_url,hr,conditions,engine,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, "pending", pixels_url, hr, json.dumps(conditions), engine, _now(), _now()))
        conn.commit()
    finally:
        conn.close()
    threading.Thread(target=_process, args=(job_id,), name=f"cardiac-ep-{job_id}",
                     daemon=True).start()
    logger.info("[cardiac-ep] job %s submitted (hr=%s conditions=%s engine=%s)",
                job_id, hr, conditions, engine)
    return {"job_id": job_id, "status": "pending"}


def _process(job_id):
    """백그라운드 워커: 다운로드 → 세그→LAT→ECG(격리 venv) → S3 → done. 동시성 1(R2와 공유)."""
    job = get_job(job_id)
    if not job:
        return
    cleanup_path = None
    out_glb = None
    with _lock:   # VRAM 직렬화(R2 organ-mesh와 공유)
        try:
            logger.info("[cardiac-ep] PHI_ACCESS endpoint=avatar-cardiac-ep job=%s (비식별 CT→EP)", job_id)
            _set(job_id, status="downloading")
            in_path, cleanup_path = _download(job["pixels_url"], "nifti_gz", job_id)

            _set(job_id, status="segmenting")
            fd, out_glb = tempfile.mkstemp(suffix=".glb", prefix=f"{job_id}_")
            os.close(fd)
            ts_py = _cfg("ts_python")
            if not os.path.exists(ts_py):
                raise RuntimeError(f"격리 venv 파이썬 없음: {ts_py}")
            cmd = [ts_py, SCRIPT, in_path, out_glb, "--hr", str(job["hr"]),
                   "--conditions", ",".join(job["conditions"]),
                   "--engine", str(job.get("engine") or DEFAULT_ENGINE)]
            cp = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=int(_cfg("seg_timeout_s")))
            res = _parse_result(cp.stdout)
            if not res or not res.get("ok"):
                err = (res or {}).get("error") or (cp.stderr or cp.stdout or "")[-200:]
                raise RuntimeError(f"cardiac-ep pipeline failed: {err}")

            ecg = res.pop("ecg_12lead", None)
            lat = res.pop("lat", None)
            _set(job_id, status="uploading", lat=json.dumps(lat, ensure_ascii=False),
                 ecg=json.dumps(ecg, ensure_ascii=False),
                 meta=json.dumps(res, ensure_ascii=False))
            from core import s3_storage
            if not s3_storage.is_enabled():
                raise RuntimeError("S3 미구성(s3_enabled=false)")
            up = s3_storage.upload_file(
                out_glb, filename=f"avatar/{job_id}.glb",
                content_type="model/gltf-binary",
                metadata={"kind": "avatar-cardiac-ep", "deidentified": "true"})
            mesh_url = (up or {}).get("url") if isinstance(up, dict) else up
            if not mesh_url:
                raise RuntimeError("S3 업로드 실패")
            _set(job_id, status="done", mesh_url=mesh_url, error="")
            logger.info("[cardiac-ep] job %s done (V%s, LAT %.0f~%.0fms, %.1fs)",
                        job_id, res.get("vertex_count"), res.get("lat_min_ms", 0),
                        res.get("lat_max_ms", 0), res.get("seconds", 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("[cardiac-ep] job %s failed: %s", job_id, str(e)[:200])
            _set(job_id, status="failed", error=str(e)[:300])
        finally:
            import shutil
            for p in (out_glb, cleanup_path):
                try:
                    if not p or not os.path.exists(p):
                        continue
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                except Exception:  # noqa: BLE001
                    pass


def stats() -> dict:
    conn = _db()
    try:
        by = {r["status"]: r["c"] for r in conn.execute(
            "SELECT status, count(*) c FROM cardiac_ep_jobs GROUP BY status").fetchall()}
        total = conn.execute("SELECT count(*) FROM cardiac_ep_jobs").fetchone()[0]
    finally:
        conn.close()
    return {"total": total, "by_status": by, "engine": "eikonal-surrogate-v1.1",
            "ts_python_available": os.path.exists(str(_cfg("ts_python")))}
