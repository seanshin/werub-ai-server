# Dashboard API Guide

통합 대시보드 API — 외부 시스템에서 AI Server의 전체 현황을 조회하기 위한 집계 엔드포인트.

- **Base URL**: `http://localhost:8585` (로컬) / `https://example.org/ollama` (외부)
- **인증**: 외부 접속 시 `Authorization: Bearer <API_KEY>` 필수
- **응답 형식**: JSON (gzip 자동 압축)
- **최적화**: ETag 304, Cache-Control, fields 필터링

---

## 인증 체계 & 기존 API와의 관계

### 두 가지 인증 방식

| 구분 | 대시보드 HTML 페이지 | API 엔드포인트 (`/api/*`) |
|------|---------------------|--------------------------|
| **대상** | `/dashboard`, `/dashboard/all`, `/monitor`, `/admin-console` | `/api/dashboard/*`, `/api/server/*`, `/api/queue/*` 등 |
| **인증 방식** | 세션 로그인 (브라우저 쿠키) | `Authorization: Bearer <API_KEY>` |
| **외부 접근** | 브라우저에서 로그인 필요 | HTTP 헤더 하나로 가능 |
| **프로그래밍 호출** | 어려움 (세션/쿠키 관리) | 쉬움 (Bearer 토큰) |

### 기존 개별 API도 외부에서 호출 가능

대시보드 HTML 페이지가 내부에서 사용하는 API들은 **이미 Bearer 토큰으로 외부 호출이 가능**합니다.

```bash
# 기존 개별 API (40+개) — 외부에서 개별 호출 가능
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/server/stats
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/health
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/status
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/queue/status
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/queue/stats
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/gpu/status
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/orchestrator/workflows
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/drug-batch/status
curl -H "Authorization: Bearer sk-..." https://example.org/ollama/api/provenance/summary
# ... 등 40개 이상
```

### 통합 Dashboard API의 역할

기존 개별 API를 **8개 엔드포인트로 집계**하여 외부 시스템의 호출 횟수와 복잡도를 줄입니다.

| 방식 | 호출 횟수 | 장점 | 단점 |
|------|-----------|------|------|
| **기존 개별 API** | 40+회 | 세밀한 제어 가능 | 호출 많음, 조합 로직 필요 |
| **통합 Dashboard API** | 1~8회 | 최소 호출, 일관된 형식, ETag/캐시 | 커스텀 조합 제한적 |
| **혼합 사용** | 필요한 만큼 | 요약은 통합 API, 상세는 개별 API | - |

**권장**: 통합 API로 전체 현황을 파악하고, 상세 조작(모델 로드/삭제, 큐 제어 등)은 기존 개별 API를 사용합니다.

---

## 공통 응답 형식

모든 엔드포인트는 동일한 envelope 구조를 반환합니다.

```json
{
  "_meta": {
    "endpoint": "/api/dashboard/summary",
    "version": "2.57.0",
    "timestamp": "2026-05-31T12:00:00Z",
    "duration_ms": 45,
    "cache_ttl_sec": 10
  },
  "data": { ... }
}
```

## 공통 파라미터

| 파라미터 | 위치 | 설명 |
|----------|------|------|
| `fields` | query | 반환할 data 키 필터링 (예: `?fields=server,queue`) |
| `compact` | query | `true` 시 프로세스 목록 등 대용량 데이터 제외 |

## ETag / 304 최적화

첫 응답의 `ETag` 헤더를 저장해 두고, 이후 요청에 `If-None-Match`로 전송하면 데이터 변경이 없을 때 **304 Not Modified** (body 없음)를 반환합니다.

```bash
# 첫 요청
curl -i http://localhost:8585/api/dashboard/summary
# → ETag: "a1b2c3d4e5f6g7h8"

# 이후 요청 — 변경 없으면 304 (전송량 0)
curl -H 'If-None-Match: "a1b2c3d4e5f6g7h8"' \
  http://localhost:8585/api/dashboard/summary
# → 304 Not Modified
```

---

## 1. Summary — 전체 현황 요약

```
GET /api/dashboard/summary
```

외부 시스템이 최초로 호출하는 엔드포인트. 서버/모델/큐/파이프라인 전체를 한눈에 파악합니다.

**캐시**: 10초

```bash
curl http://localhost:8585/api/dashboard/summary
```

**Response `data`**:

