# Release v2.100.0 — 식약처 외부 연동 운영급 격상 (자동 동기화)

**2026-06-12** | 이전: [v2.99.0](RELEASE_v2.99.0.md)

## 배경

지금까지 DUR 8유형·e약은요 정본은 **수동 1회성 적재**(env 키)로 들어왔다. 외부 서비스
연동(식약처 data.go.kr)이 약물 안전의 핵심인 만큼, **운영급 자동 동기화 파이프라인**으로 격상.

## 변경 사항 (`core/dur_sync.py` 신규)

### 1. 안전 갱신 (핵심)
- 각 소스(DUR 8유형 + e약은요) 전량 `fetch` → **건수 sane 검증(소스별 min_count)** 통과 시에만
  `clear`+교체. **API 실패/부분응답 시 기존 정본 데이터 보존**(빈 테이블·반토막 데이터 방지).
- 안전 핵심: clear(DELETE)는 전량 fetch 성공 **후에만** 실행 → 외부 장애가 운영 데이터를 훼손하지 않음.

### 2. 동기화 메타 + 관측
- `dur_sync_meta` 테이블(소스별 status `ok`/`skip`/`error`·count·last_sync_at·duration·last_error).
- `GET /api/medical/dur/sync-status` — 소스별 현황 + 키 구성 여부 + running.
- Admin **식약처 외부 연동 카드**(키 구성·마지막 동기화·소스 ok 비율).

### 3. 주기 스케줄러 + 수동 트리거
- `start_dur_sync_scheduler(interval_days=7, initial +10분)` — daemon, app.py 기동(키 있고
  `datago_sync_enabled!=false`). config `datago_sync_interval_days`.
- `POST /api/medical/dur/sync` — 수동 백그라운드 갱신(202).

### 4. 서비스키 관리 (시크릿)
- `config.json datago_api_key`(gitignored) → env `DUR_API_KEY` 폴백. 코드 하드코딩 없음.

## 운영 설정 (GPU)

```bash
# config.json(gitignored)에 키 추가 후 재기동 → 스케줄러 자동 기동
# {"datago_api_key": "<승인키>", "datago_sync_enabled": true, "datago_sync_interval_days": 7}
curl -s http://localhost:8585/api/medical/dur/sync-status     # 현황
curl -s -X POST http://localhost:8585/api/medical/dur/sync    # 수동 갱신(백그라운드)
```

## 검증
- iMac: `pytest tests/test_dur_sync.py` → 5 passed(건수 이상/fetch 실패 시 기존 보존 검증).
- GPU(배포 후): config 키 설정 → sync-status configured=true, 수동 sync 후 8유형+e약은요 ok.
