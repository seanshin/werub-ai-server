# v2.124.31 — API 키 만료 D-7 경보 (조용한 통합 단절 재발 방지)

2026-07-22 | 계기: v2.124.30 점검서 appServer 키 만료 단절 실측 → 운영자 지시

## 배경

07-22 점검서 `appServer` 키(90일 만료)가 7/19 만료돼 **활성 통합이 3일간 401
폭주(일 ~12K건)를 아무 경보 없이 지속**한 것을 실측. 만료 설정 키는 임박 시점에
아무 신호가 없어 라이브 통합이 조용히 끊기는 구조적 공백.

## 구현 (`routes/system.py` `_perf_alerts` 확장)

- **`_key_expiry_findings(rows, now, warn_days, used_days)`** 순수 판정 분리:
  - **expiring**: 유효하나 warn_days(기본 7·config
    `monitoring_thresholds.key_expiry_warn_days`) 이내 만료 도래 → **warning**
    ("연장 또는 로테이션 필요").
  - **broken**: 이미 만료 + 최근 7일 사용 흔적(`last_used_at`) → **critical**
    ("라이브 통합 단절(401) 의심") — appServer 클래스 직격.
  - **휴면 만료 키는 무경보**(hwpX 클래스 — 46일 미사용 만료는 정상 소멸).
- 타임스탬프는 기존 키 검증 쿼리와 동일한 ISO 사전식 문자열 비교(일 단위 판정에
  'Z'/'+00:00Z' 혼재 무영향). 쿨다운 6h(quality_index_low와 동일 결).
- `/api/health` perf 블록에 `keys_expiring`/`keys_expired_in_use` 노출.

## 검증

- 회귀 `tests/test_key_expiry_alert.py` **5**(임박/단절/휴면/미사용/경계+튜닝) —
  GPU 5 passed.
- **라이브 E2E**: 기본 상태 위양성 0(hwpX 휴면만료 무경보) → 임시 D-3 키 생성
  → `keys_expiring: ['_expiry_alert_e2e']` 발화 확인 → 키 삭제 → 무경보 복귀·
  health 200. (E2E 부산물로 alerts_history에 warning 1건=경보 경로 실증)
- 배포: graceful HUP·NRestarts 0.

## 운용

- 만료 임박 대응: Admin 콘솔 키 목록 "기간" 버튼 또는
  `POST /api/keys/<id>/expiry {"days": N}` / `{"never": true}`.
- 튜닝: config `monitoring_thresholds.key_expiry_warn_days`(무재배포).
