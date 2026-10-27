# v2.124.82 — 35b 생성 규칙 변형 + 경로 연동 · quality 모드 실패 실증

> 2026-08-04 · `routes/medical_law.py` · 회귀 +20 (1257 passed)
> 기획: `docs/01-plan/features/35b-baseline-hardening.plan.md`

## 1. 35b용 생성 규칙 변형 (`MEDICAL_LAW_SYSTEM_CONCISE`)

현행 `MEDICAL_LAW_SYSTEM`(2,586자)은 **폐루프 7~18이 `qwen2.5:14b`의 실패를 보고 누적한 것**이다
(규칙 4 하나가 ~1,000자 — 단서 탈락·호 오배치·방향 반전·대상 확장을 차례로 막은 흔적).

35b는 실패 방향이 다르다. **관측된 것만** 겨냥해 834자로 다시 썼다.

| 35b 실패(실측) | 새 규칙 |
|---|---|
| 408자 고시 1건이 1,470자 답변의 절반 차지 | "무관한 근거는 인용하지 말고 무시" |
| 1,322자(14b 729자) → 지연 25~30초 | "간결하게 · 부연 금지" |
| 근거 87% 지점 벌칙 조 미인용 | "목록 뒤쪽에 있더라도 끝까지 찾아" |

반대로 35b가 **자동으로 하는 것**(제15조① 단서 인용)은 규칙에서 덜어냈다.

`_system_for(model)` — config `medlaw_system_by_model` 맵으로 선택. **미지정이면 기존 동작 불변.**

### A/B (N=16)

| 조건 | 충실도 | 체크통과 | 평균 길이 | 지연 |
|---|---:|---:|---:|---:|
| 현행(14b 튜닝) | 0.979 | 0.755 | 1,249자 | 29s |
| **간결(35b용)** | **0.979** | **0.776** | **301자** | **16s** |

**충실도 전 문항 동률** · 길이 4.1배 · 지연 1.8배 단축. `conditions_kept` 7→6 개선,
`consistent_register` 4→0, `numbers_match` 8→11 악화(관찰 대상).

### 🐞 중간에 잡은 결함 — 간결 지시에 하한이 없었다

12건 중 1건이 **11자 답변**(`"의료법 제34조제1항"`)이었다. 조문 번호만 던지고 끝난 것이다.
"완결된 문장으로 반드시 서술" 하한을 넣자 최단 135자로 재발 0.

## 2. `/ask` 경로 연동 + `keep_alive` 정책 전송

`/api/medical-law/ask`의 기본 모델이 `qwen2.5:14b` 하드코딩이라, 상주를 35b로 두어도
**요청마다 35b가 축출되고 12초 콜드 복귀**를 물었다 — 상주가 사실상 무의미했다.

- `_default_model()` — 상주가 켜져 있으면 **상주 모델을 따른다**(config `medlaw_default_model`로 고정 가능)
- `_keep_alive_for()` — 🔴 상주 예열은 `keep_alive=-1`인데 **이후 요청이 keep_alive 없이 오면
  Ollama가 만료를 전역 기본값(5분)으로 재설정**한다. 즉 **요청 한 번에 고정이 풀린다.**

## 3. 🔴 그런데 quality 모드 자체가 실패했다

quality 모드(35b 상주)를 켠 상태에서 실사용 트래픽이 들어오자 35b가 계속 축출됐다.

```
ollama: "model predicted to exceed available memory, evicting"
  predicted 11.4GiB(qwen2.5:14b) · available 1.8GiB
  predicted 22.5GiB(qwen3.6:35b-a3b) · available 5.1GiB
```

| | 모델 |
|---|---|
| `/api/chat`(외부 node, 10분 **68건**) | `default_model` = `qwen2.5:14b` |
| 상주 | `llm_resident_model` = `qwen3.6:35b-a3b` |

**여유 2.0GB에서 둘은 공존할 수 없다.** `/ask`가 45초까지 늘어져 **`balanced`로 복귀**했고
축출 0건을 확인했다.

> 🔑 **상주 모델은 "가장 좋은 모델"이 아니라 "트래픽이 실제로 쓰는 모델"이어야 한다.**

## 4. 검증

`tests/test_medlaw_system_variant.py` **20** 신규(변형 선택·기본 불변·모델 연동·keep_alive 정책).
전체 **1257 passed / 7 skipped**. 기본값은 현행 유지라 서버 동작 변화 없음.