```json
{
  "server": {
    "healthy": true,
    "cpu_pct": 23.5,
    "ram_pct": 61.2,
    "gpu": {
      "name": "NVIDIA GeForce RTX 5080",
      "utilization_pct": 45,
      "memory_used_mb": 6000,
      "memory_total_mb": 16384,
      "memory_usage_pct": 36.6,
      "temperature_c": 62
    },
    "disk_pct": 34.1
  },
  "ollama": {
    "running": true,
    "loaded_models": 2,
    "available_models": 12
  },
  "queue": {
    "pending": 3,
    "running": 1,
    "completed": 500,
    "failed": 2,
    "worker_alive": true,
    "paused": false
  },
  "orchestration": {
    "workflows_count": 8,
    "active_runs": 0,
    "recent_runs_24h": 5
  },
  "pipeline": {
    "active_run": false,
    "active_mode": "day",
    "db_summary": {
      "drug_codes": 1234,
      "drug_interactions": 567,
      "denial_reasons": 89
    }
  },
  "services": {
    "ollama": true,
    "worker": true,
    "queue": true,
    "tunnel": true,
    "disk": true,
    "database": true
  },
  "servers_reachable": {
    "gpu": true,
    "production": true,
    "his_pacs": false
  }
}
```

**fields 필터링**:

```bash
# 서버 + 큐 정보만
curl "http://localhost:8585/api/dashboard/summary?fields=server,queue"
```

---

## 2. Server — 단일 서버 상세

```
GET /api/dashboard/server
GET /api/dashboard/server?compact=true
GET /api/dashboard/server?fields=stats
```

**캐시**: 5초

```bash
curl http://localhost:8585/api/dashboard/server
```

**Response `data`**:

```json
{
  "stats": {
    "cpu": {
      "load_1m": 1.23,
      "load_5m": 1.05,
      "load_15m": 0.98,
      "cores": 16,
      "usage_pct": 7.7
    },
    "ram": {
      "total_mb": 32768,
      "used_mb": 20480,
      "avail_mb": 12288,
      "usage_pct": 62.5
    },
    "gpu": { "name": "...", "utilization_pct": 45, "..." : "..." },
    "disk": {
      "total_gb": 500.0,
      "used_gb": 170.5,
      "free_gb": 329.5,
      "usage_pct": 34.1
    },
    "uptime": { "seconds": 1234567, "display": "14d 6h 56m" },
    "processes": [
      { "pid": 1234, "cpu": 12.3, "mem": 5.6, "command": "python3 app.py" }
    ]
  },
  "health": {
    "healthy": true,
    "checks": {
      "ollama": { "ok": true },
      "worker": { "ok": true, "paused": false },
      "queue": { "pending": 3, "running": 1, "ok": true },
      "disk": { "used_pct": 34.1, "free_gb": 329.5, "ok": true },
      "tunnel": { "ok": true },
      "database": { "size_mb": 12.5, "ok": true }
    }
  },
  "ollama": {
    "running": true,
    "available_models": [ { "name": "qwen2.5:7b", "size": 4700000000 } ],
    "running_models": [ { "name": "qwen2.5:7b" } ]
  },
  "image": { "comfyui_available": false }
}
```

> `?compact=true` 사용 시 `processes` 배열이 제외되어 응답이 ~60% 작아집니다.

---

## 3. Servers — 멀티서버 비교

```
GET /api/dashboard/servers
GET /api/dashboard/servers?servers=local,gpu
```

4개 서버를 ThreadPoolExecutor로 **병렬 조회** (5초 타임아웃).

**캐시**: 15초

| 서버 | 키 | 설명 |
|------|-----|------|
| Local (iMac) | `local` | 현재 서버 |
| GPU Server | `gpu` | Ubuntu RTX 5080 |
| Production | `prod` | Linode VPS |
| HIS/PACS | `his` | 의료 서버 |
| iMac Ollama | `imac` | iMac M1 Ollama |

```bash
# 전체 서버
curl http://localhost:8585/api/dashboard/servers

# GPU + 로컬만
curl "http://localhost:8585/api/dashboard/servers?servers=local,gpu"
```

**Response `data`**:

```json
{
  "local": {
    "stats": { "cpu": {...}, "ram": {...}, "gpu": {...}, "disk": {...} },
    "health": { "healthy": true, "checks": {...} },
    "reachable": true
  },
  "gpu": {
    "stats": { "cpu": {...}, "ram": {...}, "gpu": {...} },
    "health": { "healthy": true, "checks": {...} },
    "reachable": true
  },
  "his": {
    "reachable": false,
    "error": "urlopen error timed out"
  }
}
```

---

## 4. Models — 모델 + GPU 리소스

```
GET /api/dashboard/models
```

**캐시**: 30초

