"""Web Search Blueprint — SearXNG 실시간 웹 검색 API (v2.65.0)

5개 엔드포인트:
  POST /api/search          — 웹 검색
  POST /api/search/news     — 뉴스 검색
  POST /api/search/science  — 학술 검색
  POST /api/search/it       — IT/기술 검색
  GET  /api/search/status   — SearXNG 상태 확인
"""
import hashlib
import logging

from flask import Blueprint, request, jsonify
from core.cache import TTLCache
from core.web_search import search as web_search, check_status

logger = logging.getLogger("ollama-admin")

search_bp = Blueprint("search", __name__)

# 검색 결과 캐시 — 카테고리별 TTL (뉴스 2분, general/it 5분, science 30분)
_search_cache = {
    "general": TTLCache(maxsize=200, ttl=300),
    "news":    TTLCache(maxsize=200, ttl=120),
    "science": TTLCache(maxsize=200, ttl=1800),
    "it":      TTLCache(maxsize=200, ttl=300),
}


def _search_ck(query, category, language, max_results):
    raw = f"{query}|{category}|{language}|{max_results}"
    return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()


@search_bp.route("/api/search", methods=["POST"])
def api_search():
    """실시간 웹 검색 (SearXNG)
    ---
    tags: [Search]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: 검색어}
            category: {type: string, enum: [general, news, science, it], default: general}
            language: {type: string, default: ko-KR}
            max_results: {type: integer, default: 5}
    responses:
      200:
        description: 검색 결과
    """
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    category = data.get("category", "general")
    language = data.get("language", "ko-KR")
    max_results = data.get("max_results", 5)
    key = _search_ck(query, category, language, max_results)
    cache = _search_cache.get(category, _search_cache["general"])
    cached = cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = web_search(query, category=category, language=language, max_results=max_results)
    cache.set(key, result)
    return jsonify(result)


@search_bp.route("/api/search/news", methods=["POST"])
def api_search_news():
    """뉴스 검색
    ---
    tags: [Search]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: 검색어}
            language: {type: string, default: ko-KR}
            max_results: {type: integer, default: 5}
    responses:
      200:
        description: 뉴스 검색 결과
    """
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    language = data.get("language", "ko-KR")
    max_results = data.get("max_results", 5)
    key = _search_ck(query, "news", language, max_results)
    cached = _search_cache["news"].get(key)
    if cached is not None:
        return jsonify(cached)
    result = web_search(query, category="news", language=language, max_results=max_results)
    _search_cache["news"].set(key, result)
    return jsonify(result)


@search_bp.route("/api/search/science", methods=["POST"])
def api_search_science():
    """학술/과학 검색
    ---
    tags: [Search]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: 검색어}
            language: {type: string, default: ko-KR}
            max_results: {type: integer, default: 5}
    responses:
      200:
        description: 학술 검색 결과
    """
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    language = data.get("language", "ko-KR")
    max_results = data.get("max_results", 5)
    key = _search_ck(query, "science", language, max_results)
    cached = _search_cache["science"].get(key)
    if cached is not None:
        return jsonify(cached)
    result = web_search(query, category="science", language=language, max_results=max_results)
    _search_cache["science"].set(key, result)
    return jsonify(result)


@search_bp.route("/api/search/it", methods=["POST"])
def api_search_it():
    """IT/기술 검색
    ---
    tags: [Search]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: 검색어}
            language: {type: string, default: ko-KR}
            max_results: {type: integer, default: 5}
    responses:
      200:
        description: IT 검색 결과
    """
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    language = data.get("language", "ko-KR")
    max_results = data.get("max_results", 5)
    key = _search_ck(query, "it", language, max_results)
    cached = _search_cache["it"].get(key)
    if cached is not None:
        return jsonify(cached)
    result = web_search(query, category="it", language=language, max_results=max_results)
    _search_cache["it"].set(key, result)
    return jsonify(result)


@search_bp.route("/api/search/status")
def api_search_status():
    """SearXNG 상태 확인
    ---
    tags: [Search]
    responses:
      200:
        description: SearXNG 가용 상태
        schema:
          type: object
          properties:
            available: {type: boolean}
            url: {}
            status_code: {}
            error: {}
    """
    return jsonify(check_status())
