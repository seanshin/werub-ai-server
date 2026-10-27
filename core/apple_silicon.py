"""Apple Silicon (M칩) 모니터링 모듈

M1/M2/M3/M4 칩셋의 CPU (E/P 코어), GPU (Metal), ANE, Unified Memory,
Thermal 상태를 수집한다. sudo 없이 수집 가능한 항목 위주로 구현.
powermetrics는 sudoers NOPASSWD 설정 시 GPU/ANE 사용률 제공.
"""
import json
import logging
import re
import subprocess
import sys
import time
import threading

import psutil

logger = logging.getLogger("ollama-admin")

# 캐시: 느린 조회(system_profiler 등)는 TTL 캐싱
_chip_info_cache: dict = {}
_chip_info_ts: float = 0
_CHIP_CACHE_TTL = 300  # 5분

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 칩 정보 (정적)
# ---------------------------------------------------------------------------

def get_chip_info() -> dict:
    """Apple Silicon 칩 정보 (캐시됨)"""
    global _chip_info_cache, _chip_info_ts
    with _lock:
        if _chip_info_cache and (time.time() - _chip_info_ts) < _CHIP_CACHE_TTL:
            return _chip_info_cache

    info = {
        "name": "Apple Silicon",
        "cores_cpu": 0,
        "cores_e": 0,   # efficiency cores
        "cores_p": 0,   # performance cores
        "cores_gpu": 0,
        "memory_gb": 0,
        "ane_tops": 0,
    }

    try:
        # 칩 이름
        r = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0 and r.stdout.strip():
            info["name"] = r.stdout.strip()

        # 물리 코어 수
        r = subprocess.run(
            ["sysctl", "-n", "hw.physicalcpu"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            info["cores_cpu"] = int(r.stdout.strip())

        # E/P 코어 분리 (sysctl)
        r_e = subprocess.run(
            ["sysctl", "-n", "hw.perflevel1.physicalcpu"],
            capture_output=True, text=True, timeout=3
        )
        r_p = subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
            capture_output=True, text=True, timeout=3
        )
        if r_e.returncode == 0:
            info["cores_e"] = int(r_e.stdout.strip())
        if r_p.returncode == 0:
            info["cores_p"] = int(r_p.stdout.strip())

        # GPU 코어 수 (system_profiler — 느리므로 캐시 TTL 내에서만)
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType", "-json"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode == 0:
                data = json.loads(r.stdout)
                for d in data.get("SPDisplaysDataType", []):
                    cores = d.get("sppci_cores") or d.get("spdisplays_gmlt_cores")
                    if cores:
                        info["cores_gpu"] = int(re.sub(r"[^\d]", "", str(cores)))
                        break
        except Exception:
            pass

        # 메모리
        r = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            info["memory_gb"] = round(int(r.stdout.strip()) / (1024 ** 3))

        # ANE TOPS (칩명 기반 추정)
        name = info["name"].lower()
        if "m4" in name:
            info["ane_tops"] = 38
        elif "m3" in name:
            info["ane_tops"] = 18
        elif "m2" in name:
            info["ane_tops"] = 15
        elif "m1" in name:
            info["ane_tops"] = 11

    except Exception as e:
        logger.debug("apple_silicon.get_chip_info error: %s", e)

    with _lock:
        _chip_info_cache = info
        _chip_info_ts = time.time()

    return info


# ---------------------------------------------------------------------------
# CPU 사용률 (E코어 / P코어 분리)
# ---------------------------------------------------------------------------

def get_cpu_usage() -> dict:
    """CPU 사용률 — E코어/P코어 평균 포함"""
    try:
        per_cpu = psutil.cpu_percent(interval=0.3, percpu=True)
        total = sum(per_cpu) / len(per_cpu) if per_cpu else 0

        chip = get_chip_info()
        n_e = chip.get("cores_e", 0)
        n_p = chip.get("cores_p", 0)

        if n_e > 0 and n_p > 0 and len(per_cpu) >= n_e + n_p:
            # P코어가 앞, E코어가 뒤 (Apple 레이아웃)
            p_cores = per_cpu[:n_p]
            e_cores = per_cpu[n_p:n_p + n_e]
        elif len(per_cpu) >= 8:
            half = len(per_cpu) // 2
            p_cores = per_cpu[:half]
            e_cores = per_cpu[half:]
        else:
            p_cores = per_cpu
            e_cores = []

        return {
            "total": round(total, 1),
            "per_core": [round(x, 1) for x in per_cpu],
            "e_cores_avg": round(sum(e_cores) / len(e_cores), 1) if e_cores else None,
            "p_cores_avg": round(sum(p_cores) / len(p_cores), 1) if p_cores else None,
            "n_e": len(e_cores),
            "n_p": len(p_cores),
        }
    except Exception as e:
        logger.debug("apple_silicon.get_cpu_usage error: %s", e)
        return {"total": 0, "per_core": [], "e_cores_avg": None, "p_cores_avg": None}


# ---------------------------------------------------------------------------
# Unified Memory Pressure
# ---------------------------------------------------------------------------

def get_memory_pressure() -> dict:
    """Unified Memory 상태 (vm_stat 기반)"""
    try:
        r = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3)
        page_size = 16384  # Apple Silicon: 16KB 페이지

        raw: dict = {}
        for line in r.stdout.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                v = v.strip().rstrip(".")
                try:
                    raw[k.strip()] = int(v) * page_size
                except ValueError:
                    pass

        wired = raw.get("Pages wired down", 0)
        active = raw.get("Pages active", 0)
        inactive = raw.get("Pages inactive", 0)
        free = raw.get("Pages free", 0)
        compressed = raw.get("Pages occupied by compressor", 0)
        speculative = raw.get("Pages speculative", 0)

        vm = psutil.virtual_memory()
        total_gb = round(vm.total / (1024 ** 3), 1)
        used_gb = round((vm.total - vm.available) / (1024 ** 3), 1)
        free_gb = round(vm.available / (1024 ** 3), 1)
        pct = round(vm.percent, 1)

        # 메모리 압박 레벨
        if pct >= 90 or compressed > 2 * (1024 ** 3):
            pressure = "critical"
        elif pct >= 75 or compressed > 512 * (1024 ** 2):
            pressure = "warning"
        else:
            pressure = "normal"

        pressure_color = {"normal": "green", "warning": "yellow", "critical": "red"}

        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": pct,
            "wired_gb": round(wired / (1024 ** 3), 2),
            "active_gb": round(active / (1024 ** 3), 2),
            "inactive_gb": round(inactive / (1024 ** 3), 2),
            "compressed_gb": round(compressed / (1024 ** 3), 2),
            "speculative_gb": round(speculative / (1024 ** 3), 2),
            "pressure": pressure,
            "pressure_color": pressure_color[pressure],
        }
    except Exception as e:
        logger.debug("apple_silicon.get_memory_pressure error: %s", e)
        try:
            vm = psutil.virtual_memory()
            return {
                "total_gb": round(vm.total / (1024 ** 3), 1),
                "used_gb": round((vm.total - vm.available) / (1024 ** 3), 1),
                "free_gb": round(vm.available / (1024 ** 3), 1),
                "percent": round(vm.percent, 1),
                "pressure": "normal",
                "pressure_color": "green",
            }
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# GPU 사용률 (ioreg — sudo 불필요)
# ---------------------------------------------------------------------------

