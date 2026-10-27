"""System API Blueprint — 로그, 헬스체크, 모니터링, 벤치마크"""
import hashlib
import json
import logging
import os
import subprocess
import time
import threading
from datetime import datetime, timedelta, timezone

import requests
from flask import Blueprint, request, jsonify, make_response, g
from core.errors import internal_error

logger = logging.getLogger("ollama-admin")

system_bp = Blueprint("system", __name__)

_get_db = None
_load_config = None
_save_config = None
_ollama_session = None
_is_admin_request = None
_get_worker_alive = None
_get_queue_paused = None
_OLLAMA_BASE = None
_BASE_DIR = None


def init_system(*, get_db, load_config, save_config, ollama_session, get_ollama_status=None,
                is_admin_request, get_worker_alive, get_queue_paused,
                ollama_base, base_dir):
    global _get_db, _load_config, _save_config, _ollama_session
    global _is_admin_request, _get_worker_alive, _get_queue_paused
    global _OLLAMA_BASE, _BASE_DIR
    global _get_ollama_status
    _get_db = get_db
    _load_config = load_config
    _save_config = save_config
    _ollama_session = ollama_session
    _is_admin_request = is_admin_request
    _get_worker_alive = get_worker_alive
    _get_queue_paused = get_queue_paused
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir
    _get_ollama_status = get_ollama_status


@system_bp.route("/api/logs")
def api_logs():
    """Get Ollama engine logs
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - {name: lines, in: query, type: integer, default: 100, description: "Number of log lines"}
    responses:
      200:
        schema:
          type: object
          properties:
            logs: {type: string}
      401:
        description: Unauthorized
    """
    lines = request.args.get("lines", "100")
    source = request.args.get("source", "ollama")
    log_files = {
        "ollama": "/opt/homebrew/var/log/ollama.log",
        "queue": os.path.join(_BASE_DIR, "logs", "queue.log"),
        "watchdog": os.path.join(_BASE_DIR, "logs", "watchdog.log"),
    }
    path = log_files.get(source, log_files["ollama"])
    try:
        lines_int = min(int(lines), 1000)
        result = subprocess.run(["tail", f"-{lines_int}", path], capture_output=True, text=True)
        return jsonify({"logs": result.stdout, "source": source})
    except Exception as e:
        return jsonify({"error": str(e)})

# ---------------------------------------------------------------------------
# Health Check & Alerting
# ---------------------------------------------------------------------------

_alert_cooldown = {}  # alert_key -> last_fired timestamp
_worker_dead_streak = 0  # 워커 dead 연속 관측수 (recycle 오탐 방지)

def _should_alert(key, cooldown_sec=300):
    """같은 알림을 cooldown_sec 이내에 중복 발송하지 않음"""
    now = time.time()
    if now - _alert_cooldown.get(key, 0) < cooldown_sec:
        return False
    _alert_cooldown[key] = now
    return True

def _send_alert(title, message, level="warning"):
    """다채널 알림 — Slack, Discord, 일반 Webhook"""
    cfg = _load_config()
    logger.log(
        logging.ERROR if level == "critical" else logging.WARNING,
        f"ALERT [{level}] {title}: {message}",
    )
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    sent = False

    # Slack
    slack_url = cfg.get("alert_slack_url")
    if slack_url:
        icon = ":red_circle:" if level == "critical" else ":warning:"
        try:
            _ollama_session.post(slack_url, json={
                "text": f"{icon} *[Ollama Admin] {title}*\n{message}",
            }, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"Slack alert failed: {e}")

    # Discord
    discord_url = cfg.get("alert_discord_url")
    if discord_url:
        color = 0xFF0000 if level == "critical" else 0xFFAA00
        try:
            _ollama_session.post(discord_url, json={
                "embeds": [{"title": f"[Ollama Admin] {title}", "description": message, "color": color}],
            }, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"Discord alert failed: {e}")

    # 일반 Webhook
    webhook_url = cfg.get("alert_webhook_url")
    if webhook_url:
        try:
            _ollama_session.post(webhook_url, json={
                "title": f"[Ollama Admin] {title}", "message": message, "level": level, "timestamp": ts,
            }, timeout=10)
            sent = True
        except Exception as e:
            logger.warning(f"Webhook alert failed: {e}")

    # 알림 히스토리 저장
    try:
        channels = []
        if slack_url: channels.append("slack")
        if discord_url: channels.append("discord")
        if webhook_url: channels.append("webhook")
        conn = _get_db()
        conn.execute("INSERT INTO alerts_history (ts,level,title,message,channel,success) VALUES (?,?,?,?,?,?)",
                     (ts, level, title, message[:500], ",".join(channels) or "log", 1 if sent else 0))
        conn.execute("DELETE FROM alerts_history WHERE id NOT IN (SELECT id FROM alerts_history ORDER BY ts DESC LIMIT 500)")
        conn.commit()
    except Exception as e:
        logger.debug("alert history save failed: %s", e)

_INGRESS_CACHE = {"at": 0.0, "val": None}
_INGRESS_TTL = 30.0


def _check_ingress():
    """외부 유입 경로 점검 — (ok, detail, mode).

    mode(config `ingress_mode`):
      * `ssh_tunnel`(기본) — autossh / `ssh -R 8585` / systemd `ollama-tunnel` 중 하나가 살아
        있는지. iMac(launchd autossh) 및 역터널 구성이 이 모드다.
      * `proxy` — 엣지 프록시(Cloudflare Tunnel→Caddy 등)가 유입을 받는 구성. `ingress_probe_addr`
        (기본 127.0.0.1:8088)에 **TCP 연결만** 해본다. 🔑 우리 자신에게 HTTP 요청을 되쏘지
        않는 이유: /api/health는 대시보드가 5초마다 폴링해서 자기요청이 gthread를 두 배로
        먹는다(SSE 스레드 고갈 인시던트와 같은 모양). 리스너 생존만으로 충분한 신호다.
      * `none` — 감시하지 않음(내부 전용 배치). 경보도 내지 않는다.
    """
    now = time.time()
    if _INGRESS_CACHE["val"] and (now - _INGRESS_CACHE["at"]) < _INGRESS_TTL:
        return _INGRESS_CACHE["val"]

    try:
        cfg = _load_config() or {}
    except Exception:
        cfg = {}
    mode = (cfg.get("ingress_mode") or "ssh_tunnel").strip()

    if mode == "none":
        val = (True, "not monitored (ingress_mode=none)", mode)
    elif mode == "proxy":
        addr = (cfg.get("ingress_probe_addr") or "127.0.0.1:8088").strip()
        host, _, port = addr.rpartition(":")
        val = (False, f"edge proxy unreachable ({addr})", mode)
        try:
            import socket
            with socket.create_connection((host or "127.0.0.1", int(port)), timeout=1.5):
                val = (True, f"edge proxy listening ({addr})", mode)
        except Exception as e:
            logger.debug("ingress proxy probe failed: %s", e)
    else:
        import subprocess as sp
        ok, detail = False, "tunnel down"
        try:
            # 1) autossh (macOS/iMac)
            if sp.run(["pgrep", "-f", "autossh.*8585"], capture_output=True).returncode == 0:
                ok, detail = True, "autossh running"
            # 2) ssh -R (Linux/GPU server)
            elif sp.run(["pgrep", "-f", "ssh.*-R.*8585"], capture_output=True).returncode == 0:
                ok, detail = True, "ssh tunnel running"
            elif _IS_LINUX:
                # 3) systemd 서비스 상태
                r3 = sp.run(["systemctl", "is-active", "ollama-tunnel"],
                            capture_output=True, text=True, timeout=5)
                if r3.stdout.strip() == "active":
                    ok, detail = True, "systemd tunnel active"
        except Exception as e:
            logger.debug("tunnel check failed: %s", e)
        val = (ok, detail, mode)

    _INGRESS_CACHE.update(at=now, val=val)
    return val