```bash
curl http://localhost:8585/api/dashboard/models
```

**Response `data`**:

```json
{
  "running": true,
  "available_models": [
    { "name": "qwen2.5:7b", "size": 4700000000, "..." : "..." }
  ],
  "running_models": [
    { "name": "qwen2.5:7b", "size_vram": 4500000000 }
  ],
  "gpu": {
    "vram_used_mb": 6000,
    "vram_total_mb": 16384,
    "gpu_util_pct": 45,
    "temp_c": 62
  },
  "resource_manager": { "..." : "..." },
  "chat_queue": { "..." : "..." }
}
```

---

## 5. Queue — 큐 상태 + 통계

```
GET /api/dashboard/queue
GET /api/dashboard/queue?include=recent_jobs
```

**캐시**: 3초

```bash
curl http://localhost:8585/api/dashboard/queue

# 최근 작업 20건 포함
curl "http://localhost:8585/api/dashboard/queue?include=recent_jobs"
```

**Response `data`**:

```json
{
  "status": {
    "pending": 3,
    "running": 1,
    "completed": 500,
    "failed": 2,
    "llm_pending": 2,
    "image_pending": 1,
    "worker_alive": true,
    "paused": false
  },
  "stats": {
    "total": 500,
    "total_tokens": 61000,
    "total_duration_ms": 5920000,
    "avg_tokens": 122.0,
    "avg_duration_ms": 11840.0,
    "avg_tok_per_sec": 10.3,
    "jobs_per_model": {
      "qwen2.5:7b": {
        "count": 300,
        "tokens": 45000,
        "avg_tokens": 150.0,
        "avg_duration_ms": 8500.0,
        "tok_per_sec": 17.6
      }
    },
    "eta_sec": 45,
    "recent_jobs": []
  }
}
```

> `recent_jobs`는 `?include=recent_jobs` 전달 시에만 포함됩니다.

---

## 6. Orchestration — 워크플로우 + 실행 이력

```
GET /api/dashboard/orchestration
GET /api/dashboard/orchestration?limit=50
```

**캐시**: 30초

```bash
curl http://localhost:8585/api/dashboard/orchestration
```

**Response `data`**:

```json
{
  "workflows": [
    {
      "name": "medical_soap",
      "display_name": "SOAP 노트 생성",
      "description": "환자 증상으로 SOAP 노트 자동 생성",
      "steps": 4
    }
  ],
  "workflows_count": 8,
  "active_runs": [],
  "history": [
    {
      "id": "run-abc123",
      "workflow": "medical_soap",
      "status": "completed",
      "steps_completed": 4,
      "steps_total": 4,
      "total_duration_ms": 12500,
      "created_at": "2026-05-31T10:00:00"
    }
  ]
}
```

---

## 7. Pipeline — 약물 파이프라인

```
GET /api/dashboard/pipeline
```

**캐시**: 60초

```bash
curl http://localhost:8585/api/dashboard/pipeline
```

**Response `data`**:

```json
{
  "status": {
    "current_run": null,
    "progress": null,
    "active_mode": "day",
    "db_summary": {
      "drug_codes": 1234,
      "drug_interactions": 567,
      "denial_reasons": 89
    }
  },
  "stats": {
    "drug_codes": { "total": 1234 },
    "drug_interactions": { "total": 567 },
    "denial_reasons": { "total": 89 },
    "by_source": { "HIRA": 800, "MOHW": 300, "AI": 134 },
    "avg_confidence": 0.8756,
    "recent_runs": [
      { "id": 1, "run_type": "daily_collect", "status": "completed", "total_committed": 15, "started_at": "..." }
    ]
  },
  "schedule": {
    "schedules": { "daily_collect": { "hour": 2, "interval": "daily" } },
    "night_mode": { "..." : "..." },
    "active": "day"
  }
}
```

---

## 8. Stream — 통합 SSE 실시간 스트림

```
GET /api/dashboard/stream
GET /api/dashboard/stream?token=CHANGE_ME
```

Server-Sent Events (EventSource) 방식의 실시간 스트림. 기존 monitor 이벤트 + 주기적 서버 stats를 통합 전송합니다.

> SSE는 `Authorization` 헤더를 지원하지 않으므로, 외부 접속 시 `?token=` 쿼리 파라미터를 사용합니다.

```bash
# 터미널에서 테스트
curl -N http://localhost:8585/api/dashboard/stream
```

```javascript
// JavaScript EventSource
const es = new EventSource('/api/dashboard/stream');
es.onmessage = (e) => {
  const data = JSON.parse(e.data);
  console.log(data);
};
```

