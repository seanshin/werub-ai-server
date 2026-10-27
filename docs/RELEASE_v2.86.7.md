# Release v2.86.7 — GPU Ollama 성능 튜닝 (실측 기반)

> 2026-06-08 | 이전: v2.86.6 | 성능/운영 (서버 config 튜닝 + 문서)

채팅 행 incident(v2.86.6, preload_app=False)로 안정화 후, 성능을 **실측 프로파일링**해 3축 튜닝.

## 실측 프로파일 (qwen2.5:14b, RTX 5080 16GB)
| 지표 | 값 | 해석 |
|---|---|---|
| 단일 생성 | **89 tok/s** | 기존 문서 "15 tok/s"는 오기 (flash+5080로 빠름) |
| 동시 4 | 각 ~45 = 합 ~180 tok/s | GPU **메모리대역폭 바운드**(util 96%/mem 87%) |
| 프롬프트 처리 | 1227 tok/s | 병목 아님 |
| 스트리밍 TTFT | **0.14s** | 비스트림 11s 대비 **체감 80배** |
| 핸들러 오버헤드 | ~0.3s | 도메인분류=키워드(LLM아님), 세션DB 경미 |

## 적용 (server config — drop-in env)
> `/etc/systemd/system/ollama.service.d/environment.conf` (이 drop-in이 `/etc/default/ollama`를 shadow → 여기 수정 + `daemon-reload` + `systemctl restart ollama`)

```ini
[Service]
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=4"        # 2→4: 동시 4명 무대기
Environment="OLLAMA_CONTEXT_LENGTH=8192"   # 긴 의료맥락 품질
Environment="OLLAMA_KEEP_ALIVE=5m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"   # 2/KEEP_ALIVE=-1는 16GB 초과위험 → 금지
Environment="OLLAMA_GPU_OVERHEAD=512m"
```
- **VRAM: parallel4+ctx8192 로드 시 12.2GB/16GB**(KV 사전할당, peak 동일 → OOM 없음, 여유 4GB)
- 검증: chat 동시 6/6 200, 4 동시 장문 정상

## ⭐ 호출측 권장 — 스트리밍 (체감 80배)
```bash
# 권장 (첫 토큰 0.14s)
POST /api/chat  {"model":"qwen2.5:14b","messages":[...]}     # stream 생략=true
# 지양 (11s 무응답 후 일괄)
POST /api/chat  {..., "stream": false}
```
NDJSON 스트림을 토큰 단위로 출력 → 사용자 즉시 반응 확인. **HIS 등 연동측 적용 권장.**

## 3축 요약
1. **처리량** parallel=4 (동시 사용자)
2. **체감** 스트리밍 TTFT 0.14s (호출측 stream:true)
3. **품질** ctx=8192 (긴 맥락)

## 검토했으나 미채택
- 7b 전환: 14b가 이미 89 tok/s로 빠름 → 불필요(14b 품질 유지)
- speculative decoding: Ollama 0.21 미지원
- Redis: 단일 워커에선 이득 없음 → 백로그(트리거: 다중 워커/서버, SQLite 실측병목)

## 카운트
- VERSION 2.86.6 → **2.86.7** | 코드 변경 없음(서버 config 튜닝 + 문서). 튜닝값 보존용 릴리즈
