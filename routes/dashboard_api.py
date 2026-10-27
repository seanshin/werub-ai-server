"""
Unified Dashboard API — 외부 통합 관리 시스템용 집계 엔드포인트
v2.62.0

9개 엔드포인트로 8개 대시보드 데이터를 통합 제공:
  GET /api/dashboard/summary        전체 현황 요약
  GET /api/dashboard/server         단일 서버 상세
  GET /api/dashboard/servers        4서버 비교
  GET /api/dashboard/models         모델 + GPU
  GET /api/dashboard/queue          큐 상태 + 통계
  GET /api/dashboard/orchestration  워크플로우 + 실행 이력
  GET /api/dashboard/pipeline       약물 파이프라인
  GET /api/dashboard/quality        한국어 품질 메트릭
  GET /api/dashboard/stream         통합 SSE 스트림
"""

import hashlib
import json
import logging
import os
import queue as queue_mod
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

from flask import Blueprint, Response, jsonify, request, make_response

logger = logging.getLogger(__name__)

dashboard_api_bp = Blueprint("dashboard_api", __name__)

# ── Shared references (injected via init_dashboard_api) ──
_get_db = None
_ollama_session = None
_ollama_base = None
_get_ollama_status = None
_get_worker_alive = None
_get_queue_paused = None
_gpu_manager = None
_chat_queue = None
_base_dir = None
_version = "0.0.0"
_monitor_subscribers = None
_monitor_lock = None
_monitor_subscribe = None
_monitor_unsubscribe = None
_ollama_get = None

# Remote server URLs (same as system.py)
_GPU_STATS_URL = "http://127.0.0.1:9102"
_PROD_STATS_URL = "http://127.0.0.1:9100"
_HIS_STATS_URL = "http://127.0.0.1:9101"
_IMAC_OLLAMA_BASE = "http://localhost:8586"


def init_dashboard_api(*, get_db, ollama_session, ollama_base, get_ollama_status,
                       get_worker_alive, get_queue_paused, gpu_manager, chat_queue,
                       base_dir, version, monitor_subscribers, monitor_lock,
                       ollama_get, monitor_subscribe=None, monitor_unsubscribe=None):
    global _get_db, _ollama_session, _ollama_base, _get_ollama_status
    global _get_worker_alive, _get_queue_paused, _gpu_manager, _chat_queue
    global _base_dir, _version, _monitor_subscribers, _monitor_lock, _ollama_get
    global _monitor_subscribe, _monitor_unsubscribe
    _monitor_subscribe = monitor_subscribe
    _monitor_unsubscribe = monitor_unsubscribe
    _get_db = get_db
    _ollama_session = ollama_session
    _ollama_base = ollama_base
    _get_ollama_status = get_ollama_status
    _get_worker_alive = get_worker_alive
    _get_queue_paused = get_queue_paused
    _gpu_manager = gpu_manager
    _chat_queue = chat_queue
    _base_dir = base_dir
    _version = version
    _monitor_subscribers = monitor_subscribers
    _monitor_lock = monitor_lock
    _ollama_get = ollama_get


# ── TTL Cache (with ETag hash) ──
_cache = {}


def _cached(key, ttl_sec, fn):
    now = time.time()
    entry = _cache.get(key)
    if entry and now - entry["ts"] < ttl_sec:
        return entry["data"], entry["etag"]
    data = fn()
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    etag = hashlib.md5(raw.encode()).hexdigest()[:16]
    _cache[key] = {"data": data, "ts": now, "etag": etag}
    return data, etag


def _cached_noetag(key, ttl_sec, fn):
    """ETag 불필요한 내부 호출용"""
    data, _ = _cached(key, ttl_sec, fn)
    return data


# ── fields 필터링 ──
def _filter_fields(data, fields_csv):
    """?fields=cpu,ram,gpu → data에서 해당 키만 반환"""
    if not fields_csv or not isinstance(data, dict):
        return data
    keys = [k.strip() for k in fields_csv.split(",") if k.strip()]
    if not keys:
        return data
    return {k: v for k, v in data.items() if k in keys}