@system_bp.route("/api/health")
def health_check():
    """Comprehensive health check with auto-alerting
    ---
    tags: [Config]
    responses:
      200:
        description: Healthy
      503:
        description: Unhealthy
    """
    checks = {}
    healthy = True

    # 1. Ollama engine
    ollama_ok = _get_ollama_status()
    checks["ollama"] = {"ok": ollama_ok, "detail": "running" if ollama_ok else "down"}
    if not ollama_ok:
        healthy = False
        if _should_alert("ollama_down"):
            _send_alert("Ollama Engine Down", "Ollama 엔진이 응답하지 않습니다.", "critical")

    # 2. Worker — recycle 오탐 방지: graceful recycle/HUP 배포 중 일시적 alive=False는
    #    1회 관측으로 알림하지 않음(2회 연속 dead=확실). 워커 recycle ~37-40min·HUP 배포마다
    #    매번 critical 발화하던 오탐(P0 측정서 확인) 봉합.
    global _worker_dead_streak
    worker_alive = _get_worker_alive()
    checks["worker"] = {"ok": worker_alive, "paused": _get_queue_paused()}
    if not worker_alive:
        _worker_dead_streak += 1
        healthy = False
        if _worker_dead_streak >= 2 and _should_alert("worker_dead"):
            _send_alert("Worker Dead", "Worker 스레드가 종료되었습니다(2회 연속 관측).", "critical")
    else:
        _worker_dead_streak = 0

    # 3. Queue backlog
    conn = _get_db()
    try:
        pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
        running = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]
        checks["queue"] = {"pending": pending, "running": running, "ok": pending < 100}
        if pending >= 100 and _should_alert("queue_backlog"):
            _send_alert("Queue Backlog", f"대기 작업 {pending}건 적체", "warning")

        # 4. Stuck jobs (running > 10분)
        stuck = conn.execute("""
            SELECT COUNT(*) FROM jobs WHERE status='running'
            AND started_at < datetime('now', '-10 minutes')
        """).fetchone()[0]
        checks["stuck_jobs"] = {"count": stuck, "ok": stuck == 0}
        if stuck > 0 and _should_alert("stuck_jobs"):
            _send_alert("Stuck Jobs", f"{stuck}건 작업이 10분 이상 실행 중", "warning")

        # 5. Disk
        import shutil
        disk = shutil.disk_usage("/")
        disk_pct = round(disk.used / disk.total * 100, 1)
        checks["disk"] = {"used_pct": disk_pct, "free_gb": round(disk.free / 1024**3, 1), "ok": disk_pct < 90}
        if disk_pct >= 90 and _should_alert("disk_full"):
            _send_alert("Disk Full", f"디스크 사용률 {disk_pct}%", "critical")

        # 6. 외부 유입 경로(ingress) — 이 서버가 밖에서 닿을 수 있는가.
        #    2026-09-09: example.org이 Linode nginx→SSH 역터널에서 **Cloudflare Tunnel→Caddy→
        #    127.0.0.1:8585**로 바뀌면서 `ollama-tunnel`이 은퇴했다. 종전 검사는 그 터널 하나만
        #    봤기 때문에 은퇴 직후부터 critical 경보가 무한 발생했다(당일 132건).
        #    🔑 검사를 지우지 않고 **모드를 config로 갈랐다** — 지우면 신호까지 사라지고,
        #    두면 상시 위양성이라 진짜 경보가 묻힌다(경보 피로 클래스).
        #    ingress_mode: ssh_tunnel(기본·iMac/구성 유지) | proxy(엣지 리스너 TCP 확인) | none
        tunnel_ok, tunnel_detail, ingress_mode = _check_ingress()
        checks["tunnel"] = {"ok": tunnel_ok, "detail": tunnel_detail, "mode": ingress_mode}
        if ingress_mode != "none" and not tunnel_ok and _should_alert("tunnel_down"):
            _send_alert("Ingress Down", f"외부 유입 경로 이상: {tunnel_detail}", "critical")

        # 7. DB size
        db_size_mb = round(os.path.getsize(os.path.join(_BASE_DIR, "queue.db")) / 1024 / 1024, 1)
        checks["database"] = {"size_mb": db_size_mb, "ok": db_size_mb < 500}

        # 8. Failed rate (최근 1시간)
        hour_ago = (datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=1)).isoformat() + "Z"
        recent_total = conn.execute("SELECT COUNT(*) FROM jobs WHERE created_at > ?", (hour_ago,)).fetchone()[0]
        recent_failed = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='failed' AND created_at > ?", (hour_ago,)).fetchone()[0]
        fail_rate = round(recent_failed / max(recent_total, 1) * 100, 1)
        checks["fail_rate_1h"] = {"total": recent_total, "failed": recent_failed, "rate_pct": fail_rate, "ok": fail_rate < 50}
        if fail_rate >= 50 and recent_total >= 5 and _should_alert("high_fail_rate"):
            _send_alert("High Fail Rate", f"최근 1시간 실패율 {fail_rate}% ({recent_failed}/{recent_total})", "warning")

        # 9. 성능 모니터링 알림 (P3) — GPU/지연/스레드/churn. 임계는 config 무재배포 튜닝.
        #    GPU/VRAM/threads는 수집기가 박제한 최신 stats_history(≤5min)서 읽어 nvidia-smi
        #    재spawn 회피. 지연/에러율은 인메모리 리저버서 live.
        try:
            _perf_alerts(conn, checks)
        except Exception as e:
            logger.debug("perf alerts skipped: %s", e)
    finally:
        conn.close()

    status_code = 200 if healthy else 503
    return jsonify({"healthy": healthy, "checks": checks}), status_code


def _key_expiry_findings(rows, now, warn_days=7, used_days=7):
    """활성+만료설정 키 rows → (expiring, broken) 이름 리스트. 순수(테스트용).

    - expiring: 아직 유효하나 warn_days 이내 만료 도래 = D-{warn_days} 사전 경보.
    - broken: 이미 만료됐고 최근 used_days 내 사용 흔적 = 라이브 통합 단절 의심
      (07-22 appServer 클래스 — 만료 42초 전까지 사용하던 키가 경보 없이 단절).
    타임스탬프는 저장 관례(ISO 사전식, 기존 키 검증 쿼리와 동일)로 문자열 비교 —
    'Z'/'+00:00Z' 혼재는 일 단위 판정에 영향 없음.
    """
    now_iso = now.isoformat() + "Z"
    soon_iso = (now + timedelta(days=warn_days)).isoformat() + "Z"
    used_cut = (now - timedelta(days=used_days)).isoformat() + "Z"
    expiring, broken = [], []
    for r in rows:
        exp = r["expires_at"]
        if not exp:
            continue
        if now_iso < exp <= soon_iso:
            expiring.append(r["name"])
        elif exp <= now_iso and (r["last_used_at"] or "") >= used_cut:
            broken.append(r["name"])
    return expiring, broken


def _gpu_fallback_finding(resident, vram_ratio, vram_used, ratio_min=0.5):
    """상주 모델 VRAM 상태 → (title, message, severity) 또는 None. 순수(테스트용).

    2026-07-25 인시던트 봉합: 무인 드라이버 업그레이드가 커널 모듈 리로드 없이 진행
    → nvidia-smi mismatch → ollama가 로드 모델을 CPU로 폴백(size_vram=0)했는데
    /api/health는 'ollama 응답함'만 보고 healthy로 오인. GPU 사용 여부를 명시 판정.

    - resident 없음 → None(판정 불가·모델 미상주).
    - nvidia-smi 불명(vram_used 없음)인데 모델 상주 → warning(드라이버 불일치 의심,
      폴백 발생 전 조기 신호).
    - vram_ratio≈0 → critical(완전 CPU 폴백, ~수십× 느림).
    - 0<vram_ratio<ratio_min → warning(부분 오프로드=CPU 스필로 느려짐).
    - 그 외 → None(정상 GPU).
    """
    if not resident:
        return None
    if not vram_used and vram_ratio is None:
        return ("GPU Sample Unavailable",
                f"nvidia-smi·VRAM 미확인인데 모델 상주(resident={resident}) — "
                f"드라이버 불일치 의심(CPU 폴백 전 조기 신호)", "warning")
    if vram_ratio is None:
        return None
    if vram_ratio <= 0.01:
        return ("GPU CPU-Fallback",
                f"상주 모델이 CPU에 있음(size_vram≈0, resident={resident}) — "
                f"드라이버/VRAM 확인(추론 ~수십× 느림)", "critical")
    if vram_ratio < ratio_min:
        return ("GPU Partial Offload",
                f"상주 모델 VRAM 비율 {vram_ratio:.0%}(<{ratio_min:.0%}, "
                f"CPU 스필로 느려짐, resident={resident})", "warning")
    return None


def _pool_size(cfg):
    """gthread 워커 스레드 풀 상한. gunicorn.conf.py가 env로 내보낸 값이 단일 소스."""
    for src in (os.environ.get("GUNICORN_THREADS"), cfg.get("worker_thread_pool")):
        try:
            n = int(src)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return None


