# Release v2.86.13 — RAG 임베딩 500 수정 + DB 하드닝

> 2026-06-09 | 이전: v2.86.11 | fix + hardening (v2.86.12 → v2.86.13)

비밀번호 인증 시리즈(v2.86.8~11, `docs/RELEASE_v2.86.11.md`) 이후의 버그 수정·하드닝.
전체 세션 종합은 `docs/SESSION_REPORT_20260609.md` 참조.

---

## v2.86.12 — RAG 임베딩 500 수정 (bge-m3 VRAM OOM) ⭐

### 증상
- `/api/rag/ask`, `/api/rag/search` → 서버 **500** (지속)
- `/api/medical/reference`(의학 지식 RAG)는 **200** 정상 — 대조군

### 근본 원인
```
embed_text → Ollama POST /api/embed (model=bge-m3)
  → 글로벌 OLLAMA_CONTEXT_LENGTH=8192(×NUM_PARALLEL)가 임베딩 모델에도 적용
  → bge-m3(567M) KV를 ~4.5GB 예약
  → qwen2.5:14b(12.2GB) 상주 중 free 3.6GB에 못 들어감
  → cudaMalloc failed: out of memory → "llama runner terminated" → Ollama 500
```
medical/reference는 임베딩을 쓰지 않고 이미 로드된 LLM으로 직접 생성하므로 영향 없었다.

### 수정 (코드 — 서버 config·재색인 불필요)
- `client/rag.py` `embed_text`: `/api/embed` 요청에 `options.num_ctx=2048`(env `RAG_EMBED_NUM_CTX`) 명시
  → bge-m3 풋프린트 **4.5GB → 1.16GB**, LLM과 공존
- `routes/rag.py`: `rag_ask`/`rag_search_api`의 `rag_search` 호출을 try/except로 →
  임베딩 실패 시 raw 500 대신 **503 + 명확한 메시지**

### 검증 (라이브)
- bge-m3 embed 200 / 1024차원, num_ctx 512·2048·4096 모두 ~1.1~1.2GB
- `/api/rag/ask` 200(sources 3), `/api/rag/search` 200
- VRAM: bge-m3 1.16GB가 qwen2.5:14b/7b와 공존

### 교훈
- `NUM_PARALLEL 4→2`로 낮춰도 임베딩 OOM 그대로 — 임베딩 풋프린트는 parallel이 아닌
  **context에 지배**. parallel은 4 원복(처리량 무손실). 진짜 레버는 임베딩 호출의 per-request `num_ctx`.
- 새 임베딩 호출 경로 추가 시 반드시 작은 `num_ctx` 명시.

---

## v2.86.13 — sqlite connect timeout 일관성 (10곳)

`timeout` 미명시 `sqlite3.connect` 10곳에 `timeout=10` 추가.
Python sqlite3 기본 timeout은 5.0s(무한대기 아님) → 10s로 명시·일관화.
채팅 행 fork+shm 픽스(v2.86.6)의 belt-suspenders 방어.

- `app.py`: `get_db`(DB_PATH), `_get_users_db`(USERS_DB_PATH)
- `routes`: story / novel / meeting(`_init_db`) / training(TRAINING_DB·USERS_DB·MOD_DB) 
- `core`: meeting_pipeline / hr_law

부수: 서버 OLLAMA env 백업파일(`.bak-20260609`) 삭제, `OLLAMA_NUM_PARALLEL=4` 정합 확인.

**won't-fix**: `graceful_shutdown`의 `threading._shutdown` Exception ignored 노이즈는
`sys.exit(0)` 시 데몬 스레드 teardown의 본질적 무해 노이즈 — SSE drain 로직(v2.75.0
인시던트 픽스)을 건드리는 위험 대비 이득이 없어 미수정.

---

## 검증 요약
- check_syntax 160 .py, `tests/test_password.py` 8 passed
- GPU 라이브: health 200@4.6ms, VERSION 2.86.13, NRestarts=0, 워커 nlwp 39, 오늘 CRIT/000 0건
- GPU HEAD `6f9fb95` (로컬·원격 동기)

## 잔여(백로그)
- ChromaDB 지식 색인(데이터 소스 필요·코드 아님) / orchestrator Phase 2/3(operator 조율) / HIS 스트리밍(외부 대기)