# ── Response Envelope (ETag + fields + Cache-Control) ──
def _envelope(endpoint, data, cache_ttl=0, start_time=None, etag=None):
    # fields 필터링
    fields = request.args.get("fields")
    if fields:
        data = _filter_fields(data, fields)

    # ETag 조건부 응답 — 304 Not Modified
    if etag:
        client_etag = request.headers.get("If-None-Match", "").strip('" ')
        if client_etag == etag:
            resp = make_response("", 304)
            resp.headers["ETag"] = f'"{etag}"'
            resp.headers["Cache-Control"] = f"max-age={cache_ttl}" if cache_ttl else "no-cache"
            return resp

    body = {
        "_meta": {
            "endpoint": endpoint,
            "version": _version,
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "duration_ms": round((time.time() - start_time) * 1000) if start_time else 0,
            "cache_ttl_sec": cache_ttl,
        },
        "data": data,
    }
    resp = jsonify(body)
    if etag:
        resp.headers["ETag"] = f'"{etag}"'
    if cache_ttl:
        resp.headers["Cache-Control"] = f"public, max-age={cache_ttl}"
    else:
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ── Internal Data Fetchers ──

def _fetch_health():
    """health_check 로직 (system.py와 동일)"""
    checks = {}
    healthy = True

    ollama_ok = _get_ollama_status()
    checks["ollama"] = {"ok": ollama_ok}
    if not ollama_ok:
        healthy = False

    worker_alive = _get_worker_alive()
    checks["worker"] = {"ok": worker_alive, "paused": _get_queue_paused()}
    if not worker_alive:
        healthy = False

    conn = _get_db()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
        checks["queue"] = {"pending": pending, "running": running, "ok": pending < 100}

        disk = shutil.disk_usage("/")
        disk_pct = round(disk.used / disk.total * 100, 1)
        checks["disk"] = {"used_pct": disk_pct, "free_gb": round(disk.free / 1024**3, 1), "ok": disk_pct < 90}

        db_size_mb = round(os.path.getsize(os.path.join(_base_dir, "queue.db")) / 1024 / 1024, 1)
        checks["database"] = {"size_mb": db_size_mb, "ok": db_size_mb < 500}

        # Tunnel
        tunnel_ok = False
        try:
            r = subprocess.run(["pgrep", "-f", "autossh.*8585"], capture_output=True, timeout=3)
            if r.returncode == 0:
                tunnel_ok = True
            else:
                r2 = subprocess.run(["pgrep", "-f", "ssh.*-R.*8585"], capture_output=True, timeout=3)
                tunnel_ok = r2.returncode == 0
        except Exception as e:
            logger.debug("tunnel check failed: %s", e)
        checks["tunnel"] = {"ok": tunnel_ok}
    finally:
        conn.close()

    return {"healthy": healthy, "checks": checks}