def _saturation_finding(inflight_peak, pool, warn_ratio=0.8):
    """동시 처리 요청 peak → (title, message, severity) 또는 None. 순수(테스트용).

    v2.124.94 — 종전 `worker_threads`(생성된 스레드 수) 경보를 대체한다.

    종전 경보는 2026-06-30~08-10 41일간 **6,354건**(전체 경보의 95%) 발화하고
    true positive 0이었다. 원인은 임계가 아니라 지표다: gthread 풀은
    ThreadPoolExecutor라 한 번 만든 스레드를 유지하므로 스레드 수는
    "풀이 다 만들어졌다"만 말하고 "풀이 다 바쁘다"는 말하지 못한다.
    30일 실측에서 threads 구간별 p95 중앙값이 575/578/575ms로 무차이였고
    sse_subscribers는 내내 0이었다(=원래 목적이던 SSE 고갈도 못 잼).

    in-flight(동시 처리 중 요청 수)는 풀 점유를 직접 재므로 고갈에 선형으로
    접근한다. pool 미상이면 판정하지 않는다(임의 절대값으로 되돌아가지 않음).
    """
    if not pool or inflight_peak is None:
        return None
    ratio = inflight_peak / pool
    if inflight_peak >= pool:
        return ("Worker Pool Exhausted",
                f"동시 요청 {inflight_peak}/{pool} — 스레드 풀 포화(신규 요청 대기)",
                "critical")
    if ratio >= warn_ratio:
        return ("Worker Pool Saturating",
                f"동시 요청 peak {inflight_peak}/{pool}({ratio:.0%}≥{warn_ratio:.0%}) — 고갈 임박",
                "warning")
    return None


