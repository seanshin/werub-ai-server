# v2.125.12 — D1 router 이관 슬라이스 1·2 (5곳) — 이관은 변경이 아니다

날짜: 2026-08-19 | 기획 `docs/01-plan/features/remaining-work-2026-08-19.plan.md` §3-D | 인벤토리 `docs/01-plan/references/direct-ollama-calls-2026-08-19.md`

## 1. 이관 지점

| 지점 | task | 종전 → 이후 | 계약 |
|---|---|---|---|
| `core/retrieval_eval._default_ask_json` | `eval.retrieval_judge` | 직접 `requests.post` → `model_router.generate` | HTTP 오류=예외(종전 `raise_for_status` 층) · 페이로드 동일(format 스키마 포함) |
| `core/selfcheck.sample_answers` | `eval.selfcheck_sample` | 〃 | 〃 · 빈 샘플 제외 그대로 |
| `routes/rag.py` `/api/rag/query` | `rag.answer`(기본 qwen2.5:7b·요청 model 우선) | 호출자 세션 전달 | 🔴 **드러난 결함**: 종전엔 HTTP 오류가 `response` 없는 JSON → **빈 answer로 200**(무성 실패). 이관 후 예외 분기와 같은 문구(`LLM 응답 실패: …`)로 드러낸다 — 이 한 줄이 이관 안의 유일한 동작 변경이며 의도적이다 |
| `routes/notice._llm_json` | `notice.diagnose_json` | `format:"json"` 그대로 | 오류=예외 |
| `routes/medical_advanced` 구조화 판독 | `medical.structured_report`(medgemma:4b·이미지) | 예외→502 MODEL_ERROR · HTTP≠200→502+status | `Result.status`로 같은 응답 |

- 라우팅 표 20→25 task(`config/model_routing.json`). 표 값은 종전 하드코딩/기본값 그대로 — 동적 선택은 호출자 `model=`에 남긴다.
- 회귀 `tests/test_router_migration_slice1.py` 7건: 종전 페이로드를 **그대로 적어** 어댑터 본문과 비교 · 오류 계약.
  `tests/test_model_router.py::test_migration_progress_is_tracked` MIGRATED 목록 +5.
- 인벤토리 재분류: `routes/medical_training.py`의 호출은 파인튜닝 모델 활성화 **프로브**(num_predict 20) → 허용. 잔여 이관 대상 **47**.

## 2. 왜 지금 이만큼만

이관은 사이트마다 응답·오류 계약이 달라(위 표) 기계적 치환이 아니다. 잔여 47곳(game 12·vision 10·chat 3 등)은
경로별 골든셋/스모크가 회귀망인 것부터 다음 세션에 이어 간다. `repeat_penalty` 핀(D2)은 잔여가 0(또는 허용 목록만)일 때.

## 3. 배포
회귀·게이트 결과는 배포 로그. 슬라이스 2의 rag 동작 변경은 골든셋 `rag` 스위트가 덮는다.