def get_gpu_usage() -> dict:
    """Metal GPU 사용률 — ioreg IOAccelerator 기반"""
    result = {"available": False, "renderer_util": 0, "tiler_util": 0, "device_util": 0}
    try:
        r = subprocess.run(
            ["ioreg", "-r", "-c", "IOAccelerator", "-n", "AGX", "-d", "3"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0 or not r.stdout:
            return result

        # PerformanceStatistics 파싱
        match = re.search(r'"PerformanceStatistics"\s*=\s*\{([^}]+)\}', r.stdout)
        if not match:
            return result

        stats_str = match.group(1)
        stats: dict = {}
        for m in re.finditer(r'"([^"]+)"\s*=\s*(\d+)', stats_str):
            stats[m.group(1)] = int(m.group(2))

        renderer = stats.get("Renderer Utilization %", stats.get("rendererUtilization", 0))
        tiler = stats.get("Tiler Utilization %", stats.get("tilerUtilization", 0))
        device = stats.get("Device Utilization %", max(renderer, tiler))

        result.update({
            "available": True,
            "renderer_util": renderer,
            "tiler_util": tiler,
            "device_util": device,
        })
    except Exception as e:
        logger.debug("apple_silicon.get_gpu_usage error: %s", e)

    return result


# ---------------------------------------------------------------------------
# Thermal 상태
# ---------------------------------------------------------------------------

def get_thermal_state() -> dict:
    """열 상태 (sysctl kern.thermal_level)"""
    try:
        r = subprocess.run(
            ["sysctl", "-n", "kern.thermal_level"],
            capture_output=True, text=True, timeout=3
        )
        level = int(r.stdout.strip()) if r.returncode == 0 else 0
        labels = {0: "Normal", 1: "Moderate", 2: "Heavy", 3: "Critical"}
        colors = {0: "green", 1: "yellow", 2: "orange", 3: "red"}
        return {
            "level": level,
            "label": labels.get(level, "Unknown"),
            "color": colors.get(level, "green"),
            "throttling": level >= 2,
        }
    except Exception:
        return {"level": 0, "label": "Normal", "color": "green", "throttling": False}


# ---------------------------------------------------------------------------
# powermetrics (sudo NOPASSWD 설정 시 GPU/ANE 전력 제공)
# ---------------------------------------------------------------------------

def get_powermetrics() -> dict:
    """powermetrics — sudoers NOPASSWD 설정 시 작동"""
    try:
        r = subprocess.run(
            ["sudo", "-n", "powermetrics",
             "--samplers", "gpu_power,ane_power,cpu_power",
             "-n", "1", "--json"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0 or not r.stdout.strip():
            return {"available": False}

        data = json.loads(r.stdout.strip().split("\n")[0])
        gpu = data.get("gpu", {})
        ane = data.get("ane", {})
        proc = data.get("processor", {})

        return {
            "available": True,
            "gpu_active_pct": round(gpu.get("active_ratio", 0) * 100, 1),
            "gpu_power_mw": gpu.get("power_mw", 0),
            "ane_power_mw": ane.get("power_mw", 0),
            "cpu_power_mw": proc.get("cpu_power", 0),
            "package_power_mw": proc.get("package_power", 0),
        }
    except Exception:
        return {"available": False}


# ---------------------------------------------------------------------------
# 통합 조회
# ---------------------------------------------------------------------------

def get_all_metrics() -> dict:
    """M칩 전체 메트릭 통합 반환"""
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        f_cpu    = ex.submit(get_cpu_usage)
        f_mem    = ex.submit(get_memory_pressure)
        f_gpu    = ex.submit(get_gpu_usage)
        f_therm  = ex.submit(get_thermal_state)

    chip = get_chip_info()
    cpu  = f_cpu.result()
    mem  = f_mem.result()
    gpu  = f_gpu.result()
    therm = f_therm.result()

    # powermetrics는 별도 (느릴 수 있음)
    pm = get_powermetrics()
    if pm.get("available") and not gpu.get("available"):
        gpu["available"] = True
        gpu["device_util"] = pm.get("gpu_active_pct", 0)

    return {
        "chip": chip,
        "cpu": cpu,
        "memory": mem,
        "gpu": gpu,
        "thermal": therm,
        "powermetrics": pm,
    }


# ---------------------------------------------------------------------------
# Core ML 가용성 체크
# ---------------------------------------------------------------------------

def check_coreml_available() -> dict:
    """Core ML 및 관련 패키지 가용성 확인"""
    result = {"coremltools": False, "ane_available": sys.platform == "darwin"}
    try:
        import coremltools  # noqa
        result["coremltools"] = True
        result["coremltools_version"] = coremltools.__version__
    except ImportError:
        pass
    return result