def _perf_alerts(conn, checks):
    """성능 임계 알림 (P3). checks에 perf 요약 추가 + 임계 초과 시 _send_alert."""
    cfg = _load_config()
    th = cfg.get("monitoring_thresholds", {}) or {}
    vram_pct_max = th.get("vram_pressure_pct", 96)
    temp_max = th.get("gpu_temp_c", 83)
    p95_max = th.get("latency_p95_ms", 5000)
    err_max = th.get("error_rate_pct", 10)
    saturation_warn_ratio = th.get("worker_pool_warn_ratio", 0.8)
    churn_max = th.get("worker_recycles_per_30min", 4)

    fallback_ratio_min = th.get("gpu_fallback_min_vram_ratio", 0.5)

    # 최신 스냅샷 (GPU/threads/VRAM)
    row = conn.execute(
        "SELECT vram_used_mb,vram_total_mb,gpu_temp_c,worker_threads,worker_threads_peak,"
        "model_resident,model_vram_ratio FROM stats_history ORDER BY ts DESC LIMIT 1").fetchone()
    perf = {}
    if row:
        vram_used, vram_total = row["vram_used_mb"], row["vram_total_mb"]
        if vram_used and vram_total:
            vram_pct = round(vram_used / vram_total * 100, 1)
            perf["vram_pct"] = vram_pct
            if vram_pct >= vram_pct_max and _should_alert("vram_pressure"):
                _send_alert("VRAM Pressure", f"VRAM {vram_pct}% (모델 스왑 thrash 위험, resident={row['model_resident']})", "warning")
        # GPU CPU-폴백/부분 오프로드 (드라이버 불일치·VRAM 압박 등으로 로드 모델이 CPU로 감)
        vram_ratio = row["model_vram_ratio"] if "model_vram_ratio" in row.keys() else None
        perf["model_vram_ratio"] = vram_ratio
        finding = _gpu_fallback_finding(row["model_resident"], vram_ratio, vram_used, fallback_ratio_min)
        if finding and _should_alert("gpu_cpu_fallback", cooldown_sec=21600):
            _send_alert(*finding)
        temp = row["gpu_temp_c"]
        if temp is not None:
            perf["gpu_temp_c"] = temp
            if temp >= temp_max and _should_alert("gpu_temp_throttle"):
                _send_alert("GPU Temp High", f"GPU 온도 {temp}°C (throttle 임박)", "warning")
        # worker_threads는 관측용으로만 유지 — 경보는 in-flight(아래)가 담당한다.
        # (스레드 수는 풀 워밍업과 포화를 구분하지 못함 → _saturation_finding docstring)
        peak = row["worker_threads_peak"] or row["worker_threads"]
        if peak is not None:
            perf["worker_threads_peak"] = peak

    # 지연/에러율 (인메모리 리저버 — live)
    try:
        from core import metrics_reservoir
        lat = metrics_reservoir.quantiles()
        err = metrics_reservoir.error_rate()
        perf["latency_p95_ms"] = int(lat["p95"])
        perf["error_rate_pct"] = err
        # 원격 노드 미도달은 이 서버 건전성이 아니다 — 경보 축과 분리해 관측만 한다.
        perf["upstream_error_rate_pct"] = metrics_reservoir.upstream_error_rate()
        # 스레드 풀 포화 (v2.124.94) — peak은 수집기가 리셋하므로 여기선 읽기만 한다.
        pool = _pool_size(cfg)
        flight = metrics_reservoir.inflight()
        perf["inflight"] = flight["current"]
        perf["inflight_peak"] = flight["peak"]
        perf["worker_thread_pool"] = pool
        sat = _saturation_finding(flight["peak"], pool, saturation_warn_ratio)
        if sat and _should_alert("worker_pool_saturation", cooldown_sec=3600):
            _send_alert(*sat)
        if lat["p95"] >= p95_max and lat["n"] >= 20 and _should_alert("latency_p95_spike"):
            _send_alert("Latency Spike", f"p95 {int(lat['p95'])}ms (≥{p95_max}, n={lat['n']})", "warning")
        if err >= err_max and lat["n"] >= 20 and _should_alert("error_rate_spike"):
            _send_alert("Error Rate Spike", f"5xx 비율 {err}% (≥{err_max}%)", "warning")
    except Exception:
        pass

    # 품질 종합 지수 하락 (v2.124.3) — 야간 평가 지수 임계 미만 경보(TTL 5min 캐시)
    try:
        from core.eval_score import latest_index
        qi = latest_index()
        if qi:
            perf["quality_index"] = qi["score"]
            qi_min = th.get("quality_index_min", 80)   # 기본 80=B 미만이면 경고
            if qi["score"] < qi_min and _should_alert("quality_index_low", cooldown_sec=21600):
                _send_alert("Quality Index Low",
                            f"품질 종합 지수 {qi['score']} ({qi['grade']}) < {qi_min} — "
                            f"야간 평가 회귀 의심(/eval-monitor 이슈 리스트 확인)", "warning")
    except Exception:  # noqa: BLE001
        pass

    # API 키 만료 임박/단절 (v2.124.31) — 만료 설정 키의 조용한 통합 단절 방지
    # (07-22 실측: appServer 90일 만료 도래 → 활성 통합이 3일간 401 폭주를 경보 없이 지속)
    try:
        warn_days = th.get("key_expiry_warn_days", 7)
        rows2 = conn.execute(
            "SELECT name, expires_at, last_used_at FROM api_keys "
            "WHERE active=1 AND expires_at IS NOT NULL").fetchall()
        expiring, broken = _key_expiry_findings(
            rows2, datetime.now(timezone.utc), warn_days=warn_days)
        if expiring:
            perf["keys_expiring"] = expiring
            if _should_alert("key_expiry_soon", cooldown_sec=21600):
                _send_alert("API Key Expiring",
                            f"만료 {warn_days}일 이내 활성 키: {', '.join(expiring)} — "
                            f"연장(/api/keys/<id>/expiry) 또는 로테이션 필요", "warning")
        if broken:
            perf["keys_expired_in_use"] = broken
            if _should_alert("key_expired_in_use", cooldown_sec=21600):
                _send_alert("API Key Expired In Use",
                            f"만료됐으나 최근 사용 흔적이 있는 키: {', '.join(broken)} — "
                            f"라이브 통합 단절(401) 의심", "critical")
    except Exception:  # noqa: BLE001
        pass

    # 회의 STT 대기열 적체 (edu 집합교육 일괄 업로드) — **사전 통지에 의존하지 않는 자동 감지**.
    # 통지는 사람이 메일을 읽어야 성립하지만, 이건 몰림이 실제로 발생하면 알아서 뜬다.
    # 상한(50) 자체는 유실이 아니라 대기이므로 warning이며, 상한 도달은 접수 거절이라 critical.
    try:
        from core.meeting_pipeline import MAX_QUEUE
        backlog_warn = th.get("meeting_backlog_warn", max(10, MAX_QUEUE // 3))
        mconn = sqlite3.connect(os.path.join(_BASE_DIR, "meeting.db"), timeout=5)
        try:
            backlog = mconn.execute(
                "SELECT COUNT(*) FROM meeting_jobs WHERE status IN "
                "('pending','downloading','transcribing','diarizing','analyzing_vision','analyzing')"
            ).fetchone()[0]
        finally:
            mconn.close()
        if backlog:
            perf["meeting_backlog"] = backlog
        if backlog >= MAX_QUEUE and _should_alert("meeting_queue_full", cooldown_sec=3600):
            _send_alert("Meeting Queue Full",
                        f"회의 STT 대기열 {backlog}/{MAX_QUEUE} — 신규 접수가 503으로 거절된다"
                        f"(호출자에게 재시도 안내 필요)", "critical")
        elif backlog >= backlog_warn and _should_alert("meeting_backlog", cooldown_sec=3600):
            _send_alert("Meeting Backlog",
                        f"회의 STT 대기열 {backlog}건(임계 {backlog_warn}) — 일괄 업로드 진행 중. "
                        f"실시간 진료 STT와 겹치면 지연이 커진다", "warning")
    except Exception:  # noqa: BLE001 — 관측 실패가 health를 막지 않는다
        pass

    # 워커 churn 이상 (최근 ~30min 내 recycle 횟수) — DUR 과다동기화 class 자동 포착
    rows = conn.execute(
        "SELECT worker_recycles FROM stats_history WHERE worker_recycles IS NOT NULL "
        "ORDER BY ts DESC LIMIT 7").fetchall()
    if len(rows) >= 2:
        delta = (rows[0]["worker_recycles"] or 0) - (rows[-1]["worker_recycles"] or 0)
        perf["recycles_30min"] = delta
        if delta >= churn_max and _should_alert("worker_churn_anomaly", cooldown_sec=1800):
            _send_alert("Worker Churn Anomaly",
                        f"최근 ~30min 워커 recycle {delta}회 (≥{churn_max}, 폴링 폭주/과다동기화 의심)", "warning")
    checks["perf"] = perf


# ── CPU 사용률 계산용 이전 /proc/stat 값 ──
_prev_cpu_stat = None
_prev_cpu_time = 0
_prev_cpu_cores = {}

def _read_cpu_cores():
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

def _read_cpu_stat():
    """Read /proc/stat and return (idle, total) jiffies."""
    with open("/proc/stat") as f:
        line = f.readline()  # cpu  user nice system idle iowait irq softirq steal
    parts = line.split()[1:]
    vals = [int(v) for v in parts]
    idle = vals[3] + vals[4]  # idle + iowait
    total = sum(vals)
    return idle, total


# ── 원격 서버 stats 프록시 ──
GPU_STATS_URL = "http://127.0.0.1:9102"   # SSH tunnel to GPU Server (203.0.113.10)
PROD_STATS_URL = "http://127.0.0.1:9100"  # SSH tunnel to Production (192.0.2.108)
HIS_STATS_URL = "http://127.0.0.1:9101"   # SSH tunnel to HIS/PACS (198.51.100.73)
# iMac(Apple Silicon) 관리 서버 — GPU에서는 -L 8586 터널로 iMac Flask admin(:8585)에 닿는다.
# ⚠️ 8587은 **워커**(capability 전용)라 /api/health·/api/server/stats가 없다(404) — 8586이다.
MAC_STATS_URL = os.environ.get("MAC_STATS_URL", "http://127.0.0.1:8586")

# 대시보드 폴링 부하 완화: 원격 프록시 응답을 짧게 캐시(TTL) + ETag/304.
# 다중 브라우저가 같은 엔드포인트를 5초마다 폴링해도 실제 원격 호출(urllib timeout=5s,
# 호출당 gthread 점유)은 TTL당 1회로 제한 → 워커 스레드 점유 축소. (v2.74)
_PROXY_TTL = 4.0  # 초. 폴링 주기(5s)보다 약간 짧게.
_proxy_cache = {}        # key -> {"ts", "data", "status", "etag"}
_proxy_cache_lock = threading.Lock()


def _cached_proxy(key, url, *, timeout=5, err_extra=None):
    """원격 프록시 TTL 캐시 + ETag 조건부(304). 캐시 신선하면 원격 호출 생략."""
    now = time.time()
    with _proxy_cache_lock:
        ent = _proxy_cache.get(key)
        fresh = ent is not None and (now - ent["ts"]) < _PROXY_TTL
    if not fresh:
        import urllib.request
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            status = 200
        except Exception as e:
            data = {"error": str(e)}
            if err_extra:
                data.update(err_extra)
            status = 502
        raw = json.dumps(data, sort_keys=True, ensure_ascii=False)
        etag = hashlib.md5(raw.encode()).hexdigest()[:16]
        ent = {"ts": time.time(), "data": data, "status": status, "etag": etag}
        with _proxy_cache_lock:
            _proxy_cache[key] = ent

    data, status, etag = ent["data"], ent["status"], ent["etag"]
    # 원격 노드 미도달을 **이 서버의 5xx**로 세지 않게 표시(캐시 히트분도 포함하려고
    # 예외 분기가 아니라 최종 status에서 판정한다). 소비=app._activity_log →
    # metrics_reservoir.record(upstream=True). 상태 코드 계약(502)은 그대로 둔다.
    if status != 200:
        try:
            g.upstream_proxy_error = True
        except Exception:
            pass
    if status == 200:
        client_etag = request.headers.get("If-None-Match", "").strip('" ')
        if client_etag and client_etag == etag:
            resp = make_response("", 304)
            resp.headers["ETag"] = f'"{etag}"'
            resp.headers["Cache-Control"] = f"max-age={int(_PROXY_TTL)}"
            return resp
    resp = jsonify(data)
    resp.status_code = status
    if status == 200:
        resp.headers["ETag"] = f'"{etag}"'
        resp.headers["Cache-Control"] = f"max-age={int(_PROXY_TTL)}"
    return resp


@system_bp.route("/api/gpu/stats")
def gpu_server_stats():
    """Proxy GPU server stats (TTL 캐시 + ETag)"""
    return _cached_proxy("gpu/stats", f"{GPU_STATS_URL}/api/server/stats")

@system_bp.route("/api/gpu/health")
def gpu_health_check():
    """Proxy GPU server health (TTL 캐시 + ETag)"""
    return _cached_proxy("gpu/health", f"{GPU_STATS_URL}/api/health", err_extra={"healthy": False})


@system_bp.route("/api/prod/stats")
def prod_server_stats():
    """Proxy production server stats (TTL 캐시 + ETag)"""
    return _cached_proxy("prod/stats", f"{PROD_STATS_URL}/api/server/stats")

@system_bp.route("/api/prod/health")
def prod_health_check():
    """Proxy production server health (TTL 캐시 + ETag)"""
    return _cached_proxy("prod/health", f"{PROD_STATS_URL}/api/health", err_extra={"healthy": False})


@system_bp.route("/api/his/stats")
def his_server_stats():
    """Proxy HIS/PACS server stats (TTL 캐시 + ETag)"""
    return _cached_proxy("his/stats", f"{HIS_STATS_URL}/api/server/stats")

@system_bp.route("/api/his/health")
def his_health_check():
    """Proxy HIS/PACS server health (TTL 캐시 + ETag)"""
    return _cached_proxy("his/health", f"{HIS_STATS_URL}/api/health", err_extra={"healthy": False})


@system_bp.route("/api/mac/stats")
def mac_server_stats():
    """Proxy iMac server stats (TTL 캐시 + ETag).

    🔑 대시보드가 iMac을 **직접**(`/ollama-mac`) 부르지 않고 이 서버를 경유하게 하는 이유:
    브라우저에서 여러 오리진을 직접 때리면 CORS·혼합 콘텐츠·터널 노출이 각각 문제가 되고,
    무엇보다 **한 곳만 열려 있으면 되는 구조**가 깨진다(prod·his·gpu가 이미 이 패턴이다).
    """
    return _cached_proxy("mac/stats", f"{MAC_STATS_URL}/api/server/stats")


@system_bp.route("/api/mac/health")
def mac_health_check():
    """Proxy iMac server health (TTL 캐시 + ETag)"""
    return _cached_proxy("mac/health", f"{MAC_STATS_URL}/api/health",
                         err_extra={"healthy": False})


@system_bp.route("/api/mac/status")
def mac_engine_status():
    """Proxy iMac Ollama 상태(모델 목록 등, TTL 캐시 + ETag) — 모니터 화면용."""
    return _cached_proxy("mac/status", f"{MAC_STATS_URL}/api/status",
                         err_extra={"running": False, "models": []})


@system_bp.route("/api/mac/image/models")
def mac_image_models():
    """Proxy iMac 이미지 모델 목록(TTL 캐시 + ETag) — 모니터 화면용."""
    return _cached_proxy("mac/image-models", f"{MAC_STATS_URL}/api/image/models",
                         err_extra={"models": []})


@system_bp.route("/api/server/stats")
def server_stats():
    """Server resource stats (CPU, RAM, GPU, Disk, Uptime)"""
    import shutil
    stats = {}
    # CPU — /proc/stat 델타로 실제 사용률 계산
    global _prev_cpu_stat, _prev_cpu_time, _prev_cpu_cores
    try:
        with open("/proc/loadavg") as f:
            la_parts = f.read().split()
        cores = os.cpu_count() or 1
        # /proc/stat 기반 실제 CPU%
        idle, total = _read_cpu_stat()
        usage_pct = 0.0
        if _prev_cpu_stat is not None:
            d_idle = idle - _prev_cpu_stat[0]
            d_total = total - _prev_cpu_stat[1]
            if d_total > 0:
                usage_pct = round((1.0 - d_idle / d_total) * 100, 1)
        _prev_cpu_stat = (idle, total)
        # 코어별 사용률
        global _prev_cpu_cores
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
            "load_1m": float(la_parts[0]), "load_5m": float(la_parts[1]), "load_15m": float(la_parts[2]),
            "cores": cores, "usage_pct": max(0, usage_pct), "per_core": core_pcts,
        }
    except Exception:
        stats["cpu"] = {"load_1m": 0, "cores": 1, "usage_pct": 0}

    # RAM
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                k, v = line.split(":")
                mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get("MemTotal", 0) / 1024  # MB
            avail = mem.get("MemAvailable", 0) / 1024
            used = total - avail
            stats["ram"] = {"total_mb": round(total), "used_mb": round(used), "avail_mb": round(avail),
                            "usage_pct": round(used / max(total, 1) * 100, 1)}
    except Exception:
        try:
            import psutil
            vm = psutil.virtual_memory()
            stats["ram"] = {"total_mb": round(vm.total/1024/1024), "used_mb": round(vm.used/1024/1024),
                            "avail_mb": round(vm.available/1024/1024), "usage_pct": vm.percent}
        except Exception:
            stats["ram"] = {"total_mb": 0, "usage_pct": 0}

    # GPU (NVIDIA)
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
        logger.debug("GPU stats collection failed: %s", e)
    if "gpu" not in stats:
        stats["gpu"] = None

    # Disk
    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks / (1024**3)
        free = st.f_frsize * st.f_bavail / (1024**3)
        used = total - free
        stats["disk"] = {"total_gb": round(total, 1), "used_gb": round(used, 1), "free_gb": round(free, 1),
                         "usage_pct": round(used / max(total, 1) * 100, 1)}
    except Exception:
        stats["disk"] = {"total_gb": 0, "usage_pct": 0}

    # Uptime
    try:
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
        r = subprocess.run(
            ["ps", "aux", "--sort=-pcpu"],
            capture_output=True, text=True, timeout=5
        )
        procs = []
        for line in r.stdout.strip().split("\n")[1:21]:  # top 20
            parts = line.split(None, 10)
            if len(parts) >= 11:
                cpu_pct = float(parts[2])
                mem_pct = float(parts[3])
                if cpu_pct < 0.1 and mem_pct < 0.5:
                    continue
                cmd = parts[10][:80]
                # ps aux 자체 프로세스 제외
                if "ps aux" in cmd or "--sort=-pcpu" in cmd:
                    continue
                procs.append({
                    "user": parts[0], "pid": int(parts[1]),
                    "cpu": cpu_pct, "mem": mem_pct,
                    "vsz_mb": round(int(parts[4]) / 1024),
                    "rss_mb": round(int(parts[5]) / 1024),
                    "stat": parts[7], "command": cmd,
                })
        stats["processes"] = procs
        # 프로세스 기반 CPU/MEM 합계 및 피크
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

@system_bp.route("/api/stats/history")
def stats_history():
    """Get historical stats for charts
    ---
    tags: [Config]
    security: [{Bearer: []}]
    parameters:
      - {name: hours, in: query, type: integer, default: 24}
    responses:
      200:
        schema:
          type: object
          properties:
            points: {type: array, items: {type: object}}
    """
    try:
        hours = min(int(request.args.get("hours", 24)), 720)
    except ValueError:
        return jsonify({"error": "hours must be an integer"}), 400
    since = (datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=hours)).isoformat() + "Z"
    conn = _get_db()
    rows = conn.execute("SELECT * FROM stats_history WHERE ts > ? ORDER BY ts ASC", (since,)).fetchall()
    conn.close()
    return jsonify({"points": [dict(r) for r in rows]})

