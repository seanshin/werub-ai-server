# GPU 서버 → iMac 위임 활성화 — 배포 Runbook

> **예정일**: 2026-06-08 | **대상**: GPU 서버 (192.0.2.108) + iMac M1
> **변경 성격**: git pull(휴면 코드) + config 4키 + **통합 터널** — **롤백 = config 키 제거 (재배포 불필요)**
> **영향**: gunicorn 재시작 1회 (SSE 순단 — graceful drain 동작), 그 외 기존 API 무영향
>
> ⚠️ **연계 검토 결과 (2026-06-07, `gpu-imac-link.plan.md`)**: 기존 8586 경량 오프로드·대시보드
> iMac 패널이 **터널 미가동으로 죽어있음** 확인 — 본 배포에서 통합 터널(`com.werub.link-tunnel.plist`,
> -R 8586/8587 + -L 8590)로 함께 복구한다. **상세 작업 순서는 `gpu-imac-link.plan.md` §4가 우선
> (11단계 — SSH 키 확인 → 임시 터널 → git pull → L1/L2 복구 확인 → config → 위임 검증 → 터널 영구화 → 동일성 100장 → 24h 관찰)**

## 사전 확인 (iMac에서 — 배포 전)

```bash
# 1. 워커 가동 확인
curl -s http://127.0.0.1:8587/worker/health | python3 -m json.tool | head -5
# → "ok": true 확인

# 2. 토큰 값 확보 (GPU config에 넣을 값)
cat ~/.werub_worker_token
```

## 배포 절차 (GPU 서버에서)

```bash
ssh deploy@192.0.2.108 -p 819
cd <WeRUBLLMManager 경로>

# 1. 코드 반영 — 오늘자 커밋 13건. apple_worker 코드는 config 없으면 완전 휴면
#    (tests/test_apple_worker.py::test_config_absent_returns_none로 보증)
git pull --ff-only

# 2. 회귀 테스트 (선택 권장 — 기존 157건 + 신규)
venv/bin/python -m pytest tests/ -q 2>&1 | tail -3
# macos 마커 테스트는 Linux에서 자동 스킵됨

# 3. gunicorn 재시작 (SSE drain 순단 — 한가한 시간대 권장)
#    기존 배포 절차와 동일

# 4. 여기까지는 동작 변화 0 — detect는 기존 CPU 경로 그대로 (config 키 없음)
```

## 터널 구성 (iMac에서)

```bash
# 임시 가동 (검증용)
ssh -N -R 8587:127.0.0.1:8587 deploy@192.0.2.108 -p 819 &

# 검증 후 영구화: setup/com.werub.worker-tunnel.plist 생성 (gpu-tunnel.plist 패턴)
# → launchctl load + watchdog_mac.sh가 자동 감시 (이미 조건부 코드 준비됨)
```

## 위임 활성화 (GPU 서버에서)

```bash
# config.json에 추가 (iMac 토큰 값 사용)
"apple_worker_enabled": true,
"apple_worker_url": "http://localhost:8587",
"apple_worker_token": "<iMac ~/.werub_worker_token 값>",
"apple_worker_delegate": ["detect"]
# config는 핫 리로드 대상 아님 → gunicorn 재시작 1회 더 필요 여부는
# load_config() 호출 시점 확인 (apple_worker는 호출마다 동적 로드라 재시작 불필요)
```

## 검증

```bash
# GPU 서버에서 — 위임 동작 확인
curl -s -X POST http://localhost:8585/api/detect/image -F 'image=@test.png' | python3 -m json.tool | grep -E "engine|device|ai_probability"
# 기대: "engine": "imac", "device": "mps"

# 폴백 확인 — iMac에서 터널 중단 후 재호출
# 기대: engine 필드 없음 (로컬 CPU 처리), 오류 0

# 30초 후 터널 복구 → 재호출 → engine=imac 복귀 (헬스 캐시 TTL 30s)
```

## 롤백

| 수준 | 방법 |
|------|------|
| 위임만 끄기 | config `apple_worker_enabled: false` — 즉시 기존 경로 (재배포 불필요) |
| 코드 롤백 | `git revert e52a3eb` (위임 분기) — 다른 커밋은 docs/iMac 전용이라 GPU 무영향 |

## 배포 후 권장 (iMac에서)

```bash
# 동일성 확대 측정 (갭 분석 잔여 — 100장 cpu vs mps)
# 24h 안정성 관찰: tail -f logs/watchdog_mac.log (무중단 확인)
```
