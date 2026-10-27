# v2.125.47 — 약물 배치 LLM_SEED 비수렴 루프 정지 + 임상 표 오염 격리

기준 v2.125.46 · 출처 = 2026-09-28 정기 작업 점검(자동 스케줄러 실행 이력 판독)

## 0. 한 줄

약물 야간 배치가 **4개월 가까이 매일 밤 같은 약을 LLM으로 만들어 전부 버리고** 있었고,
그 전에 통과한 **모델 자체 지식 51건이 임상 표에 들어가** 처방 추천에 노출될 수 있었다.
자동 실행을 멈추고, 모델 자체 지식(T4)은 임상 표에 자동 반영되지 않게 막고, 51건을 격리했다.

## 1. 라이브 실측

- journald 14일: `night_collect` **하루 16~18회**, 전부 `fetched 8 · validated 0 · committed 0 · skipped 8`.
- `clinic_codes.staging_drug` **26,251건**(2026-06-04~09-27): skipped **25,565** — 사유가 전부
  `duplicate code`(`D-MER01` 3,798회·`D-MET01` 2,378회·…) ⇒ 같은 8개 "부족 카테고리"에 LLM이
  **이미 있는 약을 계속 제안** → 카테고리가 영영 안 채워져 **수렴하지 않는 루프**.
  밤마다 qwen2.5:14b ~17회(회당 ~15s·1.7k tok)·산출 0. 고아 `enriching` 316건.
- 자동 수집 어댑터는 **`LLM_SEED`뿐**(`adapter_name = source or "LLM_SEED"`) — Phase A MVP가 그대로 상시화.
- 🔴 **`drug_codes`의 `source='LLM_SEED'` 51건**(T4·마지막 09-05) — 실물 오류 예:
  Myfortic→"마이포르티크(**미도스프론**) **11mg**" · Favipiravir→"**아발로비르**" ·
  Tenofovir DF→"…디프록시실 **팔미네이트**" · Cefazolin 1g "**일일1회**".
  **31건이 `is_common=1`** → `routes/medical_cli.py`의 **처방 추천**(`WHERE category=? AND is_common=1`)과
  한글명→성분명 매칭 경로에 노출 가능. 공식 출처(MFDS 등 T2) 400건은 무관.
- ⚠️ `max_runs_per_night=10`인데 16~18회인 이유: 야간 카운터가 **자정에 리셋**돼 22~24시와 0~6시가
  각각 한도를 받는다(이번 처치로 자동 실행 자체가 멈춰 무관해짐 — 기록만).

## 2. 처치

| 층 | 처치 |
|---|---|
| 자동 실행 | 자동 트리거(`scheduler`·`night_auto`)는 **명시 소스 없으면 DB를 열기 전에 skip**(+로그). LLM_SEED는 **수동 전용** — 야간을 끄면 주간 스케줄이 살아나 같은 루프로 떨어지는 경로까지 막는다 |
| 야간 모드 | 기본 **off** · config `drug_batch_night_enabled`로만 켬 |
| 커밋 | 검증을 통과해도 **T4는 `needs_review`**로 빠지고 `_commit_drug`에 닿지 않음 — 모델이 지어낸 약물 정보가 임상 표로 가지 않는다 |
| 데이터 | `scripts/quarantine_llm_seed_drugs.py --apply`: DB 파일 백업 → `drug_codes_quarantine`(원행+사유·시각) → drug_codes에서 삭제 → 고아 enriching 정리 |

수동 경로(`trigger=manual`)·명시 소스 자동 실행은 그대로 동작.

## 3. 검증

신규 `tests/test_drug_batch_llm_seed_guard.py` 8건: 자동 트리거 2종이 **DB를 열지 않고** skip ·
수동/명시 소스는 진행 · 야간 기본 off · config 배선 · **T4 가드가 커밋보다 앞선다**(구조 고정).
⚠️ T4 가드는 run_pipeline 인라인이라 **소스 구조 고정**으로 검증했다(종단 실행 아님).
전체 회귀 2238 passed·1 failed(`test_korean_spellcheck::test_long_text` — 알려진 Ollama 타임아웃
flake·단독 재실행 **15/15·1/1 통과**)·9 skipped.
