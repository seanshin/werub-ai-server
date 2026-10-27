# v2.125.42 — 판정 확률 축 신설(P6-1 Phase 0): 이분 판정에 확신을 붙인다

기준 v2.125.41 · 기획 정본 `docs/01-plan/features/P6-1-Decision-Confidence-And-Jev.plan.md` ·
조사 정본 `docs/01-plan/references/jev-adoption-review-2026-09-22.md`

## 0. 한 줄

충실도 평가의 주장별 entailment는 **`true`/`false`만 받고 확률을 버리고 있었다**.
`top_logprobs`에서 판정 확률을 뽑아 **`confidence` 축**을 신설했다 — *확신한 판정*과
*망설인 판정*을 가르고, **원문을 열어볼 대상을 자동 지목**한다. 외부 의존 0 · 새 모델 0.

## 1. 왜 — 외부 도구 검토가 우리 공백을 특정했다

운영자가 신규 공개된 **Jev(TypeSafe AI System One)** 도입 가능성을 물었다. 조사 결과
Jev가 파는 것은 *타입이 고정된 확률적 판단*이고, 그중 **`Choice`·`Noul`은 우리 fleet에서
이미 되거나 이미 돌고 있었다**:

- `core/faithfulness.py:338` `_ENTAIL_SCHEMA = {"supported": {"type":"boolean"}}` = `Noul`과
  같은 모양이고 **야간 배치에서 매일 돈다**. 확률만 버리고 있었다.
- 라이브 Ollama **0.32.5가 `logprobs`/`top_logprobs`를 지원**한다(실측). `scripts/eval_bench.py:7`의
  *"Ollama가 echo+logprobs 미지원"* 주석은 **0.21~0.30 시절 가정**이었다.

⇒ Jev 도입 범위는 **T1(외부 독립 판정자) 하나로 좁혀졌고**, 그 전제인 확률 축은
**외부 없이 지금 만들 수 있다**는 것이 이번 릴리즈다. Jev 자체는 Phase 1(운영자 키 대기).

## 2. 🔴 실측이 드러낸 함정 — 제약 디코딩 + logprobs

다중 토큰 enum(`["동일문서","상보파싱","별개회차"]`)으로 한국어 판정을 시켰더니:

```
 7 tok='상'  p=0.0119 | top: '연'=0.711, '개'=0.044
 8 tok='보'  p=0.0009 | top: '위'=0.771, '이'=0.147
 9 tok='파'  p=0.0000 | top: '적'=0.980
```

🔑 `top_logprobs`가 보여주는 것은 **문법 제약이 없었다면 쓰려던 토큰**이지 enum 후보가
아니다 ⇒ 선택지 간 확률을 **읽을 수 없다**. 그리고 판정 자체도 틀렸다(회차가 다르므로
`별개회차`가 정답인데 `상보파싱`). **선택 토큰 확률 ≈0 = 문법이 답을 만들고 모델은
의견이 없었다**는 신호다.

단일 토큰 라벨(`A`/`B`/`C`)로 바꾸자 **분포가 살아나고(`C`=0.9999) 답도 교정됐다**.

⇒ **규약: 판정용 스키마 값은 단일 토큰 라벨만.** 뜻은 시스템 프롬프트에 적는다.
다중 토큰 한국어 enum은 **답과 확률을 동시에 망친다**. 소스 스캔 회귀로 재유입을 막는다.
현재 라이브 출력 스키마(`_ENTAIL_SCHEMA` boolean · `llm_judge`)는 안전함을 확인했다.

## 3. 배선

| 파일 | 변경 |
|---|---|
| `core/decision_prob.py` 🆕 | `extract_label_prob` · `prob_of` — `top_logprobs`에서 라벨 확률 추출·재정규화 |
| `core/model_router.py` | `_payload`·어댑터/모듈 `generate`에 `logprobs` 통과. **기본 False에서 키 미부착** |
| `core/faithfulness.py` | `_route_full` 신설(`_route`는 래퍼·계약 불변) · `_qwen_entail_check_ex` · `minicheck_verify(want_prob)` · `cross_check` · `summarize_entail_prob` |
| `scripts/eval_faithfulness.py` | `detail` JSON 2지점에 `entail_prob` 집계(미측정=None) |
| `routes/activity.py` | `_entail_prob` · `_confidence_summary`(순수) → `/api/eval/status`에 `confidence` |
| `templates/eval_monitor.html` | KPI 타일 2개(판정 확신·망설인 주장) · 드릴다운 컬럼 · 이슈 리스트 |

**규율**
- ⚠️ **대소문자 변종 합산**(실측 `false` 0.951 + `False` 0.049). 안 하면 confidence가
  **체계적으로 과소평가**된다.
