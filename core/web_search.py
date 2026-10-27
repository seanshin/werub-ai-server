"""SearXNG 실시간 웹 검색 래퍼"""

import logging
import time

import requests

logger = logging.getLogger("ollama-admin")

# 기본 설정 (config.json 또는 init_web_search()로 오버라이드)
_SEARXNG_URL = "http://localhost:8888"
_SEARXNG_TIMEOUT = 10
_SEARXNG_MAX_RESULTS = 5

# HTTP 세션 재사용 (TCP connection pool)
_session = requests.Session()
_session.headers.update({"Connection": "keep-alive"})


def init_web_search(*, searxng_url=None, timeout=None, max_results=None):
    """설정 초기화 (app.py에서 호출)"""
    global _SEARXNG_URL, _SEARXNG_TIMEOUT, _SEARXNG_MAX_RESULTS
    if searxng_url:
        _SEARXNG_URL = searxng_url.rstrip("/")
    if timeout:
        _SEARXNG_TIMEOUT = timeout
    if max_results:
        _SEARXNG_MAX_RESULTS = max_results


def search(query, *, category="general", language="ko-KR", max_results=None):
    """SearXNG 검색 실행

    Args:
        query: 검색어
        category: general | news | science | it
        language: 검색 언어 (기본 ko-KR)
        max_results: 최대 결과 수 (기본 _SEARXNG_MAX_RESULTS)

    Returns:
        dict: {"results": [...], "query": str, "duration_ms": int}
    """
    max_results = max_results or _SEARXNG_MAX_RESULTS
    start = time.time()

    try:
        r = _session.get(
            f"{_SEARXNG_URL}/search",
            params={
                "q": query,
                "format": "json",
                "categories": category,
                "language": language,
                "pageno": 1,
            },
            timeout=_SEARXNG_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.ConnectionError:
        elapsed = int((time.time() - start) * 1000)
        return {"error": "SearXNG 연결 실패", "results": [], "query": query, "duration_ms": elapsed}
    except requests.exceptions.Timeout:
        elapsed = int((time.time() - start) * 1000)
        return {"error": "SearXNG 타임아웃", "results": [], "query": query, "duration_ms": elapsed}
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        logger.error("SearXNG search error: %s", e)
        return {"error": str(e)[:200], "results": [], "query": query, "duration_ms": elapsed}

    # 결과 정규화
    results = []
    for item in data.get("results", [])[:max_results]:
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", ""),
        })

    elapsed = int((time.time() - start) * 1000)
    return {
        "results": results,
        "query": query,
        "category": category,
        "total_found": len(data.get("results", [])),
        "returned": len(results),
        "duration_ms": elapsed,
    }


def check_status():
    """SearXNG 상태 확인"""
    try:
        r = _session.get(f"{_SEARXNG_URL}/", timeout=5)
        return {
            "available": r.status_code == 200,
            "url": _SEARXNG_URL,
            "status_code": r.status_code,
        }
    except Exception as e:
        return {
            "available": False,
            "url": _SEARXNG_URL,
            "error": str(e)[:200],
        }