@system_bp.route("/api/alerts/history")
def alerts_history():
    """Get alert history
    ---
    tags: [Config]
    security: [{Bearer: []}]
    parameters:
      - {name: limit, in: query, type: integer, default: 100}
    responses:
      200:
        schema:
          type: object
          properties:
            alerts: {type: array, items: {type: object}}
    """
    try:
        limit = min(int(request.args.get("limit", 100)), 500)
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    conn = _get_db()
    rows = conn.execute("SELECT * FROM alerts_history ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return jsonify({"alerts": [dict(r) for r in rows]})

@system_bp.route("/api/benchmark/<model_name>", methods=["POST"])
def run_benchmark(model_name):
    """Run benchmark on a model
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - {name: model_name, in: path, type: string, required: true}
    responses:
      200:
        schema:
          type: object
          properties:
            model: {type: string}
            cold_load_ms: {type: integer}
            cached_load_ms: {type: integer}
            speed_tok_s: {type: number}
            tokens: {type: integer}
            eval_ms: {type: integer}
    """
    prompts = [
        "What is 1+1? Answer in one word.",
        "Translate to Korean: Hello, how are you?",
        "Write a haiku about computers.",
    ]
    results = []
    for i, p in enumerate(prompts):
        try:
            r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate",
                json={"model": model_name, "prompt": p, "stream": False, "options": {"num_predict": 30}},
                timeout=120)
            d = r.json()
            if "error" in d:
                return jsonify({"error": d["error"]}), 400
            results.append({
                "tokens": d.get("eval_count", 0),
                "eval_ns": d.get("eval_duration", 0),
                "load_ns": d.get("load_duration", 0),
                "total_ns": d.get("total_duration", 0),
            })
        except Exception as e:
            return internal_error(e)

    cold_load = results[0]["load_ns"] / 1e6 if results else 0
    cached_load = results[-1]["load_ns"] / 1e6 if len(results) > 1 else 0
    total_tokens = sum(r["tokens"] for r in results)
    total_eval_ns = sum(r["eval_ns"] for r in results)
    speed = round(total_tokens / max(total_eval_ns / 1e9, 0.01), 1)

    return jsonify({
        "model": model_name,
        "cold_load_ms": int(cold_load),
        "cached_load_ms": int(cached_load),
        "speed_tok_s": speed,
        "tokens": total_tokens,
        "eval_ms": int(total_eval_ns / 1e6),
        "runs": len(results),
    })

# ---------------------------------------------------------------------------
# 플랫폼 정보
# ---------------------------------------------------------------------------

@system_bp.route("/api/system/platform")
def api_platform():
    """현재 플랫폼 및 활성화된 기능 플래그 조회
    ---
    tags: [System]
    responses:
      200:
        description: 플랫폼 정보 및 feature flags
        schema:
          type: object
          properties:
            platform: {}
            label: {}
            features: {type: object}
    """
    from core.platform import features_summary
    return jsonify(features_summary())


# ---------------------------------------------------------------------------
# Apple Silicon 모니터링
# ---------------------------------------------------------------------------

_as_cache: dict = {}
_as_cache_ts: float = 0
_AS_CACHE_TTL = 3  # 3초 캐시 (빠른 폴링 대응)


@system_bp.route("/api/system/apple-silicon")
def api_apple_silicon():
    """Apple Silicon (M칩) 실시간 메트릭
    ---
    tags: [System]
    responses:
      200:
        description: M칩 CPU/GPU/Memory/Thermal 메트릭
    """
    global _as_cache, _as_cache_ts
    import sys as _sys
    if _sys.platform != "darwin":
        return jsonify({"error": "Apple Silicon 전용 API"}), 400

    now = time.time()
    if _as_cache and (now - _as_cache_ts) < _AS_CACHE_TTL:
        return jsonify(_as_cache)

    try:
        from core.apple_silicon import get_all_metrics
        data = get_all_metrics()
        _as_cache = data
        _as_cache_ts = now
        return jsonify(data)
    except Exception as e:
        logger.error("apple_silicon metrics error: %s", e)
        return internal_error(e)