- **미측정은 null**. 구 배치·확률 미지원 백엔드·라벨 미검출을 "0건"과 가른다.
- **평균은 주장 수 가중**. 문항별 평균의 단순 평균이면 주장 1개 문항이 20개와 같은 무게.
- **fail-open**: 확률이 안 나와도 판정(bool)은 종전대로 난다. 주 KPI 불변.
- **계약 불변**: `_qwen_entail_check`는 여전히 `bool`, `minicheck_verify`는 기본값에서
  리스트. `cross_check(want_prob=False)`면 키 자체가 안 생긴다 ⇒ **롤백이 한 줄**.

## 4. 🔴 구현 중 잡은 내 결함 2건 (정직 기록)

1. **`prob_of`가 "미검출"과 "결정적 패배"를 둘 다 `None`으로** 돌려줬다. `top_logprobs`에
   `true`가 없다는 건 그 라벨이 **결정적으로 졌다**는 뜻인데 `None`으로 처리하면
   **가장 확신한 판정들이 축에서 통째로 빠지고 애매한 건만 남아 분포가 뒤집힌다**.
   → 분포가 잡혔으면 미포함 라벨은 `0.0`.
2. **`core/faithfulness.py`에 `logger`가 정의돼 있지 않았다**(내가 `logger.info`를 써서
   `NameError`가 날 상태였다). v2.125.25에서 triage가 겪은 것과 같은 계열 —
   이번엔 커밋 전에 잡혔다.

## 5. 회귀

신규 **4파일**: `test_decision_prob`(10) · `test_logprobs_passthrough`(7) ·
`test_entail_prob`(16) · `test_eval_confidence_axis`(12).

고정한 것 중 값이 큰 것:
- **기본값에서 페이로드가 안 바뀐다** — 이게 깨지면 v2.125.13 이관 동일성 회귀 75건이
  의미를 잃는다(그 회귀들은 "종전 페이로드를 그대로 적은 것"이다).
- **결정적 패배는 0.0이고 미측정이 아니다**(§4-1 재발 차단).
- 미지원 어댑터는 **거절이 아니라 확률 없이 진행**(fail-open). 단 `logprobs`를 안 건
  `TypeError`는 진짜 배선 오류라 **던진다**.

## 6. 부수 정정

- `templates/eval_monitor.html` 교차검증 설명 — **`bespoke-minicheck`는 현행이 아니다**.
  CC BY-NC라 **v2.124.14에 설치본에서 제거**됐고 실동작은 `eval.verifier`=**`qwen2.5:7b`**다.
  함수·축 이름만 남아 있었다. 평가 구성 오인은 측정 판독을 통째로 틀리게 만든다.
  (`CLAUDE.md`의 같은 문구도 정정 대상 — 기획서 §15)

## 6-b. 라이브 결과 — 축이 첫 배치에서 값을 냈다

배포: 회귀 **2158 passed**(2113 → **+45** = 신규 수와 일치 ⇒ 기존 전량 유지·**이관 동일성
회귀 75건 포함**) · 게이트 **177/177** · health 200 · NRestarts 0.

배치(core 5문항) = **33/33 주장에 확률·결측 0**. 그중 `id461` 사본 발급 문항:

```
faith=1.000 · mc_agree=0.5 · mc_conflicts=1
  P=1.0000  judge=supported · 검증자=supported
  P=0.3727  judge=supported · 검증자=not_supported   ← 망설임
```

- **주 KPI는 완벽인데 판정자는 망설였다** — 이 축을 만든 이유 그대로.
- **확률이 기존 축을 설명한다**: 유일한 `mc_conflicts`가 난 지점이고, 검증자의 부정은
  **확신이 아니라 0.37**이었다. 이분값만 보면 "한쪽이 틀렸다"인데 실제로는 **경계 사례**다.
- 🔴 **이 문항이 백로그 항목**(`id461` 사본발급 3/40 — *발생률 A/B 판정 불가*로 파킹돼
  있던 것). 새 축이 첫 배치에서 **독립적으로 지목**했다.
- 🟡 가설(미검증): 그 항목이 막힌 이유는 *결함이 40번에 3번만 발생*이었는데, 확률 축은
  **발생이 아니라 판정자가 매번 경계에 있다는 사실**을 재므로 손잡이가 될 수 있다.
  **관측 1건이다** — 착수하면 사전 등록한다.

## 7. 미완 (다음 세션)

- Phase 1 **Jev 어댑터**는 운영자 **API 키 대기**(`console.typesafe.ai/keys`).
- 채택/기각은 기획서 §8 사전 등록 기준으로 판정한다 — **E축**(Jev가 로컬 확률 축보다
  나쁘지 않을 것)이 정직 장치다. Phase 0가 공짜로 더 잘하면 Jev는 기각이다.