**이벤트 형식**:

```
data: {"type":"connected","version":"2.57.0"}

data: {"domain":"server","type":"stats","ts":"2026-05-31T12:00:10Z","cpu_pct":23.5,"ram_pct":61.2,"gpu":{...},"disk_pct":34.1}

data: {"ts":"2026-05-31T12:00:15Z","type":"job","job_id":"abc12345","model":"qwen2.5:7b","status":"completed","duration_ms":4800}
```

| 이벤트 domain | 설명 | 주기 |
|---------------|------|------|
| (없음) | 연결 성공 | 1회 |
| `server` | 서버 리소스 stats | 10초 |
| (기존 monitor) | job 완료/실패, 요청 로그 | 실시간 |

---

## 외부 시스템 연동 가이드

### Python

```python
import requests

BASE = "https://example.org/ollama"
HEADERS = {"Authorization": "Bearer sk-your-api-key"}

# 전체 현황
r = requests.get(f"{BASE}/api/dashboard/summary", headers=HEADERS)
summary = r.json()["data"]
print(f"CPU: {summary['server']['cpu_pct']}%")
print(f"Queue pending: {summary['queue']['pending']}")

# ETag 활용 (폴링 최적화)
etag = r.headers.get("ETag")
r2 = requests.get(f"{BASE}/api/dashboard/summary",
                   headers={**HEADERS, "If-None-Match": etag})
if r2.status_code == 304:
    print("No changes")
else:
    summary = r2.json()["data"]

# fields 필터링 — 서버+큐만
r = requests.get(f"{BASE}/api/dashboard/summary?fields=server,queue",
                 headers=HEADERS)

# SSE 스트림
r = requests.get(f"{BASE}/api/dashboard/stream?token=CHANGE_ME",
                 stream=True)
for line in r.iter_lines():
    if line and line.startswith(b"data:"):
        data = json.loads(line[5:])
        print(data)
```

### JavaScript / React

```javascript
// 폴링 방식 (10초 간격)
let lastEtag = null;

async function fetchDashboard() {
  const headers = { 'Authorization': 'Bearer sk-your-api-key' };
  if (lastEtag) headers['If-None-Match'] = lastEtag;

  const res = await fetch('https://example.org/ollama/api/dashboard/summary', { headers });

  if (res.status === 304) return null; // no changes

  lastEtag = res.headers.get('ETag');
  const { data } = await res.json();
  return data;
}

setInterval(async () => {
  const data = await fetchDashboard();
  if (data) updateUI(data);
}, 10000);

// SSE 방식 (실시간)
const es = new EventSource(
  'https://example.org/ollama/api/dashboard/stream?token=CHANGE_ME'
);
es.onmessage = (e) => {
  const event = JSON.parse(e.data);
  if (event.domain === 'server') updateServerMetrics(event);
  if (event.type === 'job') updateJobList(event);
};
```

### cURL 원라이너

```bash
# 전체 현황
curl -s http://localhost:8585/api/dashboard/summary | python3 -m json.tool

# 서버 상세 (compact)
curl -s "http://localhost:8585/api/dashboard/server?compact=true" | python3 -m json.tool

# GPU + 로컬 서버만
curl -s "http://localhost:8585/api/dashboard/servers?servers=local,gpu" | python3 -m json.tool

# 큐 + 최근 작업
curl -s "http://localhost:8585/api/dashboard/queue?include=recent_jobs" | python3 -m json.tool

# SSE 스트림
curl -N http://localhost:8585/api/dashboard/stream
```

---

## 엔드포인트 요약

| # | Endpoint | 용도 | 캐시 | 주요 파라미터 |
|---|----------|------|------|---------------|
| 1 | `GET /api/dashboard/summary` | 전체 현황 요약 | 10s | `fields` |
| 2 | `GET /api/dashboard/server` | 단일 서버 상세 | 5s | `compact`, `fields` |
| 3 | `GET /api/dashboard/servers` | 멀티서버 비교 | 15s | `servers` |
| 4 | `GET /api/dashboard/models` | 모델 + GPU | 30s | `fields` |
| 5 | `GET /api/dashboard/queue` | 큐 상태/통계 | 3s | `include` |
| 6 | `GET /api/dashboard/orchestration` | 워크플로우/이력 | 30s | `limit` |
| 7 | `GET /api/dashboard/pipeline` | 약물 파이프라인 | 60s | `fields` |
| 8 | `GET /api/dashboard/stream` | SSE 실시간 | - | `token` |
