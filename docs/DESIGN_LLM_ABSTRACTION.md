# LLM 추상화 층 — 무엇이 이미 있고, LiteLLM은 무엇을 더 주는가

작성 2026-08-10 · **갱신 2026-08-12**(이관 완료 반영) · 전수 감사 기준 · **설계 정본**

> **한 줄**: 이 시스템에는 LLM 추상화가 **여섯 층** 있고, LiteLLM이 제공하는 것의
> 대부분이 이미 자체 구현으로 존재한다. 남은 갭은 **task → 모델 매핑**과 **정책 강제** 둘뿐이다.

---

## 0. 왜 이 문서가 필요했나

2026-08-10 모델 디커플링을 기획하면서 "추상화가 없다"고 전제했다. **틀렸다.**
`core/llm_gateway.py`를 같은 이름으로 덮어쓰다가 v2.51.0부터 있던 provider 게이트웨이를
발견했고, 이어진 전수 감사에서 **여섯 층**이 나왔다.

📌 **교훈**: 인벤토리를 `grep <패턴>` 한 번으로 끝내지 말 것.
**이름으로도 훑어야 한다**(`ls core/ | grep -iE 'gateway|rout|llm|infer'`).
"없다"는 주장은 있다는 주장보다 검증이 어렵다.

---

## 1. 층 지도 (배선 상태 실측)

| 층 | 모듈 | 정하는 것 | 배선 | 상태 |
|---|---|---|---|---|
| **1. provider** | `core/llm_gateway.py` | **모델명 → provider** | `routes/ollama.py` 1곳 | ⚠️ 외부 전부 `enabled:false` = **휴면** |
| **2. 복원력** | `core/llm_resilience.py` | 서킷 브레이커·요청 병합·적응형 타임아웃 | **6파일**(story·writing·novel 등) | ✅ 활성 |
| **3. 상주** | `core/llm_residency.py` | 어느 모델을 VRAM에 붙잡을까 | **4파일** | ✅ 활성 |
| **4. 노드** | `core/orchestrator/dispatch.py` | **어느 기계**(GPU↔iMac) | detect·stt | ✅ 활성 |
| **5. 앞단 라우팅** | `routes/gateway.py` | 복잡도 → 로컬/원격 | iMac 전용 BP | ⚠️ GPU엔 미등록 |
| **5-b. 도메인 라우팅** | `core/medical_router.py` | 질의 → 결정적 도구 | orchestrator | ✅ 활성 |
| **6. task→모델** | `core/model_router.py` | **task → 모델명 + 정책** | **11파일** | ✅ 이관 완료(v2.125.0) |
| (참고) | `core/aux_infer.py` | 사이드카 | 0파일 | 연구용·미배선 |

### 관련 HTTP 표면 (이미 있음)

```
/api/providers            /api/providers/models      /api/providers/generate
/api/providers/<n>/enable /api/providers/<n>/disable /api/providers/<n>/test
/api/models/routing       /api/models/residency      /api/models/warm|unload
/api/gateway/chat|route|status|config   (iMac)
```

---

## 2. LiteLLM 비교 — 무엇이 새로운가

| LiteLLM이 주는 것 | 이 시스템의 현황 | 신규 이득 |
|---|---|---|
| 다중 provider 통합 | **`llm_gateway`**(OpenAI·Anthropic·Google·Groq·OpenRouter) | ❌ 없음 |
| OpenAI 포맷 통일 | **`routes/openai_compat.py`**(SSE·tool_calls 양방향·num_ctx 주입) | ❌ 없음 |
| 재시도·타임아웃 | **`llm_resilience`**(적응형 타임아웃·서킷 브레이커·요청 병합) | ❌ 없음 |
| 폴백 체인 | `dispatch.run` local_fn + `llm_resilience` | ❌ 없음 |
| 비용·토큰 추적 | `jobs.tokens`·응답 `tokens`·`api_activity` | 🟡 부분(집계 UI 없음) |
| 모델 라우팅 | `gateway`(복잡도)·`medical_router`(결정적)·`/api/models/routing` | ❌ 없음 |
| **task → 모델 매핑** | **없었음** → `model_router`로 신설 | — |
| **경로 정책(`local_only`)** | **없음**(규제 경계를 코드가 안 막았다) | — |

⇒ **LiteLLM의 실질 신규 이득은 "비용 대시보드" 정도**다.
그 대가로 **큰 의존성 트리**와 **11계약 재검증**(`DEVELOPER_GUIDE §1`)을 진다.

### 그런데 진짜 문제는 다른 곳이었다

추상화가 여섯 층이나 있는데도 **모델명 하드코딩이 141회**(`qwen2.5:14b`) 남아 있었다.
(2026-08-12 이관 후 전 계열 합계 **257 → 154**.)
이유는 단순하다 — **모든 층이 "모델명을 이미 아는 상태"에서 시작**한다.
호출자가 `generate("qwen2.5:14b", …)`라고 적으면, 그 뒤의 provider 해석·복원력·
상주·노드 분산이 아무리 정교해도 **모델명은 코드에 박힌 채로 남는다.**

```
호출자 ──"qwen2.5:14b"──▶ [llm_gateway] ──▶ provider 해석 ──▶ Ollama
         ↑ 여기가 141곳에 흩어져 있다
```

