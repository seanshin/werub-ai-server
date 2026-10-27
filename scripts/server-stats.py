#!/usr/bin/env python3
"""
WeRU.B Server Stats — 경량 독립 모니터링 에이전트
원격 서버(HIS/PACS, Production 등)에 배포하여 /api/server/stats, /api/health 제공.
메인 AI 서버에서 SSH 터널을 통해 프록시로 접근.

Usage:
    python3 server-stats.py              # 기본 포트 8585
    python3 server-stats.py --port 9200  # 커스텀 포트
    python3 server-stats.py --host 0.0.0.0  # 외부 접근 허용

Requirements:
    pip install flask
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
from datetime import datetime

from flask import Flask, jsonify

app = Flask(__name__)

IS_MAC = platform.system() == "Darwin"  # Apple Silicon(iMac) 지원 — Linux 경로는 불변


def _mac_sysctl(key):
    try:
        r = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _mac_memory_pressure():
    """macOS memory_pressure → (free_pct, level). level: normal|warn|critical"""
    try:
        r = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=5)
        free_pct = None
        for line in r.stdout.splitlines():
            if "free percentage" in line:
                free_pct = float(line.split(":")[1].strip().rstrip("%"))
        lvl = _mac_sysctl("kern.memorystatus_vm_pressure_level")
        level = {"1": "normal", "2": "warn", "4": "critical"}.get(lvl, "normal")
        return free_pct, level
    except Exception:
        return None, "unknown"

# ── CPU 사용률 계산용 이전 /proc/stat 값 ──
_prev_cpu_stat = None
_prev_cpu_cores = {}


def _read_cpu_stat():
    """Read /proc/stat and return (idle, total) jiffies."""
    with open("/proc/stat") as f:
        line = f.readline()
    parts = line.split()[1:]
    vals = [int(v) for v in parts]
    idle = vals[3] + vals[4]
    total = sum(vals)
    return idle, total


def _read_cpu_cores():
    """Read per-core CPU stats from /proc/stat."""
    cores = {}
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("cpu") and line[3] != " ":
                parts = line.split()
                cid = int(parts[0][3:])
                vals = [int(v) for v in parts[1:]]
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                cores[cid] = (idle, sum(vals))
    return cores


@app.route("/api/server/stats")
def server_stats():
    global _prev_cpu_stat, _prev_cpu_cores
    stats = {}

    # CPU
    try:
        if IS_MAC:
            la = os.getloadavg()
            cores = os.cpu_count() or 1
            stats["cpu"] = {
                "load_1m": round(la[0], 2), "load_5m": round(la[1], 2), "load_15m": round(la[2], 2),
                "cores": cores,
                "usage_pct": round(min(la[0] / cores * 100, 100), 1),  # loadavg 근사 (mac은 /proc 없음)
                "per_core": [],
            }
        else:
            with open("/proc/loadavg") as f:
                la_parts = f.read().split()
            cores = os.cpu_count() or 1

            idle, total = _read_cpu_stat()
            usage_pct = 0.0
            if _prev_cpu_stat is not None:
                d_idle = idle - _prev_cpu_stat[0]
                d_total = total - _prev_cpu_stat[1]
                if d_total > 0:
                    usage_pct = round((1.0 - d_idle / d_total) * 100, 1)
            _prev_cpu_stat = (idle, total)

            cur_cores = _read_cpu_cores()
            core_pcts = []
            if _prev_cpu_cores:
                for cid in sorted(cur_cores.keys()):
                    if cid in _prev_cpu_cores:
                        pi, pt = _prev_cpu_cores[cid]
                        ci, ct = cur_cores[cid]
                        dt = ct - pt
                        core_pcts.append(round((1.0 - (ci - pi) / dt) * 100, 1) if dt > 0 else 0)
            _prev_cpu_cores = cur_cores

            stats["cpu"] = {
                "load_1m": float(la_parts[0]),
                "load_5m": float(la_parts[1]),
                "load_15m": float(la_parts[2]),
                "cores": cores,
                "usage_pct": max(0, usage_pct),
                "per_core": core_pcts,
            }
    except Exception:
        stats["cpu"] = {"load_1m": 0, "cores": 1, "usage_pct": 0}

    # RAM
    try:
        if IS_MAC:
            total_mb = int(_mac_sysctl("hw.memsize") or 0) / 1024 / 1024
            free_pct, _ = _mac_memory_pressure()
            avail_mb = total_mb * (free_pct or 0) / 100
            used_mb = total_mb - avail_mb
            stats["ram"] = {
                "total_mb": round(total_mb),
                "used_mb": round(used_mb),
                "avail_mb": round(avail_mb),
                "usage_pct": round(used_mb / max(total_mb, 1) * 100, 1),
            }
        else:
            with open("/proc/meminfo") as f:
                mem = {}
                for line in f:
                    k, v = line.split(":")
                    mem[k.strip()] = int(v.strip().split()[0])
                total_mb = mem.get("MemTotal", 0) / 1024
                avail_mb = mem.get("MemAvailable", 0) / 1024
                used_mb = total_mb - avail_mb
                stats["ram"] = {
                    "total_mb": round(total_mb),
                    "used_mb": round(used_mb),
                    "avail_mb": round(avail_mb),
                    "usage_pct": round(used_mb / max(total_mb, 1) * 100, 1),
                }
    except Exception:
        stats["ram"] = {"total_mb": 0, "usage_pct": 0}

    # GPU (NVIDIA)
    try:
        if shutil.which("nvidia-smi"):
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                stats["gpu"] = {
                    "name": parts[0],
                    "utilization_pct": int(parts[1]),
                    "memory_used_mb": int(parts[2]),
                    "memory_total_mb": int(parts[3]),
                    "memory_usage_pct": round(int(parts[2]) / max(int(parts[3]), 1) * 100, 1),
                    "temperature_c": int(parts[4]),
                }
    except Exception:
        pass
    if "gpu" not in stats:
        stats["gpu"] = None

    # Apple Silicon 전용 메트릭 (apple-silicon-worker Phase 0-6)
    if IS_MAC:
        try:
            free_pct, pressure_level = _mac_memory_pressure()
            # thermal: pmset -g therm → CPU_Speed_Limit (100=정상, <100=스로틀링)
            therm_limit = None
            r = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                if "CPU_Speed_Limit" in line:
                    therm_limit = int(line.split("=")[1].strip())
            # swap: sysctl vm.swapusage → "total = 0.00M  used = 0.00M  free = 0.00M"
            swap_used_mb = None
            sw = _mac_sysctl("vm.swapusage")
            if sw and "used =" in sw:
                swap_used_mb = round(float(sw.split("used =")[1].split("M")[0].strip()), 1)
            stats["apple_silicon"] = {
                "chip": _mac_sysctl("machdep.cpu.brand_string"),
                "memory_pressure": pressure_level,        # normal | warn | critical
                "memory_free_pct": free_pct,
                "thermal_cpu_speed_limit": therm_limit,   # 100 = 정상
                "swap_used_mb": swap_used_mb,
            }
        except Exception:
            stats["apple_silicon"] = {"memory_pressure": "unknown"}

    # Disk
    try:
        st = os.statvfs("/")
        total_gb = st.f_frsize * st.f_blocks / (1024**3)
        free_gb = st.f_frsize * st.f_bavail / (1024**3)
        used_gb = total_gb - free_gb
        stats["disk"] = {
            "total_gb": round(total_gb, 1),
            "used_gb": round(used_gb, 1),
            "free_gb": round(free_gb, 1),
            "usage_pct": round(used_gb / max(total_gb, 1) * 100, 1),
        }
    except Exception:
        stats["disk"] = {"total_gb": 0, "usage_pct": 0}

    # Uptime
    try:
        if IS_MAC:
            import time as _time
            boot = _mac_sysctl("kern.boottime")  # "{ sec = 1717..., usec = ... } ..."
            boot_sec = int(boot.split("sec =")[1].split(",")[0].strip())
            uptime_sec = int(_time.time()) - boot_sec
        else:
            with open("/proc/uptime") as f:
                uptime_sec = int(float(f.read().split()[0]))
        days, rem = divmod(uptime_sec, 86400)
        hours, rem = divmod(rem, 3600)
        mins = rem // 60
        stats["uptime"] = {"seconds": uptime_sec, "display": f"{days}d {hours}h {mins}m"}
    except Exception:
        stats["uptime"] = {"seconds": 0, "display": "unknown"}

    # Processes (top CPU/MEM)
    try:
        if IS_MAC:
            # macOS ps는 --sort 미지원 → -r(CPU 내림차순) 사용
            r = subprocess.run(["ps", "aux", "-r"], capture_output=True, text=True, timeout=5)
        else:
            r = subprocess.run(
                ["ps", "aux", "--sort=-pcpu"],
                capture_output=True, text=True, timeout=5,
            )
        procs = []
        for line in r.stdout.strip().split("\n")[1:21]:
            parts = line.split(None, 10)
            if len(parts) >= 11:
                cpu_pct = float(parts[2])
                mem_pct = float(parts[3])
                if cpu_pct < 0.1 and mem_pct < 0.5:
                    continue
                cmd = parts[10][:80]
                if "ps aux" in cmd or "--sort=-pcpu" in cmd:
                    continue
                procs.append({
                    "user": parts[0],
                    "pid": int(parts[1]),
                    "cpu": cpu_pct,
                    "mem": mem_pct,
                    "vsz_mb": round(int(parts[4]) / 1024),
                    "rss_mb": round(int(parts[5]) / 1024),
                    "stat": parts[7],
                    "command": cmd,
                })
        stats["processes"] = procs
        if procs:
            proc_cpu_sum = sum(p["cpu"] for p in procs)
            stats["cpu"]["proc_sum_pct"] = round(proc_cpu_sum, 1)
            stats["cpu"]["peak_proc_pct"] = round(max(p["cpu"] for p in procs), 1)
            proc_mem_sum = sum(p["mem"] for p in procs)
            stats["ram"]["proc_sum_pct"] = round(proc_mem_sum, 1)
            stats["ram"]["peak_proc_pct"] = round(max(p["mem"] for p in procs), 1)
    except Exception:
        stats["processes"] = []

    return jsonify(stats)


@app.route("/api/health")
def health_check():
    checks = {}
    healthy = True

    # Disk
    try:
        disk = shutil.disk_usage("/")
        disk_pct = round(disk.used / disk.total * 100, 1)
        checks["disk"] = {"used_pct": disk_pct, "free_gb": round(disk.free / 1024**3, 1), "ok": disk_pct < 90}
        if disk_pct >= 90:
            healthy = False
    except Exception:
        checks["disk"] = {"ok": False}

    # Ollama (optional — check if running)
    try:
        r = subprocess.run(["pgrep", "-f", "ollama"], capture_output=True, timeout=3)
        ollama_ok = r.returncode == 0
        checks["ollama"] = {"ok": ollama_ok, "detail": "running" if ollama_ok else "not running"}
    except Exception:
        checks["ollama"] = {"ok": False, "detail": "check failed"}

    # SSH Tunnel
    try:
        r = subprocess.run(["pgrep", "-f", "ssh.*-[RL]"], capture_output=True, timeout=3)
        tunnel_ok = r.returncode == 0
        checks["tunnel"] = {"ok": tunnel_ok, "detail": "running" if tunnel_ok else "no tunnel"}
    except Exception:
        checks["tunnel"] = {"ok": False}

    # Database (check if queue.db exists nearby)
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue.db")
    if os.path.exists(db_path):
        db_size_mb = round(os.path.getsize(db_path) / 1024 / 1024, 1)
        checks["database"] = {"size_mb": db_size_mb, "ok": db_size_mb < 500}
    else:
        checks["database"] = {"size_mb": 0, "ok": True}

    return jsonify({"healthy": healthy, "checks": checks}), 200 if healthy else 503


@app.route("/")
def index():
    return jsonify({
        "service": "WeRU.B Server Stats Agent",
        "endpoints": ["/api/server/stats", "/api/health"],
        "hostname": os.uname().nodename,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WeRU.B Server Stats Agent")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8585, help="Port (default: 8585)")
    args = parser.parse_args()

    print(f"[server-stats] Starting on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, threaded=True)
