# Release v2.85.0 — 오케스트레이션 운영 가동 + 관리 콘솔 + iMac 플랫폼 통합

> 2026-06-08 | 이전: v2.84.0

## 하이라이트

### 1. 🟢 Node Orchestration 운영 가동 — "GPU만 호출하면 iMac에 자동 배정"
v2.84.0에서 구축한 오케스트레이터를 **실전 활성화**. detect가 실제로 iMac 워커로 자동 위임된다.

- **단일 진입점**: 호출자는 GPU API만 호출 → detect 자동 iMac 위임(engine=imac-worker, warm ~0.6s), iMac 다운 시 GPU 로컬 폴백. **호출 코드/URL 무변경.**
- **정책**: `config/orchestrator.json` detect/mark `policy=prefer_imac` (offload 의도 정합 — auto는 동률 시 로컬 우선이라 한가한 GPU가 offload 안 함).
- **영구 브릿지**: `ollama-worker-bridge.service` (systemd, `Restart=always`, 재부팅 생존) — GPU `localhost:8587` → prod:8587 → iMac worker.
- **하드닝**: 워커 `detector.available`로 detect capability 게이팅 — 워커 ML 다운 시 breaker 트립 churn 제거.
- 활성화: `scripts/activate_imac_delegation.sh` (config 안전 설정 + 검증 + 롤백, 재시작 불필요).

### 2. 관리 콘솔 (일체형 관리)
- Admin 대시보드 **오케스트레이터 카드** — 노드×capability×라우팅 실적(위임/로컬/폴백/오류)·지연 EWMA·breaker, 30s 폴링. (`templates/admin.html` + `static/js/main.js`)

### 3. iMac 플랫폼 통합 (워커측 협업)
- `core/platform.py` — GPU/Darwin 플랫폼 자동 분기 + 플랫폼 전용 Blueprint 로딩
- `routes/gateway.py` (Smart Gateway), `routes/system.py`, `core/apple_silicon.py`, `core/coreml_embed.py`, `client/imac_client.py`
- 워커 안정화: `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`(gunicorn fork ObjC 크래시), `DETECTOR_DEVICE=cpu`(MPS jetsam SIGKILL 회피), 역터널 8590→11435

### 4. 문서/호출법
- `docs/ORCHESTRATOR_GUIDE.md` 신규 — 호출법/토폴로지/활성화/정책/운영/로드맵
- CLAUDE.md/README "Node Orchestration — 단일 진입점 자동 분산" 섹션

## 호출 방법 (요약)
```bash
# 평소처럼 GPU만 호출 → detect 자동 iMac 위임 + 폴백
curl -X POST http://localhost:8585/api/detect/image -F 'image=@x.png'
# 라우팅 상태
curl http://localhost:8585/api/orchestrator/nodes
```

## 운영 주의
- **GPU 코드 반영 = 전체 재기동** (`preload_app=True` → SIGHUP은 코드 reload 안 함). 마스터 TERM→systemd 재기동 또는 `sudo systemctl restart`. 재기동 전 `import app` dry-run 권장.
- 오케스트레이터 불변식: 노드0=기존동작100% / 폴백 무전파 / Darwin 가드.

## 카운트
- VERSION 2.84.0 → **2.85.0** | API 551+ | Blueprint 33 | Core 33 (+gateway/platform/system은 플랫폼 통합)

## 다음
- Phase 2: 워커 `/worker/run/<cap>` generic + mark/stt/embed 편입 (v2.86)
- gateway(Smart Gateway) ↔ orchestrator 역할 경계 정리 검토