@system_bp.route("/api/system/apple-silicon/chip")
def api_apple_silicon_chip():
    """Apple Silicon 칩 정보 (정적)
    ---
    tags: [System]
    responses:
      200:
        description: 칩명, 코어 수, GPU 코어 수, ANE TOPS
    """
    import sys as _sys
    if _sys.platform != "darwin":
        return jsonify({"error": "Apple Silicon 전용 API"}), 400
    from core.apple_silicon import get_chip_info
    return jsonify(get_chip_info())


# ---------------------------------------------------------------------------
# Unified Memory 스마트 캐싱 (모델 keep-alive 관리)
# ---------------------------------------------------------------------------

@system_bp.route("/api/models/warm", methods=["POST"])
def api_model_warm():
    """모델을 메모리에 예열 (keep-alive 설정)
    ---
    tags: [System]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [model]
          properties:
            model: {type: string}
            keep_alive: {type: string, default: "1h"}
    responses:
      200:
        description: 예열 결과
    """
    data = request.get_json(force=True, silent=True) or {}
    model = data.get("model", "")
    keep_alive = data.get("keep_alive", "1h")
    if not model:
        return jsonify({"error": "model required"}), 400

    try:
        resp = _ollama_session.post(
            f"{_OLLAMA_BASE}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": keep_alive},
            timeout=30,
        )
        resp.raise_for_status()
        logger.info("[warm] %s keep_alive=%s", model, keep_alive)
        return jsonify({"ok": True, "model": model, "keep_alive": keep_alive})
    except Exception as e:
        return internal_error(e)


