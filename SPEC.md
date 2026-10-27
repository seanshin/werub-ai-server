# WeRUBLLMManager Specification

**Version**: v2.34.0
**Updated**: 2026-05-08

## API Summary

| Category | Routes | Blueprint |
|----------|--------|-----------|
| Ollama Proxy | 22 | routes/ollama.py |
| Queue | 16 | routes/queue.py |
| Image | 15 | routes/image.py |
| HR Law | 23 | routes/hr_law.py |
| Game NPC | 40 | routes/game.py |
| Training | 32 | routes/training.py |
| System | 14 | routes/system.py |
| Activity | 6 | routes/activity.py |
| RAG | 5 | routes/rag.py |
| Vision | 2 | routes/vision.py |
| Web UI (app.py) | 20 | app.py |
| Medical AI | 22 | routes/medical.py |
| Medical CLI | 7 | routes/medical_cli.py |
| Symptom Triage | 4 | routes/triage.py |
| Coding AI | 16 | routes/coding.py |
| Orchestrator | 7 | routes/orchestrator.py |
| **Total** | **~218** | |

## Core Modules

| Module | Purpose |
|--------|---------|
| core/config.py | 설정 로드/저장 |
| core/cache.py | TTLCache (LLM 500건/1h, 이미지 모델 5m) |
| core/korean.py | 한국어 필터, 품질 검증, 자동 재시도 |
| core/security.py | API 키 생성/검증 |
| core/queue_engine.py | ChatFairQueue (글로벌 3, 유저 1) |
| core/logger.py | 로깅 설정 |

## Authentication

- **API**: Bearer token (`Authorization: Bearer sk-xxx`)
- **Web UI**: Flask session (PBKDF2-HMAC-SHA256, 200K rounds)
- **Game**: X-Game-Key header
- **HR Law**: X-Tenant-Key header

## Database

| File | Purpose | Size |
|------|---------|------|
| queue.db | 작업 큐, API 키 | ~2MB |
| hr_law.db | 법률 데이터, 테넌트 | 408KB |
| game.db | NPC, 히로인, 시나리오 | 172KB |
| training.db | 학습 이력 | 20KB |
| clinic_codes.db | KCD/EDI/진료과/매핑 코드 | ~50KB |
| medical.db | 의료 테넌트/상담/지식/감사 | ~100KB |
| users.db | 사용자, 역할, 권한 | 16KB |

All SQLite WAL mode, busy_timeout=5000ms.

## Model Benchmark (2026-05-06)

| Model | Pass | Korean | Speed | VRAM |
|-------|------|--------|-------|------|
| qwen2.5:14b | 6/6 | 99% | 73 tok/s | 9.0GB |
| exaone3.5:7.8b | 6/6 | 99% | 35 tok/s | 4.8GB |
| EEVE-10.8b Q8 | 5/6 | 98% | 24 tok/s | 11GB |
| solar:10.7b | 2/6 | 62% | 22 tok/s | 6.1GB |
| gemma3:12b | 1/6 | 81% | 16 tok/s | 7.6GB |

## Medical Models (2026-05-06)

| Model | Size | Clinical | Status |
|-------|------|----------|--------|
| medllama2 | 3.8GB | Clinical analysis + references | Installed |
| meditron | 3.8GB | Differential diagnosis, 188tok/s | Installed |
| OpenBioLLM-8B | 4.9GB | Base model, no inference | Removed |

Dual loading: qwen2.5(10GB) + meditron(5GB) = 15GB/16GB OK.
OLLAMA_MAX_LOADED_MODELS=2.

## Planned: Medical CLI v2
- Code DB: KCD 500+, EDI 300+, drugs 200+
- Role handlers: billing calc, SBAR nursing, Rx recommend, claim rules
- Multi-turn context, shortcuts, vital alerts
- See: docs/plan_medical_cli_v2.md

## Integration Test

53/53 endpoints passed (GET 43 + POST 7 + Auth 3).
