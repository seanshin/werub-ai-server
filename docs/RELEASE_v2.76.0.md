# Release v2.76.0 — API 키 만료 관리 (연장/무제한) + Invalid Date 저장버그 수정

> 릴리즈: 2026-06-07 | 이전: v2.75.0

## 요약

발급된 API 키의 **만료 기간을 연장하거나 무제한으로 전환**하는 기능을 추가했다.
지금까지는 키를 발급할 때만 만료를 정할 수 있어, 만료가 임박한 키는 삭제 후 재발급해야 했다.
함께, 일부 키의 만료/생성 시각이 콘솔에서 **"Invalid Date"** 로 표시되던 저장 형식 버그를
바로잡았다(원인: 타임스탬프를 `...+00:00Z` 이중 접미사로 저장 → JS `Date` 파싱 실패).

---

## ✨ 신규 — API 키 만료 기간 연장/무제한

`POST /api/keys/{key_id}/expiry` (localhost only)

```bash
# 지금부터 365일 후로 연장(갱신)
curl -X POST http://localhost:8585/api/keys/12/expiry \
  -H "Content-Type: application/json" -d '{"days": 365}'
# → {"ok": true, "expires_at": "2027-06-07T...Z"}

# 무제한(만료 없음)으로 변경
curl -X POST http://localhost:8585/api/keys/12/expiry \
  -H "Content-Type: application/json" -d '{"never": true}'
# → {"ok": true, "expires_at": null}
```

- `days`(int>0): **현재 시각 기준** N일 후로 만료일 재설정(연장/갱신).
- `never: true` 또는 `days: 0`: 무제한(`expires_at = NULL`).
- 검증 캐시(`_key_cache`)는 TTL 60초로 자동 반영 — 기존 `toggle`/`delete`와 동일 정책.
- 만료 시각은 올바른 ISO8601(`...Z`)로 저장.

### Admin 콘솔 UI

- 키 목록 각 행에 **기간** 버튼 추가 → 일수 입력(0=무제한) 다이얼로그.
- 경량 `showPrompt()` 모달 헬퍼 신규(기존 `showConfirm` 패턴 미러링).

## 🐛 수정 — "Invalid Date" 표시 + 저장 형식

- **저장**: `generate_api_key`(app.py), `create_key`(routes/ollama.py)가 `datetime.isoformat() + "Z"`
  로 저장해 `2026-..+00:00Z`(이중 접미사)가 됨 → `.isoformat().replace("+00:00", "Z")` 로 수정.
- **표시**: 프론트 `formatDate()` + 키 목록 만료 판정에 `_cleanTs()` 보정 추가 →
  기존에 잘못 저장된 행도 즉시 정상 표시(무효 시 'N/A'). 만료 검증 로직(`verify_api_key`)은
  날짜 prefix 비교라 무변경 — 회귀 없음.

---

## 변경 파일

```
routes/ollama.py   — POST /api/keys/{id}/expiry 엔드포인트, create_key 저장형식 수정
app.py             — generate_api_key 저장형식, formatDate/_cleanTs, 기간 버튼,
                     setKeyExpiry(), showPrompt 모달, VERSION 2.76.0
CLAUDE.md/README   — 엔드포인트 문서 + 버전
```

## 운영 메모

- 배포: GPU `git pull` + 저트래픽 창 재시작(v2.75.0 graceful drain 적용됨).
- 검증: 콘솔 키 목록에서 **기간** 버튼 → 일수 입력 → EXPIRES 갱신 확인. 기존 "Invalid Date"
  행이 정상 날짜/`never`로 표시되는지 확인.