@system_bp.route("/api/models/unload", methods=["POST"])
def api_model_unload():
    """모델을 메모리에서 즉시 해제
    ---
    tags: [System]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [model]
          properties:
            model: {type: string}
    responses:
      200:
        description: 언로드 결과
    """
    data = request.get_json(force=True, silent=True) or {}
    model = data.get("model", "")
    if not model:
        return jsonify({"error": "model required"}), 400

    try:
        resp = _ollama_session.post(
            f"{_OLLAMA_BASE}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("[unload] %s", model)
        return jsonify({"ok": True, "model": model})
    except Exception as e:
        return internal_error(e)


@system_bp.route("/api/models/cache-status")
def api_model_cache_status():
    """현재 메모리에 로드된 모델 목록 및 Unified Memory 상태
    ---
    tags: [System]
    responses:
      200:
        description: 로드된 모델 + 메모리 상태
    """
    import sys as _sys
    try:
        ps_resp = _ollama_session.get(f"{_OLLAMA_BASE}/api/ps", timeout=5)
        running = ps_resp.json().get("models", []) if ps_resp.ok else []
    except Exception:
        running = []

    result = {"loaded_models": running, "count": len(running)}

    if _sys.platform == "darwin":
        try:
            from core.apple_silicon import get_memory_pressure
            result["memory"] = get_memory_pressure()
        except Exception:
            pass

    return jsonify(result)


# ---------------------------------------------------------------------------
# STT — faster-whisper + Metal (iMac 전용, GPU 서버 호출 가능)
# ---------------------------------------------------------------------------

@system_bp.route("/api/stt", methods=["POST"])
def api_stt():
    """음성 → 텍스트 (faster-whisper + Metal/ANE)
    ---
    tags: [System]
    summary: STT — iMac Metal 가속 음성 인식 (GPU 서버 호출 가능)
    consumes:
      - multipart/form-data
      - application/json
    parameters:
      - in: formData
        name: file
        type: file
        description: 오디오 파일 (mp3/mp4/wav/m4a)
      - in: body
        name: body
        schema:
          type: object
          properties:
            file_url: {type: string, description: "S3/HTTP URL"}
            language: {type: string, default: "ko"}
            model:    {type: string, default: "base", description: "tiny|base|small|medium|large-v3"}
            diarize:  {type: boolean, default: false}
    responses:
      200:
        description: 전사 결과
      400:
        description: faster-whisper 미설치 또는 입력 오류
    """
    import sys as _sys
    if _sys.platform != "darwin":
        return jsonify({"error": "이 엔드포인트는 iMac(Metal) 전용입니다"}), 400

    try:
        import faster_whisper  # noqa
    except ImportError:
        return jsonify({
            "error": "faster-whisper 미설치",
            "hint": "pip install faster-whisper"
        }), 400

    import tempfile, os as _os
    from faster_whisper import WhisperModel

    # 파라미터
    language = "ko"
    model_size = "base"
    diarize = False
    tmp_path = None

    try:
        if request.content_type and "multipart" in request.content_type:
            # 파일 업로드
            f = request.files.get("file")
            if not f:
                return jsonify({"error": "file 필드 필요"}), 400
            language   = request.form.get("language", "ko")
            model_size = request.form.get("model", "base")
            diarize    = request.form.get("diarize", "false").lower() == "true"
            suffix = _os.path.splitext(f.filename or "audio.mp3")[1] or ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                f.save(tmp)
                tmp_path = tmp.name
        else:
            data = request.get_json(force=True, silent=True) or {}
            language   = data.get("language", "ko")
            model_size = data.get("model", "base")
            diarize    = data.get("diarize", False)
            file_url   = data.get("file_url", "")
            if not file_url:
                return jsonify({"error": "file 또는 file_url 필요"}), 400

            import urllib.request
            suffix = ".mp4" if "mp4" in file_url else ".mp3"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                urllib.request.urlretrieve(file_url, tmp.name)
                tmp_path = tmp.name

        t0 = time.time()

        # Metal 백엔드 (compute_type="int8" → ANE 친화적)
        wm = WhisperModel(
            model_size,
            device="auto",          # Metal GPU 자동 선택
            compute_type="int8",
            download_root=_os.path.join(_BASE_DIR, "whisper_models"),
        )
        segments_gen, info = wm.transcribe(
            tmp_path,
            language=language,
            beam_size=5,
            vad_filter=True,
        )

        segments = [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments_gen
        ]
        full_text = " ".join(s["text"] for s in segments)
        duration_ms = round((time.time() - t0) * 1000)

        logger.info("[STT] %s | lang=%s | segments=%d | %dms",
                    model_size, language, len(segments), duration_ms)

        return jsonify({
            "text": full_text,
            "segments": segments,
            "language": info.language,
            "duration_sec": round(info.duration, 1),
            "model": model_size,
            "engine": "faster-whisper-metal",
            "duration_ms": duration_ms,
        })

    except Exception as e:
        logger.error("[STT] error: %s", e)
        return internal_error(e)
    finally:
        if tmp_path and _os.path.exists(tmp_path):
            _os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 실시간 청크 STT — 워밍 faster-whisper (호스트 STT 파이프라인 계약)
# ---------------------------------------------------------------------------

@system_bp.route("/api/stt/chunk", methods=["POST"])
def api_stt_chunk():
    """실시간 청크 음성인식 (raw PCM) + final 번역 통합(A+C)
    ---
    tags: [System]
    summary: 워밍 faster-whisper 실시간 STT. partial=STT만 / final=STT(+X-Targets 번역).
    consumes: [application/octet-stream]
    parameters:
      - {in: header, name: X-Sample-Rate, type: integer, description: "PCM 샘플레이트(기본 16000)"}
      - {in: header, name: X-Lang, type: string, description: "언어 코드(기본 ko)"}
      - {in: header, name: X-Mode, type: string, description: "partial | final(기본 final)"}
      - {in: header, name: X-Session-Id, type: string, description: "발화 세션 키(optional, partial 누적)"}
      - {in: header, name: X-Targets, type: string, description: "final 번역 대상 CSV(예: en) — A+C 통합"}
      - {in: header, name: X-Hotwords, type: string, description: "어휘 바이어싱 CSV. 한글은 헤더(latin-1) 불가 → 퍼센트 인코딩(UTF-8) 또는 권장 경로 POST /api/stt/chunk/vocab 사용. 미지정 시 세션 등록분 적용"}
      - {in: header, name: X-VAD, type: string, description: "on이면 서버측 Silero VAD 게이트 — 무음/비발화 청크는 text 빈 문자열(기본 off)"}
      - {in: header, name: X-Min-Speech-Ms, type: integer, description: "X-VAD on일 때 최소 발화 길이 ms(기본 250) — 이보다 짧은 소리는 비발화 처리"}
    responses:
      200: {description: "{text, lang, confidence, no_speech, no_speech_prob, avg_logprob, compression_ratio, ms, is_final[, suppressed, translation]}. 무음 환각 판별=no_speech_prob(프롬프트 에코는 confidence가 높아 low_confidence를 통과하므로 이 값으로 드롭). suppressed=prompt_echo|hallucination_pattern이면 서버가 환각 전사를 차단해 text를 비운 것"}
      400: {description: 입력 오류}
      503: {description: STT 엔진 미가용}
    """
    from core import stt_realtime

    if not stt_realtime.is_available():
        return jsonify({"error": "stt unavailable", "hint": "pip install faster-whisper"}), 503

    pcm = request.get_data(cache=False)
    if not pcm:
        return jsonify({"error": "raw PCM body required (application/octet-stream)"}), 400

    try:
        sample_rate = int(request.headers.get("X-Sample-Rate", stt_realtime.SAMPLE_RATE))
    except (TypeError, ValueError):
        sample_rate = stt_realtime.SAMPLE_RATE
    language = (request.headers.get("X-Lang") or "ko").strip().lower()
    # X7 같은 클래스: 무효 코드는 transcribe에서 ValueError → 500 "transcribe_failed"로
    # 뭉개졌다(상세는 로그에만). 접수 시점 400으로 표면화. "auto"는 이 트랙(실시간·계약
    # 경로)에선 미지원 — 언어충실도 가드가 요청 언어를 전제하므로 여기선 거절만 한다.
    _codes = None
    try:
        from core.meeting_stt import whisper_language_codes
        _codes = whisper_language_codes()
    except Exception:                      # noqa: BLE001 — 검증 불가 시 현행(무검증)
        pass
    if language == "auto" or (_codes is not None and language not in _codes):
        return jsonify({"error": f"unsupported X-Lang '{language}' — whisper 언어 코드를 지정 (기본 ko·auto 미지원)"}), 400
    mode = (request.headers.get("X-Mode") or "final").strip().lower()
    is_final = mode == "final"
    session_id = (request.headers.get("X-Session-Id") or "").strip()
    targets_hdr = (request.headers.get("X-Targets") or "").strip()
    # 서버측 VAD 게이트(HIS 요청 3·옵트인) — 켜면 무음/비발화 청크는 모델 앞단에서 걸러진다.
    vad_on = (request.headers.get("X-VAD") or "").strip().lower() in ("on", "1", "true")
    try:
        min_speech_ms = int(request.headers.get("X-Min-Speech-Ms", "250"))
    except (TypeError, ValueError):
        min_speech_ms = 250

    # 세션 누적(있으면) — partial 잠정/ final flush. 미지정 시 청크 단독.
    dropped_s = 0.0
    if session_id:
        audio_pcm, dropped_s = stt_realtime.session_append(session_id, pcm, is_final)
    else:
        audio_pcm = pcm

    # 어휘 바이어싱: 요청 헤더 > 세션 등록분(/vocab) > 미적용. 프롬프트도 세션분이 있으면 사용.
    # ⚠️HTTP 헤더는 latin-1이라 **한글 용어는 헤더로 그대로 보낼 수 없다**(클라이언트에서
    # UnicodeEncodeError). 그래서 헤더는 퍼센트 인코딩(UTF-8)을 허용하고, 한글 용어의 권장
    # 경로는 JSON 본문인 `POST /api/stt/chunk/vocab` 세션 등록이다.
    hdr = request.headers.get("X-Hotwords") or ""
    if "%" in hdr:
        from urllib.parse import unquote
        hdr = unquote(hdr)
    vocab = stt_realtime.get_session_vocab(session_id)
    hotwords = stt_realtime._normalize_terms(hdr) or vocab.get("hotwords") or None
    sess_prompt = vocab.get("prompt")

    try:
        result = stt_realtime.transcribe_pcm(audio_pcm, sample_rate=sample_rate, language=language,
                                             initial_prompt=sess_prompt, hotwords=hotwords,
                                             vad=vad_on, min_speech_ms=min_speech_ms)
    except Exception as e:  # noqa: BLE001 — 실패 시 자막 비표시(호스트 정책), 500 대신 명시
        logger.error("[stt/chunk] transcribe error: %s", e)
        return jsonify({"error": "transcribe_failed"}), 500  # 상세는 위 logger.error에만

    # 저신뢰 STT는 번역 cascade 보류(오인식→번역 환각 전파 방지, 호스트 요청 가드).
    low_conf = result["text"] and result["confidence"] < stt_realtime.STT_TRANSLATE_MIN_CONF
    resp = {
        "text": result["text"],
        "lang": language,
        "confidence": result["confidence"],
        "ms": result["ms"],
        "is_final": is_final,
        "duration_s": result["duration_s"],
        "low_confidence": bool(low_conf),
        "hotwords_applied": bool(hotwords),   # 바이어싱 적용 여부(호출자 검증용)
        # 무음 판별(HIS 요청 1) — 프롬프트 에코 환각은 confidence가 높아 low_confidence를
        # 통과하므로 no_speech_prob가 유일한 드롭 신호다. no_speech=편의 불리언(빈 전사).
        "no_speech": not result["text"],
        "no_speech_prob": result.get("no_speech_prob"),
        "avg_logprob": result.get("avg_logprob"),
        "compression_ratio": result.get("compression_ratio"),
        "vad": "on" if vad_on else "off",
        # 세션 슬라이딩(30초)으로 버려진 오디오 길이. >0이면 **앞부분 발화가 유실**된 것이라
        # 호출자가 더 짧은 주기로 final을 끊어야 한다(종전에는 무성 절단이었다).
        "dropped_s": dropped_s,
        "session_max_s": stt_realtime._SESSION_MAX_SEC,
    }
    if result.get("suppressed"):
        # 서버가 환각 전사(프롬프트 에코·정형 환각구)를 차단해 text를 비웠음을 통지(HIS 요청 2)
        resp["suppressed"] = result["suppressed"]

    # A+C: final + X-Targets 지정 시 번역 통합(단일 왕복). 단 저신뢰면 보류.
    if is_final and targets_hdr and result["text"]:
        targets = [t.strip().lower() for t in targets_hdr.split(",") if t.strip()]
        if low_conf:
            resp["translation_held"] = "low_confidence"   # 자막 측 보류/표시 판단
        elif targets:
            try:
                from routes.writing import translate_core, TRANSLATE_LANGUAGES
                valid = [t for t in targets if t in TRANSLATE_LANGUAGES]
                if valid:
                    tr = translate_core(result["text"], valid, source=language, domain="medical")
                    resp["translation"] = tr["translation"]
                    resp["ms"] += tr.get("ms", 0)
            except Exception as e:  # noqa: BLE001 — 번역 실패해도 STT 텍스트는 반환
                logger.warning("[stt/chunk] final translate failed: %s", e)
                resp["translation_error"] = str(e)

    return jsonify(resp)


@system_bp.route("/api/stt/chunk/status", methods=["GET"])
def api_stt_chunk_status():
    """실시간 STT 상태(모델 로드/활성 세션)
    ---
    tags: [System]
    responses:
      200:
        description: "요청 성공"
    """
    from core import stt_realtime
    return jsonify(stt_realtime.status())


@system_bp.route("/api/stt/chunk/vocab", methods=["POST"])
def api_stt_chunk_vocab():
    """세션(진료) 단위 어휘 바이어싱 등록 — 약품/검사명 shortlist
    ---
    tags: [System]
    summary: 등록 후 같은 X-Session-Id 청크에 hotwords 적용. terms 비우면 해제.
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [session_id]
          properties:
            session_id: {type: string, description: X-Session-Id와 동일 값}
            terms: {type: array, items: {type: string}, description: '용어 shortlist(CSV 문자열도 허용). 상한 200개·600자 — 초과분은 절단되며 응답 truncated로 통지'}
            initial_prompt: {type: string, description: 도메인 프롬프트 교체(미지정 시 기본 의료 프롬프트)}
    responses:
      200: {description: "{session_id, terms_requested, terms_applied, truncated, chars}"}
      400: {description: session_id 필요}
    """
    from core import stt_realtime
    data = request.json or {}
    sid = (data.get("session_id") or "").strip()
    if not sid:
        return jsonify({"error": "session_id required"}), 400
    return jsonify(stt_realtime.set_session_vocab(
        sid, data.get("terms"), data.get("initial_prompt")))


@system_bp.route("/api/stt/chunk/warm", methods=["POST"])
def api_stt_chunk_warm():
    """STT 모델 사전 워밍(상주 로드) — 회의 시작 시 호출해 첫 청크 콜드로드(~18s) 제거.
    ---
    tags: [System]
    summary: 워커 재기동(~40분 주기)으로 언로드되므로 회의 시작 직전 1회 호출 권장.
    responses:
      200:
        description: "요청 성공"
    """
    from core import stt_realtime
    t0 = time.time()
    ok = stt_realtime.warm()
    return jsonify({"warmed": ok, "ms": int((time.time() - t0) * 1000),
                    **stt_realtime.status()})


# ---------------------------------------------------------------------------
# Core ML 임베딩
# ---------------------------------------------------------------------------

@system_bp.route("/api/embed/coreml", methods=["POST"])
def api_embed_coreml():
    """Core ML (ANE) 임베딩 생성
    ---
    tags: [System]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [input]
          properties:
            input: {type: array, items: {type: string}}
            model: {type: string, default: "BAAI/bge-m3"}
            normalize: {type: boolean, default: true}
    responses:
      200:
        description: 임베딩 벡터 배열
      400:
        description: Core ML 미지원 환경
    """
    import sys as _sys
    if _sys.platform != "darwin":
        return jsonify({"error": "Core ML은 macOS 전용입니다"}), 400

    data = request.get_json(force=True, silent=True) or {}
    texts = data.get("input", [])
    model_id = data.get("model", "BAAI/bge-m3")
    normalize = data.get("normalize", True)

    if not texts:
        return jsonify({"error": "input required"}), 400
    if isinstance(texts, str):
        texts = [texts]

    try:
        from core.coreml_embed import embed, is_available, is_converted
        if not is_available():
            return jsonify({"error": "coremltools 미설치. pip install coremltools"}), 400
        if not is_converted(model_id):
            return jsonify({
                "error": "Core ML 모델 미변환",
                "hint": "from core.coreml_embed import convert_model; convert_model() 실행"
            }), 400

        t0 = time.time()
        vectors = embed(texts, model_id=model_id, normalize=normalize)
        duration_ms = round((time.time() - t0) * 1000)

        return jsonify({
            "embeddings": vectors,
            "model": model_id,
            "count": len(vectors),
            "dim": len(vectors[0]) if vectors else 0,
            "duration_ms": duration_ms,
            "engine": "coreml_ane",
        })
    except Exception as e:
        logger.error("coreml embed error: %s", e)
        return internal_error(e)


@system_bp.route("/api/embed/coreml/status")
def api_embed_coreml_status():
    """Core ML 임베딩 환경 상태
    ---
    tags: [System]
    responses:
      200:
        description: "요청 성공"
    """
    try:
        from core.coreml_embed import get_status
        return jsonify(get_status())
    except Exception as e:
        return internal_error(e)


@system_bp.route("/api/embed/coreml/convert", methods=["POST"])
def api_embed_coreml_convert():
    """Core ML 모델 변환 (백그라운드 실행)
    ---
    tags: [System]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            model: {type: string, default: "BAAI/bge-m3"}
            compute_units: {type: string, default: "ALL"}
    responses:
      200:
        description: "요청 성공"
      403:
        description: "권한 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "관리자 전용"}}
    """
    if not _is_admin_request():
        return jsonify({"error": "관리자 전용"}), 403

    data = request.get_json(force=True, silent=True) or {}
    model_id = data.get("model", "BAAI/bge-m3")
    compute_units = data.get("compute_units", "ALL")

    def _run():
        try:
            from core.coreml_embed import convert_model
            convert_model(model_id=model_id, compute_units=compute_units)
        except Exception as e:
            logger.error("coreml convert error: %s", e)

    import threading
    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({
        "ok": True,
        "message": f"{model_id} 변환 시작 (백그라운드). /api/embed/coreml/status 로 확인",
        "model": model_id,
    })


# ---------------------------------------------------------------------------
# RAG (Retrieval-Augmented Generation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Model Initialization
# ---------------------------------------------------------------------------

def _init_config_path(): return os.path.join(_BASE_DIR, "init_config.json")

DEFAULT_INIT_CONFIG = {
    "country": "대한민국",
    "region": "서울",
    "timezone": "Asia/Seoul",
    "language": "한국어",
    "base_instructions": (
        "반드시 한국어로만 답변하세요.\n"
        "사용자의 질문에 친절하고 정확하게 답변합니다.\n"
        "모르는 것은 모른다고 솔직하게 말합니다."
    ),
    "custom_commands": "",
    "warm_up_prompt": "준비되었습니까? 간단히 인사해 주세요.",
}

def load_init_config():
    try:
        with open(_init_config_path()) as f:
            return {**DEFAULT_INIT_CONFIG, **json.load(f)}
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_INIT_CONFIG)

