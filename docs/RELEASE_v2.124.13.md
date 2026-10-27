# RELEASE v2.124.13 — 교차검증 백엔드 교체: bespoke-minicheck(NC) → qwen-entail(Apache-2.0)

2026-07-20 | 이전: v2.124.12(모델 라우팅 조회 API) | 배경: 라이선스 전수 조사(인벤토리
v1.1)에서 야간 평가 교차검증 모델 bespoke-minicheck가 **CC BY-NC 4.0(비상업)**으로 확인 —
상업 서비스 내부 도구 사용도 위반 소지. 운영자 결정 **(b) 대체** 채택.

## 1. PoC (측정 선행 — `scripts/poc_verifier_backend.py`)

12페어: v2.120.0 원 PoC 클래스(수치 스왑·한정조건 드롭·호 오배치·패러프레이즈) +
이후 폐루프 실증 난문(id236 간접 함의=bespoke 위음성이던 케이스·id240 항 구분·
id253 반대 요약·id257 결합 조건) — 원 PoC보다 엄격한 세트.

| 백엔드 | 정확도 |
|---|---|
| **qwen-entail / qwen2.5:7b** | **12/12** |
| qwen-entail / qwen2.5:14b | 12/12 |
| bespoke-minicheck (기준선) | 11/12 (패러프레이즈 실패) |

→ Apache-2.0 대체가 성능 손실 없이(오히려 개선) 가능함을 실측 확정. **GO**.

## 2. 변경 사항 (`core/faithfulness.py`)

- `_qwen_entail_check`: qwen 계열 + format 제약 `{"supported": bool}`·temperature 0 —
  1청크 entailment 판정.
- `_default_checker` + env `FAITH_VERIFIER`(기본 **"qwen-entail"**) ·
  `FAITH_VERIFIER_MODEL`(기본 **qwen2.5:7b** — judge 14b와 다른 체크포인트=이종성 유지).
  `"minicheck"`=레거시 opt-in만 잔존(비권장·NC).
- **불변**: `minicheck_verify` 인터페이스·청크 분할·max-over-chunks 프로토콜·mc_* 지표
  스키마·러너(`F.cross_check` 호출부 무변경)·checker 주입(테스트).
- 회귀 test_faithfulness 16→19(백엔드 선택·entail 파싱·비정형 예외).

## 3. 지표 연속성 (EVAL_GUIDE 추세 주석)

**07-20 이전 mc_agree/mc_conflicts/strict_faithfulness = bespoke-minicheck 기준** —
스키마·프로토콜은 동일하나 백엔드가 달라 직접 비교 시 감안. faithfulness 주 KPI(judge
축)는 무영향. 기지 MC 위음성 클래스(간접 함의) 일부는 새 백엔드에서 해소될 것으로
예상(PoC id236 케이스 정답) — conflicts 감소는 개선이지 이상 아님.

## 4. 라이선스 정리

- 인벤토리 v1.1 §5⑤ 해소: 교차검증=qwen2.5:7b(Apache-2.0). bespoke-minicheck GPU
  설치본은 기본 미사용 — 제거는 운영자 임의(보존해도 라이선스는 '사용'에 적용).
- 야간 평가 스택 전체가 Apache-2.0(judge 14b)+Apache-2.0(verifier 7b)로 정리.

## 5. 검증

- GPU 회귀: test_faithfulness 19 passed.
- 라이브 E2E: 러너 1문항 교차검증 — 새 백엔드 경로로 mc_* 산출 확인(스왑은 7b 로드로
  기존과 동등).
