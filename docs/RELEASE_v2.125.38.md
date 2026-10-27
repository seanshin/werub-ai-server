# v2.125.38 — 잔여 항목 전수 의사결정 + M6 고비용 레이트리밋 + heavy 백업 A/B 후 정리

2026-09-03 · 라이브 `2.125.38`(commit `1039e1b`) · 게이트 **177/177** · 회귀 **2094 passed**(신규 4)

"남은 부분 디테일 검토 후 의사결정" 세션 — 잔여 항목 전부를 검토해 결정하고 결정분을
집행했다. **결정 기록 정본 = `docs/01-plan/references/remaining-items-decisions-2026-09-03.md`**.

## 1. heavy 백업 태그 — A/B 동률 확인 후 삭제 (23GB 회수)

레지스트리는 최신 manifest만 제공하므로 백업 태그를 지우면 구판은 영구 복원 불가 —
**지우기 전에, 백업이 있는 동안만 가능한 구판 vs 신판 A/B**를 실행했다(기준 사전 확정:
신≥구=삭제·2문항↓=롤백·1문항↓=유지). 결정적 grounded N=10(temp 0·seed 42·기대 문자열)
= **신 10/10 · 구 10/10 동률** → 삭제. 디스크 212G→**234G**.
원자료 `heavy-ab-old-vs-new-20260903.json`.

## 2. M6 — 고비용 엔드포인트 개별 레이트리밋 (06-23 감사 잔여 처치)

- 🔑 **임계를 실측에서 가져왔다**: 7일 journald 피크 = generate 5/min·chat 2·rag 3·
  counseling 3·translate 5. 개별 상한은 그 6배 이상 — 현 연동(그룹웨어·edu·
  OpenPencil·ERP) 무영향, 단일 키가 전역 120/min을 고비용 생성에 독식하는 것만 차단.
- 구현: `core/rate_limit.py` 주입식 홀더 + `heavy_limit` 데코레이터.
  **미주입이면 no-op** — 단독 blueprint 테스트·배치 스크립트 동작 불변.
  app.py가 Limiter 생성 직후·routes import **전에** 주입.
- 대상 15경로: `generate`·`chat` 30/min · `chat/heavy` 10 · `rag/search`·`rag/ask` 30 ·
  `counseling/chat(±stream)` 20 · `translate` 60 · `meeting/analyze` 10 ·
  `image/generate` 30 · `reasoning/chain(±stream)`·`ensemble` 20 ·
  `organ-mesh`·`cardiac-ep` 6.
- **의도적 제외**: `/api/stt/chunk` — 실시간 세그먼트 연쇄 계약이라 개별 상한이
  실사용을 깨뜨릴 수 있다(전역 120/min이 덮음). 회귀가 이 제외를 **동결**한다
  (붙이면 실패하는 테스트).
- 회귀 4: no-op 경로 · 실제 429 발동 · 대상 15경로 소스 동결 · STT 제외 동결.

## 3. 함께 내린 결정 (코드 무변경 — 결정 기록 정본 참조)

- **M2 "블로킹 fetch"**: 코드 확인 결과 20MB 캡·리다이렉트 차단·10s 타임아웃이
  **이미 처치돼 있었다**(백로그 문구가 실제보다 크게 남음 → 정정). 잔여는 사설IP
  리졸브 도메인 케이스뿐 — 워커 오프로드 구조가 필요해 파킹(트리거=외부 URL 사용처).
- **triage 영어 증상명**: 30일 호출 **6건** — 측정 재료조차 없는 표면. 파킹
  (트리거=주 50건+).
- **`/api/gpu/*` 자기 카드 Offline**: 보류 — 기능 영향 0이고 그 502가
  upstream_error_rate의 살아있는 통제군(v2.125.26).
- **운영자 브리프 현행화**: `operator-pending-2026-09-03.md`(구판 08-25 대체) —
  신규 항목 "커뮤니티 연동 스펙"(일반 표면 v2 착수 조건) 포함.

## 4. 검증

- 게이트 **177/177·베이스라인 0** — 새 리밋이 게이트(연속 LLM 호출)와 콘솔·연동
  트래픽을 깨지 않음을 배포 게이트가 실증. 클린 회귀 2094 passed·0 failed.