def save_init_config(cfg):
    with open(_init_config_path(), "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def build_init_system_prompt(cfg=None):
    """현재 시각/국가/지역 + 커스텀 명령어로 시스템 프롬프트 생성"""
    if cfg is None:
        cfg = load_init_config()
    import datetime
    kst = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(kst)
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    date_str = now.strftime(f"%Y년 %m월 %d일 ({weekdays[now.weekday()]})")
    time_str = now.strftime("%H:%M")

    parts = [
        "=== 시스템 초기화 정보 ===",
        f"현재 날짜: {date_str}",
        f"현재 시각: {time_str} KST (UTC+9)",
        f"국가: {cfg.get('country', '대한민국')}",
        f"지역: {cfg.get('region', '서울')}",
        f"언어: {cfg.get('language', '한국어')}",
        "========================",
    ]

    base = cfg.get("base_instructions", "").strip()
    if base:
        parts.append("")
        parts.append(base)

    custom = cfg.get("custom_commands", "").strip()
    if custom:
        parts.append("")
        parts.append("=== 추가 명령어 ===")
        parts.append(custom)

    return "\n".join(parts)


@system_bp.route("/api/models/init-config", methods=["GET"])
def get_init_config():
    """Get model initialization configuration
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    responses:
      200:
        schema:
          type: object
          properties:
            country: {type: string}
            region: {type: string}
            timezone: {type: string}
            language: {type: string}
            base_instructions: {type: string}
            custom_commands: {type: string}
            warm_up_prompt: {type: string}
            preview: {type: string}
    """
    cfg = load_init_config()
    cfg["preview"] = build_init_system_prompt(cfg)
    return jsonify(cfg)


@system_bp.route("/api/models/init-config", methods=["POST"])
def update_init_config():
    """Update model initialization configuration
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            country: {type: string}
            region: {type: string}
            timezone: {type: string}
            language: {type: string}
            base_instructions: {type: string}
            custom_commands: {type: string}
            warm_up_prompt: {type: string}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            preview: {type: string}
    """
    data = request.json or {}
    cfg = load_init_config()
    valid_keys = ("country", "region", "timezone", "language",
                  "base_instructions", "custom_commands", "warm_up_prompt")
    for k in valid_keys:
        if k in data:
            cfg[k] = str(data[k])
    save_init_config(cfg)
    return jsonify({"ok": True, "preview": build_init_system_prompt(cfg)})


@system_bp.route("/api/models/init", methods=["POST"])
def init_model():
    """Initialize a model with system context (time, locale, instructions)
    ---
    tags: [Ollama]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [model]
          properties:
            model: {type: string, example: "qwen2.5:7b"}
            warm_up_prompt: {type: string}
    responses:
      200:
        schema:
          type: object
          properties:
            ok: {type: boolean}
            model: {type: string}
            response: {type: string}
            duration_ms: {type: integer}
            system_prompt: {type: string}
    """
    data = request.json or {}
    model = data.get("model", "").strip()
    if not model:
        return jsonify({"error": "model required"}), 400

    cfg = load_init_config()
    system_prompt = build_init_system_prompt(cfg)
    warm_up = data.get("warm_up_prompt") or cfg.get("warm_up_prompt", "준비되었습니까?")

    import time as _time
    t0 = _time.time()
    try:
        r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
            "model": model,
            "system": system_prompt,
            "prompt": warm_up,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 100},
        }, timeout=120)
        r.raise_for_status()
        result = r.json()
        response_text = result.get("response", "")
        duration_ms = int((_time.time() - t0) * 1000)
        logger.info(f"Model init: {model} — {duration_ms}ms")
        return jsonify({
            "ok": True,
            "model": model,
            "response": response_text,
            "duration_ms": duration_ms,
            "system_prompt": system_prompt,
        })
    except Exception as e:
        logger.warning("internal error [system]: %s", e)
        return jsonify({"ok": False, "error": "internal server error"}), 500



# ---------------------------------------------------------------------------
# Game NPC AI System
# ---------------------------------------------------------------------------
# Image Generation (ComfyUI proxy)
# ---------------------------------------------------------------------------

COMFYUI_BASE = "http://127.0.0.1:8188"
FLUX_CHECKPOINT = "flux1-schnell-fp8.safetensors"


# ---------------------------------------------------------------------------
# Korean LLM helpers
# ---------------------------------------------------------------------------