def _fetch_server_stats():
    """server_stats 로직 — CPU/RAM/GPU/Disk/Uptime"""
    stats = {}

    # CPU
    try:
        with open("/proc/loadavg") as f:
            la = f.read().split()
        cores = os.cpu_count() or 1
        stats["cpu"] = {
            "load_1m": float(la[0]), "load_5m": float(la[1]), "load_15m": float(la[2]),
            "cores": cores, "usage_pct": round(float(la[0]) / cores * 100, 1),
        }
    except Exception:
        stats["cpu"] = {"cores": os.cpu_count() or 1, "usage_pct": 0}

    # RAM
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get("MemTotal", 0) / 1024
            avail = mem.get("MemAvailable", 0) / 1024
            used = total - avail
            stats["ram"] = {"total_mb": round(total), "used_mb": round(used),
                            "avail_mb": round(avail), "usage_pct": round(used / max(total, 1) * 100, 1)}
    except Exception:
        try:
            import psutil
            vm = psutil.virtual_memory()
            stats["ram"] = {"total_mb": round(vm.total/1024/1024), "used_mb": round(vm.used/1024/1024),
                            "avail_mb": round(vm.available/1024/1024), "usage_pct": vm.percent}
        except Exception:
            stats["ram"] = {"total_mb": 0, "usage_pct": 0}

    # GPU
    try:
        if shutil.which("nvidia-smi"):
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                stats["gpu"] = {
                    "name": parts[0], "utilization_pct": int(parts[1]),
                    "memory_used_mb": int(parts[2]), "memory_total_mb": int(parts[3]),
                    "memory_usage_pct": round(int(parts[2]) / max(int(parts[3]), 1) * 100, 1),
                    "temperature_c": int(parts[4]),
                }
    except Exception as e:
        logger.debug("gpu stats collection failed: %s", e)
    if "gpu" not in stats:
        stats["gpu"] = None

    # Disk
    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks / (1024**3)
        free = st.f_frsize * st.f_bavail / (1024**3)
        used = total - free
        stats["disk"] = {"total_gb": round(total, 1), "used_gb": round(used, 1),
                         "free_gb": round(free, 1), "usage_pct": round(used / max(total, 1) * 100, 1)}
    except Exception:
        stats["disk"] = {"total_gb": 0, "usage_pct": 0}

    # Uptime
    try:
        with open("/proc/uptime") as f:
            sec = int(float(f.read().split()[0]))
            d, rem = divmod(sec, 86400)
            h, rem = divmod(rem, 3600)
            m = rem // 60
            stats["uptime"] = {"seconds": sec, "display": f"{d}d {h}h {m}m"}
    except Exception:
        stats["uptime"] = {"seconds": 0, "display": "unknown"}

    # Processes (compact — top 10)
    compact = request.args.get("compact") == "true"
    if not compact:
        try:
            r = subprocess.run(["ps", "aux", "--sort=-pcpu"], capture_output=True, text=True, timeout=5)
            procs = []
            for line in r.stdout.strip().split("\n")[1:11]:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    cpu = float(parts[2])
                    mem = float(parts[3])
                    if cpu < 0.1 and mem < 0.5:
                        continue
                    cmd = parts[10][:80]
                    if "ps aux" in cmd:
                        continue
                    procs.append({"pid": int(parts[1]), "cpu": cpu, "mem": mem, "command": cmd})
            stats["processes"] = procs
        except Exception:
            stats["processes"] = []

    return stats


def _fetch_remote_server(base_url, timeout=5):
    """원격 서버의 stats + health 동시 조회"""
    result = {"reachable": False}
    try:
        req = urllib.request.Request(f"{base_url}/api/server/stats")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["stats"] = json.loads(resp.read())
        result["reachable"] = True
    except Exception as e:
        result["error"] = str(e)

    try:
        req = urllib.request.Request(f"{base_url}/api/health")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["health"] = json.loads(resp.read())
    except Exception:
        result["health"] = {"healthy": False}

    return result


def _fetch_ollama_data():
    """Ollama 상태 + 모델 목록"""
    running = _get_ollama_status()
    models = _ollama_get("/api/tags") if running else {}
    ps = _ollama_get("/api/ps") if running else {}
    return {
        "running": running,
        "available_models": models.get("models", []),
        "running_models": ps.get("models", []),
    }


def _fetch_gpu_status():
    """GPU 리소스 매니저 상태"""
    gpu_info = {}
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"], timeout=5).decode().strip().split(", ")
        gpu_info = {"vram_used_mb": int(out[0]), "vram_total_mb": int(out[1]),
                    "gpu_util_pct": int(out[2]), "temp_c": int(out[3])}
    except Exception as e:
        logger.debug("gpu status fetch failed: %s", e)
    return {
        "gpu": gpu_info,
        "resource_manager": _gpu_manager.status() if _gpu_manager else {},
        "chat_queue": _chat_queue.stats() if _chat_queue else {},
    }


def _fetch_queue_status():
    """큐 상태"""
    conn = _get_db()
    try:
        counts = {}
        for status in ["pending", "running", "completed", "failed"]:
            counts[status] = conn.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (status,)).fetchone()[0]
        llm_pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND (job_type IS NULL OR job_type='llm')").fetchone()[0]
        img_pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending' AND job_type='image'").fetchone()[0]
    finally:
        conn.close()
    return {**counts, "llm_pending": llm_pending, "image_pending": img_pending,
            "worker_alive": _get_worker_alive(), "paused": _get_queue_paused()}


