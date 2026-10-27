"""보안 유틸리티 — SSRF 방지, Webhook, 인증 데코레이터"""
import re
import time
import socket
import ipaddress
import logging
from functools import wraps
from urllib.parse import urlparse

logger = logging.getLogger("ollama-admin")

# 하위호환(테스트 import) + 빠른 prefix 참고용. 실제 판정은 _host_is_public(IP 해석)이 수행.
PRIVATE_NETS = re.compile(
    r"https?://(127\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|169\.254\.|localhost|0\.0\.0\.0|\[?::1\]?)"
)


def _ip_is_blocked(addr):
    """ipaddress 객체가 사설/루프백/링크로컬(169.254 메타데이터)/예약/멀티캐스트/unspecified면 True."""
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _parse_ip_literal(host):
    """host가 IP 리터럴이면 ipaddress 객체 반환(아니면 None). DNS 미사용.
    점표기(192.168.0.1)·IPv6(::1)·정수/16진/8진 인코딩(2130706433, 0x7f000001) 처리 —
    getaddrinfo가 127.0.0.1로 해석하던 SSRF 우회벡터를 DNS 없이 직접 차단."""
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)            # 점표기 IPv4 / IPv6
    except ValueError:
        pass
    # 단일 정수 인코딩 (32비트 IPv4 트릭): 10진 / 0x16진 / 0o8진
    n = None
    try:
        if re.fullmatch(r"[0-9]+", host):
            n = int(host, 10)
        elif re.fullmatch(r"0[xX][0-9a-fA-F]+", host):
            n = int(host, 16)
        elif re.fullmatch(r"0[oO][0-7]+", host):
            n = int(host, 8)
    except ValueError:
        return None
    if n is not None and 0 <= n <= 0xFFFFFFFF:
        try:
            return ipaddress.ip_address(n)
        except (ValueError, OverflowError):
            return None
    return None


def validate_webhook_url(url):
    """SSRF 방지(빠름·DNS 미사용): http/https 스킴 + 호스트가 IP 리터럴이면 사설/메타데이터 차단.
    도메인 호스트는 통과(블로킹 DNS 회피 — 요청 gthread 점유/공인 S3 오차단 방지).
    도메인→사설IP 리졸브 SSRF는 다운로드 시점 resolve_host_is_public()로 별도 차단."""
    if not url:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    addr = _parse_ip_literal(p.hostname.strip("[]"))
    if addr is not None:
        return not _ip_is_blocked(addr)
    return True   # 도메인 — 빠른 통과


def resolve_host_is_public(host, timeout=4.0):
    """host(도메인/IP)를 '백그라운드 워커'에서 해석해 모든 결과 IP가 공인이면 True.
    사설/메타데이터/예약이면 False, 해석 실패도 False(fail-closed).
    ⚠️ 블로킹 DNS — 요청 핸들러(gthread)가 아닌 다운로드 워커에서만 호출할 것."""
    if not host:
        return False
    host = host.strip("[]")
    lit = _parse_ip_literal(host)
    if lit is not None:
        return not _ip_is_blocked(lit)
    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(old)
    if not infos:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0].split("%")[0])
        except ValueError:
            return False
        if _ip_is_blocked(addr):
            return False
    return True


def validate_callback_url(url):
    """콜백 URL 검증(완화): http/https 스킴 + 호스트 존재만 확인.
    내부 서비스 콜백(hospital-jitsi 등) 연동을 위해 사설 IP는 허용하되,
    file://·gopher:// 등 비-HTTP 스킴과 스킴 없는 경로 주입은 차단."""
    if not url:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname)


def fire_webhook(session, job, status, response=None, tokens=None, duration_ms=None):
    """작업 완료/실패 시 webhook POST (3회 재시도)"""
    url = job.get("webhook_url")
    if not url or not validate_webhook_url(url):
        return
    payload = {
        "job_id": job["id"], "model": job["model"], "status": status,
        "response": (response or "")[:2000], "tokens": tokens, "duration_ms": duration_ms,
    }
    for attempt in range(3):
        try:
            r = session.post(url, json=payload, timeout=10)
            if r.status_code < 400:
                logger.info(f"Webhook sent for {job['id'][:8]}")
                return
        except Exception as e:
            logger.warning(f"Webhook attempt {attempt+1} failed: {e}")
        time.sleep(2)
    logger.error(f"Webhook failed after 3 attempts for {job['id'][:8]}")


def require_auth(f):
    """Flask 라우트 인증 데코레이터 — app.py의 인증 함수를 lazy import로 사용"""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import request, jsonify, session, g
        # app.py에서 정의된 함수들을 lazy import (순환 참조 방지)
        import app as _app
        load_config = _app.load_config
        verify_api_key = _app.verify_api_key
        get_key_name = _app.get_key_name
        is_local_request = _app.is_local_request

        cfg = load_config()
        auth = request.headers.get("Authorization", "")
        token = auth.replace("Bearer ", "") if auth.startswith("Bearer ") else ""

        if not cfg.get("auth_enabled", True):
            if token:
                verify_api_key(token)
                g.act_key_name = get_key_name(token) or "noauth"
            else:
                g.act_key_name = "noauth"
            return f(*args, **kwargs)

        if cfg.get("local_bypass", False) and is_local_request():
            if token and verify_api_key(token):
                g.act_key_name = get_key_name(token)
            else:
                g.act_key_name = "local"
            return f(*args, **kwargs)

        if session.get("web_logged_in"):
            if token and verify_api_key(token):
                g.act_key_name = get_key_name(token)
            else:
                g.act_key_name = "web:" + (session.get("web_user") or "admin")
            return f(*args, **kwargs)

        if not verify_api_key(token):
            return jsonify({"error": "unauthorized"}), 401
        g.act_key_name = get_key_name(token)
        return f(*args, **kwargs)
    return decorated
