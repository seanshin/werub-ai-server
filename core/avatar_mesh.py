"""아바타 R2 — CT NIfTI → 장기 메시(glTF) 비동기 잡 매니저.

트윈 서명 URL(NIfTI.gz) → AI 백그라운드 다운로드 → TotalSegmentator(격리 venv, fast 3mm)
→ marching_cubes → trimesh → .glb → S3 presigned(짧은 TTL). 202+폴링, 동시성 1(VRAM 직렬화).

거버넌스(트윈 합의): 비식별(glTF 지오메트리만)·temp 처리 후 폐기·요청핸들러 비블로킹
(다운로드/세그는 백그라운드 워커)·감사 로그(PHI_ACCESS endpoint=avatar-mesh).
세그 파이프라인은 메인 venv가 아닌 격리 venv(~/ts_poc_venv) 파이썬으로 subprocess 호출.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

logger = logging.getLogger("ollama-admin")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AVATAR_DB_PATH = os.path.join(BASE_DIR, "avatar.db")
SCRIPT = os.path.join(BASE_DIR, "scripts", "avatar_organ_mesh.py")

_DEFAULTS = {
    "ts_python": os.path.expanduser("~/ts_poc_venv/bin/python"),
    "seg_timeout_s": 1200,        # 세그+메시 상한
    "download_max_mb": 500,
    "mesh_ttl_s": 1800,           # presigned 짧은 TTL(30분)
    "allow_full": False,          # full(1.5mm)=OOM 위험 → 기본 차단(fast로 강등)
    "allow_local_input": False,   # 로컬 파일 경로 입력 허용(GPU 테스트용)
    "full_unload_model": "",      # full 시 언로드할 LLM(예 qwen2.5:14b), 빈값=미실시
}

_lock = threading.Lock()         # 동시성 1(VRAM 직렬화) — 세그(R2)+심장 EP(R3) 공유 GPU 직렬화
_init_lock = threading.Lock()

# 지원 장기 ROI(다장기 아바타) — scripts/avatar_organ_mesh.py ROI_LABELS와 동기
SUPPORTED_ROIS = ("lung", "heart", "kidney", "liver", "heart_chambers")  # heart_chambers=심혈관 세분(⚠️TS 라이선스 필요·fast 미지원)

_DDL = """
CREATE TABLE IF NOT EXISTS avatar_jobs (
  job_id     TEXT PRIMARY KEY,
  status     TEXT,             -- pending | downloading | segmenting | uploading | done | failed
  pixels_url TEXT,
  fmt        TEXT,
  mode       TEXT,
  roi        TEXT,
  mesh_url   TEXT,
  meta       TEXT,             -- 결과 메타(JSON: geometries/vertices/faces/organs/seconds)
  error      TEXT,
  created_at TEXT,
  updated_at TEXT
);
"""


def _cfg(key):
    try:
        from core.config import load_config
        v = (load_config() or {}).get(f"avatar_{key}")
        if v is not None and v != "":
            return v
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULTS[key]


def _db():
    conn = sqlite3.connect(AVATAR_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    with _init_lock:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_DDL)
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
        conn.execute(f"UPDATE avatar_jobs SET {cols} WHERE job_id=?",
                     (*fields.values(), job_id))
        conn.commit()
    finally:
        conn.close()


def get_job(job_id) -> dict | None:
    conn = _db()
    try:
        r = conn.execute("SELECT * FROM avatar_jobs WHERE job_id=?", (job_id,)).fetchone()
    finally:
        conn.close()
    if not r:
        return None
    d = dict(r)
    d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
    return d


def submit(pixels_url, fmt="nifti_gz", mode="fast", roi="lung") -> dict:
    """잡 생성 + 백그라운드 처리 시작. 즉시 202용 dict 반환. pixels_url 정적 SSRF 1차 검사."""
    pixels_url = (pixels_url or "").strip()
    if not pixels_url:
        return {"error": "pixels_url required"}
    roi = (roi or "lung").strip().lower()
    if roi not in SUPPORTED_ROIS:
        return {"error": f"unsupported roi: {roi} (지원: {', '.join(SUPPORTED_ROIS)})"}
    # 로컬 입력: NIfTI 파일 또는 DICOM 폴더(PACS 폴더 접근 시)
    is_local = os.path.isabs(pixels_url) and (os.path.isfile(pixels_url) or os.path.isdir(pixels_url))
    if not is_local:
        if urlparse(pixels_url).scheme != "https":
            return {"error": "pixels_url must be https"}
        from core.security import validate_webhook_url
        if not validate_webhook_url(pixels_url):  # 공인 https·IP리터럴 정적검사
            return {"error": "pixels_url must be a public https URL (사설IP 차단)"}
    elif not _cfg("allow_local_input"):
        return {"error": "local input not allowed (avatar_allow_local_input=false)"}

    job_id = "avt-" + uuid.uuid4().hex[:12]
    if mode == "full" and not _cfg("allow_full"):
        mode = "fast"   # OOM 회피 — full 차단 시 fast로 강등
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO avatar_jobs (job_id,status,pixels_url,fmt,mode,roi,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (job_id, "pending", pixels_url, fmt, mode, roi, _now(), _now()))
        conn.commit()
    finally:
        conn.close()
    threading.Thread(target=_process, args=(job_id,), name=f"avatar-{job_id}", daemon=True).start()
    logger.info("[avatar] job %s submitted (mode=%s roi=%s)", job_id, mode, roi)
    return {"job_id": job_id, "status": "pending"}


def _download(pixels_url, fmt, job_id) -> tuple:
    """입력 확보 → (입력경로, 정리대상경로). 입력은 NIfTI 파일 또는 DICOM 폴더.

    로컬(파일/폴더): 그대로 사용(정리 안 함). URL: SSRF 검증 후 백그라운드 다운로드.
    dicom_zip는 ZIP 받아 임시 폴더로 압축해제(zip bomb 가드) → 폴더 반환.
    """
    if os.path.isabs(pixels_url) and (os.path.isfile(pixels_url) or os.path.isdir(pixels_url)):
        return pixels_url, None  # 로컬 입력(PACS 폴더 등) — 정리 대상 아님
    from core.security import resolve_host_is_public
    host = urlparse(pixels_url).hostname or ""
    if not resolve_host_is_public(host):
        raise ValueError("pixels_url host is not public (SSRF 차단)")
    import requests
    cap = int(_cfg("download_max_mb")) * 1024 * 1024
    suffix = ".zip" if fmt == "dicom_zip" else ".nii.gz"
    fd, dl_path = tempfile.mkstemp(suffix=suffix, prefix=f"{job_id}_")
    os.close(fd)
    n = 0
    # 리다이렉트 미추종: 공인 호스트가 302로 사설/메타데이터 IP로 우회(SSRF)하는 것 차단.
    # 서명 URL(S3/트윈)은 직접 GET이라 리다이렉트 불요. 3xx면 거부.
    with requests.get(pixels_url, stream=True, timeout=120, allow_redirects=False) as r:
        if r.is_redirect or 300 <= r.status_code < 400:
            raise ValueError("redirect not allowed (SSRF 리다이렉트 우회 차단)")
        r.raise_for_status()
        with open(dl_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                n += len(chunk)
                if n > cap:
                    raise ValueError(f"download exceeds {_cfg('download_max_mb')}MB cap")
                f.write(chunk)
    if fmt != "dicom_zip":
        return dl_path, dl_path  # NIfTI 파일(정리 대상)
    # DICOM ZIP → 임시 폴더로 압축해제(zip bomb 가드: 총 해제크기·파일수 상한)
    import zipfile
    out_dir = tempfile.mkdtemp(prefix=f"{job_id}_dcm_")
    extracted = 0
    with zipfile.ZipFile(dl_path) as z:
        infos = z.infolist()
        if len(infos) > 50000:
            raise ValueError("zip 파일 수 과다(>50000)")
        for info in infos:
            if info.is_dir():
                continue
            # 경로 탐색(zip slip) 방지
            dest = os.path.join(out_dir, os.path.basename(info.filename))
            # zip bomb: 신고된 file_size가 아닌 실제 해제 바이트로 카운트(메타 위조 방지)
            with z.open(info) as src, open(dest, "wb") as dst:
                while True:
                    buf = src.read(1 << 20)
                    if not buf:
                        break
                    extracted += len(buf)
                    if extracted > cap * 4:
                        raise ValueError("압축해제 크기 상한 초과")
                    dst.write(buf)
    try:
        os.remove(dl_path)
    except Exception:  # noqa: BLE001
        pass
    return out_dir, out_dir  # DICOM 폴더(정리 대상)


def _process(job_id):
    """백그라운드 워커: 다운로드 → 세그→메시(격리 venv) → S3 → done. 동시성 1."""
    job = get_job(job_id)
    if not job:
        return
    cleanup_path = None
    out_glb = None
    with _lock:   # VRAM 직렬화
        try:
            logger.info("[avatar] PHI_ACCESS endpoint=avatar-mesh job=%s (비식별 CT→메시)", job_id)
            _set(job_id, status="downloading")
            in_path, cleanup_path = _download(job["pixels_url"], job["fmt"], job_id)

            _set(job_id, status="segmenting")
            fd, out_glb = tempfile.mkstemp(suffix=".glb", prefix=f"{job_id}_")
            os.close(fd)
            ts_py = _cfg("ts_python")
            if not os.path.exists(ts_py):
                raise RuntimeError(f"격리 venv 파이썬 없음: {ts_py}")
            cp = subprocess.run(
                [ts_py, SCRIPT, in_path, out_glb, "--mode", job["mode"], "--roi", job["roi"]],
                capture_output=True, text=True, timeout=int(_cfg("seg_timeout_s")))
            res = _parse_result(cp.stdout)
            if not res or not res.get("ok"):
                err = (res or {}).get("error") or (cp.stderr or cp.stdout or "")[-200:]
                raise RuntimeError(f"mesh pipeline failed: {err}")

            _set(job_id, status="uploading", meta=json.dumps(res, ensure_ascii=False))
            from core import s3_storage
            if not s3_storage.is_enabled():
                raise RuntimeError("S3 미구성(s3_enabled=false)")
            up = s3_storage.upload_file(
                out_glb, filename=f"avatar/{job_id}.glb",
                content_type="model/gltf-binary",
                metadata={"kind": "avatar-organ-mesh", "deidentified": "true"})
            mesh_url = (up or {}).get("url") if isinstance(up, dict) else up
            if not mesh_url:
                raise RuntimeError("S3 업로드 실패")
            _set(job_id, status="done", mesh_url=mesh_url, error="")
            logger.info("[avatar] job %s done (%s, %s faces, %.1fs)",
                        job_id, res.get("organs"), res.get("faces"), res.get("seconds", 0))
        except Exception as e:  # noqa: BLE001
            logger.warning("[avatar] job %s failed: %s", job_id, str(e)[:200])
            _set(job_id, status="failed", error=str(e)[:300])
        finally:
            import shutil
            for p in (out_glb, cleanup_path):   # 다운로드/압축해제분만 정리(로컬 입력은 None)
                try:
                    if not p or not os.path.exists(p):
                        continue
                    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
                except Exception:  # noqa: BLE001
                    pass


def _parse_result(stdout):
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:  # noqa: BLE001
                continue
    return None


def stats() -> dict:
    conn = _db()
    try:
        by = {r["status"]: r["c"] for r in conn.execute(
            "SELECT status, count(*) c FROM avatar_jobs GROUP BY status").fetchall()}
        total = conn.execute("SELECT count(*) FROM avatar_jobs").fetchone()[0]
    finally:
        conn.close()
    return {"total": total, "by_status": by,
            "ts_python_available": os.path.exists(str(_cfg("ts_python")))}