def _fetch_queue_stats():
    """큐 성능 통계"""
    conn = _get_db()
    try:
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   COALESCE(SUM(tokens),0) as total_tokens,
                   COALESCE(SUM(duration_ms),0) as total_duration_ms,
                   COALESCE(AVG(tokens),0) as avg_tokens,
                   COALESCE(AVG(duration_ms),0) as avg_duration_ms
            FROM jobs WHERE status='completed' AND tokens > 0
        """).fetchone()
        stats = dict(row)
        stats["avg_tokens"] = round(stats["avg_tokens"], 1)
        stats["avg_duration_ms"] = round(stats["avg_duration_ms"], 1)
        stats["avg_tok_per_sec"] = round(stats["total_tokens"] / (stats["total_duration_ms"] / 1000), 1) if stats["total_duration_ms"] > 0 else 0

        model_rows = conn.execute("""
            SELECT model, COUNT(*) as count, COALESCE(SUM(tokens),0) as tokens,
                   COALESCE(AVG(tokens),0) as avg_tokens,
                   COALESCE(AVG(duration_ms),0) as avg_duration_ms,
                   COALESCE(SUM(duration_ms),0) as total_duration_ms
            FROM jobs WHERE status='completed' AND tokens > 0 GROUP BY model ORDER BY count DESC
        """).fetchall()
        stats["jobs_per_model"] = {}
        for mr in model_rows:
            d = dict(mr)
            d["avg_tokens"] = round(d["avg_tokens"], 1)
            d["avg_duration_ms"] = round(d["avg_duration_ms"], 1)
            d["tok_per_sec"] = round(d["tokens"] / (d["total_duration_ms"] / 1000), 1) if d["total_duration_ms"] > 0 else 0
            stats["jobs_per_model"][d["model"]] = d

        # ETA
        pending_models = conn.execute(
            "SELECT model, COUNT(*) as cnt FROM jobs WHERE status IN ('pending','running') GROUP BY model"
        ).fetchall()
        eta_ms = 0
        for pm in pending_models:
            m = dict(pm)
            model_stat = stats["jobs_per_model"].get(m["model"])
            avg_ms = model_stat["avg_duration_ms"] if model_stat else 10000
            eta_ms += int(avg_ms * m["cnt"])
        stats["eta_sec"] = round(eta_ms / 1000)

        # Recent 20 jobs
        include_recent = request.args.get("include", "")
        if "recent_jobs" in include_recent:
            recent = conn.execute("""
                SELECT id, status, model, prompt, error, tokens, duration_ms, priority, created_at, completed_at, job_type
                FROM jobs ORDER BY created_at DESC LIMIT 20
            """).fetchall()
            stats["recent_jobs"] = [dict(r) for r in recent]
    finally:
        conn.close()
    return stats


def _fetch_orchestration():
    """워크플로우 + 실행 이력"""
    # Import orchestrator internals
    try:
        from routes.orchestrator import _get_all_workflows, _get_orch_db
    except ImportError:
        return {"workflows": [], "history": []}

    wfs = []
    for name, wf in _get_all_workflows().items():
        wfs.append({
            "name": name,
            "display_name": wf["name"],
            "description": wf["description"],
            "steps": len(wf["steps"]),
        })

    limit = min(int(request.args.get("limit", 20)), 100)
    db = _get_orch_db()
    rows = db.execute(
        "SELECT id, workflow, status, steps_completed, steps_total, total_duration_ms, created_at "
        "FROM orchestrator_runs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()

    active = [dict(r) for r in rows if r["status"] == "running"]

    return {
        "workflows": wfs,
        "workflows_count": len(wfs),
        "active_runs": active,
        "history": [dict(r) for r in rows],
    }


def _fetch_pipeline_status():
    """약물 파이프라인 상태"""
    conn = _get_db()
    try:
        current = conn.execute(
            "SELECT * FROM batch_run WHERE status='running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

        progress = None
        if current:
            counts = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM staging_drug WHERE batch_run_id=? GROUP BY status",
                [current["id"]]
            ).fetchall()
            progress = {r["status"]: r["cnt"] for r in counts}

        drug_count = conn.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
        inter_count = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
        denial_count = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]
    except sqlite3.OperationalError:
        # Drug pipeline 테이블이 아직 생성되지 않은 경우
        return {"current_run": None, "progress": None, "active_mode": "day",
                "db_summary": {"drug_codes": 0, "drug_interactions": 0, "denial_reasons": 0}}
    finally:
        conn.close()

    # Night mode
    try:
        from core.drug_batch_scheduler import get_active_mode, SCHEDULES, NIGHT_MODE
        active_mode = get_active_mode()
    except (ImportError, Exception):
        active_mode = "day"

    return {
        "current_run": dict(current) if current else None,
        "progress": progress,
        "active_mode": active_mode,
        "db_summary": {
            "drug_codes": drug_count,
            "drug_interactions": inter_count,
            "denial_reasons": denial_count,
        },
    }


def _fetch_pipeline_stats():
    """약물 데이터 통계"""
    conn = _get_db()
    try:
        drug_count = conn.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
        inter_count = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
        denial_count = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]

        source_dist = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM drug_codes GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        avg_conf = conn.execute("SELECT AVG(confidence) FROM drug_codes").fetchone()[0] or 0

        recent_runs = conn.execute(
            "SELECT id, run_type, status, total_committed, started_at FROM batch_run ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"drug_codes": {"total": 0}, "drug_interactions": {"total": 0},
                "denial_reasons": {"total": 0}, "by_source": {}, "avg_confidence": 0, "recent_runs": []}
    finally:
        conn.close()

    return {
        "drug_codes": {"total": drug_count},
        "drug_interactions": {"total": inter_count},
        "denial_reasons": {"total": denial_count},
        "by_source": {r["source"]: r["cnt"] for r in source_dist},
        "avg_confidence": round(avg_conf, 4),
        "recent_runs": [dict(r) for r in recent_runs],
    }


# ══════════════════════════════════════════════════
# API Endpoints
# ══════════════════════════════════════════════════

@dashboard_api_bp.route("/api/dashboard/summary")
def dashboard_summary():
    """전체 현황 요약 — 외부 시스템이 최초 호출하는 엔드포인트
    ---
    tags: [Dashboard]
    responses:
      200:
        description: 서버/모델/큐/파이프라인 전체 요약
    """
    t0 = time.time()

    def _get():
        health = _cached_noetag("health", 10, _fetch_health)
        ollama = _cached_noetag("ollama", 10, _fetch_ollama_data)
        q_status = _cached_noetag("queue_status", 3, _fetch_queue_status)
        server_stats = _cached_noetag("server_stats", 5, _fetch_server_stats)

        # Orchestration count
        try:
            from routes.orchestrator import _get_all_workflows, _get_orch_db
            wf_count = len(_get_all_workflows())
            db = _get_orch_db()
            active_runs = db.execute("SELECT COUNT(*) FROM orchestrator_runs WHERE status='running'").fetchone()[0]
            recent_24h = db.execute(
                "SELECT COUNT(*) FROM orchestrator_runs WHERE created_at > datetime('now', '-1 day')"
            ).fetchone()[0]
        except Exception:
            wf_count = 0
            active_runs = 0
            recent_24h = 0

        # Pipeline summary
        pipeline = _cached_noetag("pipeline_status", 60, _fetch_pipeline_status)

        # Remote servers reachability (parallel, short timeout)
        servers_ok = {}
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                def _check_server(url):
                    urllib.request.urlopen(urllib.request.Request(f"{url}/api/health"), timeout=3)
                    return True
                futures = {
                    pool.submit(_check_server, url): name
                    for name, url in [("gpu", _GPU_STATS_URL), ("production", _PROD_STATS_URL), ("his_pacs", _HIS_STATS_URL)]
                }
                for f in as_completed(futures, timeout=5):
                    try:
                        f.result()
                        servers_ok[futures[f]] = True
                    except Exception:
                        servers_ok[futures[f]] = False
        except Exception as e:
            logger.debug("server status check wrapper failed: %s", e)

        return {
            "server": {
                "healthy": health["healthy"],
                "cpu_pct": server_stats.get("cpu", {}).get("usage_pct", 0),
                "ram_pct": server_stats.get("ram", {}).get("usage_pct", 0),
                "gpu": server_stats.get("gpu"),
                "disk_pct": server_stats.get("disk", {}).get("usage_pct", 0),
            },
            "ollama": {
                "running": ollama["running"],
                "loaded_models": len(ollama.get("running_models", [])),
                "available_models": len(ollama.get("available_models", [])),
            },
            "queue": {
                "pending": q_status.get("pending", 0),
                "running": q_status.get("running", 0),
                "completed": q_status.get("completed", 0),
                "failed": q_status.get("failed", 0),
                "worker_alive": q_status.get("worker_alive", False),
                "paused": q_status.get("paused", False),
            },
            "orchestration": {
                "workflows_count": wf_count,
                "active_runs": active_runs,
                "recent_runs_24h": recent_24h,
            },
            "pipeline": {
                "active_run": pipeline.get("current_run") is not None,
                "active_mode": pipeline.get("active_mode", "day"),
                "db_summary": pipeline.get("db_summary", {}),
            },
            "services": {k: v["ok"] for k, v in health["checks"].items() if "ok" in v},
            "servers_reachable": servers_ok,
            "korean_quality": {
                "engine_version": "v2.0",
                "source": "im-not-ai (KatFish baseline)",
                "s1_pattern_count": 18,
                "s2_pattern_count": 12,
                "metric_count": 22,
                "genres_supported": ["essay", "column", "report", "blog", "medical", "chat"],
                "auto_regeneration": True,
            },
        }

    data, etag = _cached("summary", 10, _get)
    return _envelope("/api/dashboard/summary", data, cache_ttl=10, start_time=t0, etag=etag)


@dashboard_api_bp.route("/api/dashboard/server")
def dashboard_server():
    """단일 서버 상세 (CPU/GPU/RAM/디스크/프로세스)
    ---
    tags: [Dashboard]
    parameters:
      - {name: compact, in: query, type: string, enum: ["true","false"], default: "false"}
    responses:
      200:
        description: 서버 리소스 상세
    """
    t0 = time.time()
    stats = _cached_noetag("server_stats", 5, _fetch_server_stats)
    health = _cached_noetag("health", 10, _fetch_health)
    ollama = _cached_noetag("ollama", 10, _fetch_ollama_data)

    data = {
        "stats": stats,
        "health": health,
        "ollama": ollama,
    }

    # Optional: image/comfyui status
    try:
        from routes.image import _get_comfyui_status
        data["image"] = {"comfyui_available": _get_comfyui_status()}
    except Exception:
        data["image"] = {"comfyui_available": False}

    # ETag for composed data
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
    etag = hashlib.md5(raw.encode()).hexdigest()[:16]
    return _envelope("/api/dashboard/server", data, cache_ttl=5, start_time=t0, etag=etag)


@dashboard_api_bp.route("/api/dashboard/servers")
def dashboard_servers():
    """4서버 비교 — 병렬 조회
    ---
    tags: [Dashboard]
    parameters:
      - {name: servers, in: query, type: string, default: "local,gpu,prod,his,imac",
         description: "comma-separated server names"}
    responses:
      200:
        description: 멀티서버 상태
    """
    t0 = time.time()
    requested = request.args.get("servers", "local,gpu,prod,his,imac").split(",")

    server_map = {
        "gpu": _GPU_STATS_URL,
        "prod": _PROD_STATS_URL,
        "his": _HIS_STATS_URL,
        "imac": _IMAC_OLLAMA_BASE,
    }

    results = {}

    # Local server
    if "local" in requested:
        results["local"] = {
            "stats": _cached_noetag("server_stats", 5, _fetch_server_stats),
            "health": _cached_noetag("health", 10, _fetch_health),
            "reachable": True,
        }

    # Remote servers (parallel)
    remote_names = [n for n in requested if n in server_map]
    if remote_names:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_remote_server, server_map[n], 5): n for n in remote_names}
            for f in as_completed(futures, timeout=8):
                name = futures[f]
                try:
                    results[name] = f.result()
                except Exception as e:
                    results[name] = {"reachable": False, "error": str(e)}

    return _envelope("/api/dashboard/servers", results, cache_ttl=15, start_time=t0)


@dashboard_api_bp.route("/api/dashboard/models")
def dashboard_models():
    """모델 목록 + GPU 상태
    ---
    tags: [Dashboard]
    responses:
      200:
        description: Ollama 모델 + GPU 리소스
    """
    t0 = time.time()
    ollama = _cached_noetag("ollama", 30, _fetch_ollama_data)
    gpu = _cached_noetag("gpu_status", 30, _fetch_gpu_status)
    data = {**ollama, **gpu}
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    etag = hashlib.md5(raw.encode()).hexdigest()[:16]
    return _envelope("/api/dashboard/models", data, cache_ttl=30, start_time=t0, etag=etag)


@dashboard_api_bp.route("/api/dashboard/queue")
def dashboard_queue():
    """큐 상태 + 통계
    ---
    tags: [Dashboard]
    parameters:
      - {name: include, in: query, type: string, description: "recent_jobs"}
    responses:
      200:
        description: 큐 상태 및 성능 통계
    """
    t0 = time.time()
    status = _cached_noetag("queue_status", 3, _fetch_queue_status)
    stats = _fetch_queue_stats()
    data = {"status": status, "stats": stats}
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    etag = hashlib.md5(raw.encode()).hexdigest()[:16]
    return _envelope("/api/dashboard/queue", data, cache_ttl=3, start_time=t0, etag=etag)


@dashboard_api_bp.route("/api/dashboard/orchestration")
def dashboard_orchestration():
    """워크플로우 목록 + 실행 이력
    ---
    tags: [Dashboard]
    parameters:
      - {name: limit, in: query, type: integer, default: 20}
    responses:
      200:
        description: 오케스트레이션 현황
    """
    t0 = time.time()
    data, etag = _cached("orchestration", 30, _fetch_orchestration)
    return _envelope("/api/dashboard/orchestration", data, cache_ttl=30, start_time=t0, etag=etag)


@dashboard_api_bp.route("/api/dashboard/pipeline")
def dashboard_pipeline():
    """약물 파이프라인 상태 + 통계
    ---
    tags: [Dashboard]
    responses:
      200:
        description: Drug Pipeline 현황
    """
    t0 = time.time()
    status = _cached_noetag("pipeline_status", 60, _fetch_pipeline_status)
    stats = _cached_noetag("pipeline_stats", 60, _fetch_pipeline_stats)

    # Schedule
    try:
        from core.drug_batch_scheduler import SCHEDULES, NIGHT_MODE, DAY_MODE, get_active_mode
        schedule = {"schedules": SCHEDULES, "night_mode": NIGHT_MODE, "active": get_active_mode()}
    except ImportError:
        schedule = {}

    data = {"status": status, "stats": stats, "schedule": schedule}
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    etag = hashlib.md5(raw.encode()).hexdigest()[:16]
    return _envelope("/api/dashboard/pipeline", data, cache_ttl=60, start_time=t0, etag=etag)


@dashboard_api_bp.route("/api/dashboard/quality")
def dashboard_quality():
    """한국어 품질 메트릭 현황 — S1/S2 규칙, 장르 설정, baseline 정보
    ---
    tags: [Dashboard]
    responses:
      200:
        description: 한국어 자연화 품질 시스템 현황
    """
    t0 = time.time()

    def _get():
        try:
            rules_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "core", "data", "humanize_rules.json")
            baseline_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "core", "data", "baseline.json")
            rules = {}
            baseline_info = {}
            if os.path.exists(rules_path):
                with open(rules_path, "r", encoding="utf-8") as f:
                    rules = json.load(f)
            if os.path.exists(baseline_path):
                with open(baseline_path, "r", encoding="utf-8") as f:
                    bl = json.load(f)
                    baseline_info = {
                        "version": bl.get("version"),
                        "source": bl.get("source"),
                        "genres_available": [g for g, v in bl.get("genres", {}).items() if v is not None],
                        "strongest_signal": {
                            "metric": "ending_comma_rate",
                            "ratio": 4.84,
                            "description": "연결어미 뒤 쉼표 (KatFish)",
                        },
                    }
            return {
                "engine_version": rules.get("version", "v2.0"),
                "source": rules.get("source", "epoko77-ai/im-not-ai"),
                "s1_patterns": len(rules.get("s1_patterns", [])),
                "s2_patterns": len(rules.get("s2_key_patterns", [])),
                "do_not_touch": rules.get("do_not_touch", []),
                "genre_config": rules.get("genre_config", {}),
                "change_rate_guard": rules.get("change_rate_guard", {}),
                "baseline": baseline_info,
                "integration_points": [
                    "story_generate", "story_continue",
                    "novel_chapter_write", "novel_chapter_rewrite",
                    "novel_scene_handler(5종)", "novel_revise_polish",
                    "novel_chapter_expand", "novel_chapter_condense",
                    "writing_draft", "writing_polish",
                    "chat_non_streaming",
                    "medical_summarize", "medical_explain",
                    "medical_research", "patient_education",
                    "/api/korean/* (11 API)",
                ],
                "auto_regeneration": {
                    "trigger": "risk_band == high",
                    "action": "S1 규칙 삽입 후 1회 재생성",
                    "max_retries": 1,
                },
            }
        except Exception as e:
            return {"error": str(e)}

    data, etag = _cached("quality", 300, _get)
    return _envelope("/api/dashboard/quality", data, cache_ttl=300, start_time=t0, etag=etag)


@dashboard_api_bp.route("/api/dashboard/stream")
def dashboard_stream():
    """통합 SSE 스트림 — 실시간 이벤트 (monitor + 서버 stats)
    ---
    tags: [Dashboard]
    parameters:
      - {name: token, in: query, type: string, description: "Bearer token (SSE용)"}
    responses:
      200:
        description: SSE event stream
    """
    q = queue_mod.Queue(maxsize=200)

    # 공유 헬퍼로 등록 (전역 상한 + IP당 1개 축출). 헬퍼 없으면 구버전 폴백.
    sub = None
    if _monitor_subscribe is not None:
        sub = _monitor_subscribe(q)
        if sub is None:
            return jsonify({"error": "monitor capacity reached"}), 503
    elif _monitor_subscribers is not None and _monitor_lock is not None:
        with _monitor_lock:
            _monitor_subscribers.append(q)

    def generate():
        yield f"data: {json.dumps({'type': 'connected', 'version': _version})}\n\n"
        stats_counter = 0
        try:
            while True:
                if sub is not None and sub.close.is_set():
                    break
                try:
                    msg = q.get(timeout=10)
                    if msg is None:        # 종료 신호 (IP 축출)
                        break
                    yield msg
                except queue_mod.Empty:
                    # keepalive + periodic stats
                    stats_counter += 1
                    if stats_counter % 1 == 0:  # 매 10초마다 stats 전송
                        try:
                            brief = _cached_noetag("server_stats", 5, _fetch_server_stats)
                            stats_event = {
                                "domain": "server", "type": "stats",
                                "ts": datetime.now(timezone.utc).isoformat() + "Z",
                                "cpu_pct": brief.get("cpu", {}).get("usage_pct", 0),
                                "ram_pct": brief.get("ram", {}).get("usage_pct", 0),
                                "gpu": brief.get("gpu"),
                                "disk_pct": brief.get("disk", {}).get("usage_pct", 0),
                            }
                            yield f"data: {json.dumps(stats_event, ensure_ascii=False)}\n\n"
                        except Exception:
                            yield ": keepalive\n\n"
                    else:
                        yield ": keepalive\n\n"
        finally:
            if sub is not None and _monitor_unsubscribe is not None:
                _monitor_unsubscribe(sub)
            elif _monitor_subscribers is not None and _monitor_lock is not None:
                with _monitor_lock:
                    try:
                        _monitor_subscribers.remove(q)
                    except ValueError:
                        pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
