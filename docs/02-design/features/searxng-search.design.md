# SearXNG 실시간 웹 검색 — Design

> **Feature**: searxng-search | **Version**: v2.65.0 | **Created**: 2026-06-02

## 1. 컴포넌트 구조

```
config/tools.json          ← web_search 도구 정의 (21번째)
core/web_search.py         ← SearXNG HTTP 래퍼 (신규)
routes/orchestrator.py     ← search:// 프로토콜 + /api/search 엔드포인트
setup/com.werub.gpu-tunnel.plist ← SSH 터널 8888 포트 추가
```

## 2. 데이터 흐름

```
사용자 "오늘 삼성전자 주가"
  → Orchestrator intent 미매칭
  → Tool Calling (qwen3:8b)
    → LLM: web_search(query="삼성전자 주가") 도구 선택
    → _execute_action("search://searxng", {...})
    → core/web_search.search()
    → HTTP GET http://localhost:8888/search?q=...&format=json
    → SearXNG (Docker, GPU 서버) → Google/Bing/DDG 집계
    → 결과 5건 정규화 반환
    → LLM이 결과 종합 → 한국어 답변
  → force_korean → 최종 응답
```

## 3. API 명세

### POST /api/search
```json
Request: {"query": "검색어", "category": "general", "max_results": 5}
Response: {
  "results": [{"title": "", "url": "", "snippet": "", "engine": ""}],
  "query": "검색어", "category": "general",
  "total_found": 39, "returned": 5, "duration_ms": 1200
}
```

### GET /api/search/status
```json
Response: {"available": true, "url": "http://localhost:8888", "status_code": 200}
```

## 4. Tool Calling 도구 정의

```json
{
  "name": "web_search",
  "action": "search://searxng",
  "parameters": {
    "required": ["query"],
    "properties": {
      "query": "검색어",
      "category": "general|news|science|it",
      "max_results": 5
    }
  }
}
```

## 5. 인프라

| 구성 | 상세 |
|------|------|
| SearXNG | Docker `searxng/searxng:latest`, GPU 서버 127.0.0.1:8888 |
| Redis | 기존 6379 공유 (rate limiting) |
| SSH 터널 | iMac `-L 8888:127.0.0.1:8888` (gpu-tunnel.plist) |
| 설정 | config.json `searxng_url` / `searxng_timeout` / `searxng_max_results` |

## 6. 에러 처리

| 상황 | 처리 |
|------|------|
| SearXNG 다운 | `{"error": "SearXNG 연결 실패", "results": []}` |
| 타임아웃 (>10초) | `{"error": "SearXNG 타임아웃", "results": []}` |
| 빈 검색어 | 400 에러 반환 |
| Tool Calling에서 실패 | LLM이 도구 없이 직접 답변 시도 |