`model_router`가 채우는 자리가 정확히 그 화살표 왼쪽이다:

```
호출자 ──"medical_law.answer"──▶ [model_router] ──모델명+정책──▶ (기존 층들)
```

---

## 3. 판정

| 질문 | 답 |
|---|---|
| LiteLLM을 도입할 것인가 | **아직 아니다.** 신규 이득이 거의 없다 |
| 채택 트리거 | **두 번째 provider가 실제로 필요해질 때** + `DEVELOPER_GUIDE §1` 11계약을 라이브로 통과할 때(`pytest tests/test_llm_adapter_contract.py --live --adapter=litellm`) |
| 즉시 기각 조건 | V1 `num_ctx` 미적용 · V2 `format` 제약 미지원 |
| 그럼 무엇을 하나 | **task→모델 매핑과 정책 강제**(`model_router`) — 이건 LiteLLM이 안 준다 |

⚠️ **프론티어 모델 사용은 별개의 결정이다.** PHI 비저장 확인서·식약처 규제 트랙·
`digest` 소멸·비용(일 9,400건)이 걸린다. `model_router`의 `policy=local_only`가
의료·PHI 경로를 코드로 막는다.

---

## 4. 유지보수 규율

- **새 LLM 호출을 추가할 때**: 모델명을 코드에 쓰지 말고 `config/model_routing.json`에
  task를 등록한다. 미등록 task는 즉시 실패한다(오타가 조용히 다른 모델을 타는 것 방지).
- **의료·PHI·규제 경로는 `policy: local_only`** — 완화는 규제 검토 사안이지 코드 결정이 아니다.
- **임베딩 교체는 전 코퍼스 재색인**을 요구한다(`requires_reindex: true`).
  seam은 "바꿀 수 있게" 만들되 **"바꾸기 쉬워 보이게" 만들면 안 된다.**
- **층을 새로 만들기 전에 이 문서를 볼 것.** 이미 여섯 층이 있다.

## 관련 정본

- `docs/DESIGN_MODEL_SERVING.md` — VRAM 예산·적재 비용·증설 트리거
- `docs/DESIGN_SERVING_MODES.md` — 상주 모드(quality/balanced/light/off)
- `docs/DESIGN_MEDICAL_MODEL_ROUTING.md` — 의료 경로에 범용 모델을 쓰는 근거
- `docs/DEVELOPER_GUIDE.md §1` — Ollama를 그대로 쓰지 않는 이유 11항목(어댑터 계약)
- `docs/01-plan/features/model-decoupling-and-eval-driven-development.plan.md` — 이관 계획


---

## 8. 이관 결과 (2026-08-12 · v2.125.0)

6층이 **실제 배선**됐다. 이 문서의 결론(LiteLLM의 신규 이득은 작다)은 바뀌지 않았고,
오히려 **이관이 그 결론을 강화**했다 — 갭이던 "task → 모델 매핑 + 정책 강제"가 채워졌으므로
LiteLLM이 더 줄 것은 **두 번째 provider**뿐이다.

| 항목 | 결과 |
|---|---|
| seam 경유 | **11파일**(medical_law·counseling·writing·korean·spellcheck·orchestrator·novel·rag·coding·faithfulness·llm_judge) |
| 어댑터 표면 | `generate` · `stream` · `chat` · `chat_stream` · `embed` |
| 모델명 하드코딩 | 257 → **154** |
| 정책 강제 | 전 task `local_only` 기본 · 스트리밍/chat도 동일 경로로 검사 |

### 이관이 확정한 경계 (설계에 반영)

| 관심사 | 어디 | 근거 |
|---|---|---|
| 서킷 브레이커·적응형 타임아웃 | **seam 위** | 복원력은 도메인 정책이다(counseling·novel) |
| 한국어 후처리 | **seam 위** | §1-6에서 필터가 내용을 지운 사고 |
| 응답 캐시 | **seam 위** | 키에 이미지·think·options가 들어가야 한다(v2.124.59) |
| keep_alive·think·format | **seam 안** | 모델 선택과 붙어 있다 |
| 커넥션 회수(스트리밍) | **seam 안** | 조기 종료 시 `GeneratorExit` 전파 — 호출자에 맡기면 샌다 |

### 🔑 정본이 둘이 되지 않게 하는 장치

표는 값을 적지 않고 **어디서 가져올지**만 적는다(`model_from` + 이름 등록제 resolver):

| task | 정본 |
|---|---|
| `eval.*_judge` | `core/judge_config.py` |
| `orchestrator.*` | `config/tools.json`(reload로 무재배포 교체·mtime 캐시) |

표에 값을 또 적으면 config로 모델을 바꿔도 `/api/models/routing`이 **옛 값을 광고**하고,
규제 트랙은 그 엔드포인트 폴링으로 모델 교체를 감지하기로 한 계약이다(v2.124.12).

### ⚠️ 임베딩은 다른 규율

`requires_reindex`가 재색인 비용을 코드에 남긴다. seam은 '바꿀 수 있게' 만들되
**'바꾸기 쉬워 보이게' 만들면 안 된다** — 모델을 바꾸면 저장 벡터가 무의미해진다.
`rag.embed`(bge-m3)와 `coding.embed`(qwen3-embedding:8b)는 **다른 색인과 짝**이라 같게 만들면
한쪽이 통째로 죽는다.
