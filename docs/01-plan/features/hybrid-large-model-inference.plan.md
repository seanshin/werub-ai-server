# 하이브리드 대형 모델 추론 (GPU+RAM 오프로드) — 기획서

> 작성 2026-07-05 | **Phase 0 PoC 실측 완료 2026-07-05(§5.5)** | 범위: **16GB VRAM 천장 우회 — 품질 우선(속도 비이슈) 대형 모델 탑재** | 방식: GPU VRAM ↔ 시스템 RAM 혼용(부분 오프로드)
> 기조: 측정 선행 · 외부 API 0(자체호스팅 GGUF) · 현 스택(Ollama/llama.cpp) 재사용 · 상주 fast-lane 불변 · 의료 자문보조 스탠스 불변
> **PoC 요약(측정이 낙관 가정 반증)**: 하이브리드 메커니즘=✅작동(자동 오프로드 56 tok/s·무증설). 품질=❌**30B MoE가 상주 14B보다 열등** — 파라메트릭 MCQA 동률(60%=60%)+**RAG-grounded 실경로선 오히려 나쁨**(충실도 0.68<0.85·품질 3.5<4.3·5배 느림, Phase 0.5). reasoning 통합비용=실재(gpt-oss harmony 비호환·qwen3 thinking 극저속). **기보유 大모델(medgemma:27b 의료특화·gemma4:26b·qwen3:14b)까지 확대검토**: 17GB 대형 dense는 하이브리드서 50~88s/문항(8~14× 느림)+한국어 열세로 실질 부적합 → **결국 크기 아닌 도메인·언어 정렬이 지배**. → **Tier A NO-GO**(현 후보 전부 qwen2.5:14b 미달). Tier B(RAM 증설→235B급)만 남으나 투기적 — §9 권고.
> 관련: [[project_gpu_utilization_review]] · [[project_model_benchmark]] · [[project_sana_image_poc]](사이드카 선례) · [[project_llm_eval_hallucination_plan]](품질 측정) · [[reference_ollama_gpu_tuning]] · [[feedback_no_external_api]] · [[feedback_ollama_fleet_wired]]

---

## 1. 배경 / 문제 정의

현 무기고(36 모델)는 **16GB VRAM 천장**에 묶여 있다. 상주 `qwen2.5:14b`(~11.7GB)를 넘어서는 대형 모델(30B+ dense·70B·거대 MoE)은 **애초에 로드 불가** — `MAX_LOADED=1` + 스왑으로도 16GB를 넘는 모델은 못 올린다. 즉 "더 큰 모델 = 더 나은 품질"의 상단이 하드웨어로 잘려 있다.

**사용자 요구(2026-07-05)**:
1. **품질로만 접근** — 속도는 이슈가 아니다. 대형 모델의 품질을 취하되 느린 추론은 수용한다.
2. **현재 모델에 추가 가능한 형태**로, 관련 오픈소스를 검토해 분석.
3. **GPU 메모리 + 시스템 RAM 혼용 하이브리드** 구성.

이는 정확히 **부분 오프로드(partial offload)** — 모델 일부를 GPU VRAM에, 나머지를 시스템 RAM/CPU에 두고 계산하는 이질(heterogeneous) 추론이다. 속도 저하를 감수하는 대신 VRAM에 안 들어가는 모델을 돌린다.

---

## 2. 측정 선행 — GPU 박스 실측 (2026-07-05, 결정적 게이트)

| 자원 | 실측 | 함의 |
|---|---|---|
| **시스템 RAM** | **30GB**(가용 ~19GB · 스왑 8GB) | 🔴 **VRAM이 아니라 RAM이 하이브리드의 진짜 병목.** CPU 오프로드분이 RAM에 들어가야 함 |
| CPU | AMD Ryzen 9 7900 (12C/24T) | 오프로드 연산 충분(AVX-512). ⚠️ AMX는 Intel 전용 → KTransformers AMX 커널 미적용 |
| GPU | RTX 5080 16GB(현재 11.7GB=qwen2.5:14b 상주) | 부분 오프로드 여지 있음 |
| Ollama | 0.30.11 | llama.cpp 백엔드 · **MoE CPU 오프로드 네이티브 지원**(§4-B) |
| 디스크 | 110GB 여유 | 대형 GGUF 저장 가능 |

**🔑 결론(측정이 뒤집은 전제)**: 사용자는 "GPU 메모리 제약"을 문제로 봤으나, **하이브리드에선 30GB 시스템 RAM이 더 타이트한 제약**이다. 이 게이트가 후보 모델의 상한을 결정한다:
- **30GB RAM 내(무증설)**: ~30B급 MoE(gpt-oss-20B·Qwen3-30B-A3B, 오프로드분 10~15GB RAM) → **현재 못 돌리던 품질, 즉시 실현 가능**.
- **70B dense / 거대 MoE(235B·671B)**: RAM 부족 → **RAM 증설(64/128GB) 필요**. DDR5 증설은 32GB GPU 교체보다 **1/10 비용**(§7-B) = 사용자 "하이브리드" 구상의 저비용 핵심 레버.

---

## 3. ⚠️ 검토에서 나온 결정적 사실 (설계를 가르는 것)

**dense 모델 부분 오프로드는 느리고, MoE 오프로드는 실용적이다 — 둘은 완전히 다른 얘기.**

- **Dense(70B) 부분 오프로드**: 모든 레이어가 매 토큰 계산 → CPU가 대량 GEMM 부담 → 70B가 24GB GPU서 **3~8 tok/s**(품질우선이라 감내는 가능하나, 30GB RAM엔 70B Q4(40GB) 자체가 안 들어감 → **이중 탈락**).
- **MoE + expert 오프로드**: 토큰마다 소수 expert만 활성(예: gpt-oss 21B 중 **3.6B active**, Qwen3-30B 중 **3.3B active**). **attention/router는 GPU에, 큰 expert FFN만 CPU RAM에** 두면 — PCIe엔 작은 activation 벡터만 오가고 CPU는 활성 expert만 계산 → **부분 오프로드인데도 소형 모델 속도에 근접**. FFN은 덜 쓰이고 크기가 커서 오프로드 효율 최고.

→ **본 기획의 축은 "MoE + expert CPU 오프로드"**. dense 대형 부분 오프로드는 명시적 비권장(느림+RAM초과).

---

## 4. 오픈소스 기법 조사 (전부 자체호스팅 · [[feedback_no_external_api]] 부합)

### A. MoE expert CPU 오프로드 (핵심 기법, llama.cpp)
- `--override-tensor "\.ffn_.*_exps\.weight=CPU"` — 모든 expert FFN을 CPU에, attention은 GPU에. `--n-cpu-moe N` — 상위 N개 MoE 레이어 expert를 CPU에(부분 조정).
- 원리: expert 활성 시 작은 activation을 VRAM→RAM 전송, CPU가 expert 가중치로 계산 후 VRAM 복귀. FFN은 사용빈도 낮고 커서 오프로드 손실 최소·VRAM 회수 최대.
- 라이선스: llama.cpp **MIT**.

### B. Ollama 네이티브 `num_moe_offload` (★ 현 스택 추가 = 최소 마찰)
- Ollama가 Modelfile `num_moe_offload` 파라미터 지원(PR #12333): `N`=상위 N MoE 레이어 CPU 오프로드, `-1`=전 expert CPU, `0`=off(기본). 내부적으로 llama.cpp 텐서 오버라이드 전달.
- **의미**: **신규 프레임워크·venv·의존성 0.** MoE GGUF pull → Modelfile에 `PARAMETER num_moe_offload -1` → attention GPU / expert RAM. 현 오케스트레이터·큐·마킹·인증 전부 무변경 재사용.
- 한계: Ollama는 llama.cpp 얇은 래퍼라 임의 텐서 오버라이드 regex 세밀조정은 제한 → 정밀 튜닝 필요 시 C.

### C. llama.cpp 사이드카 (세밀제어 폴백, SANA 선례 정합)
- `num_moe_offload`가 부족(레이어별 배분·다GPU·정밀 override)하면 **llama.cpp 서버를 로컬 사이드카**로: `--override-tensor` 명시. [[project_sana_image_poc]] `sana.service`(127.0.0.1:8189)·아바타 `ts_poc_venv` 격리 선례와 동일 패턴(systemd·내부전용·오케스트레이터 capability).
- 라이선스: MIT.

### D. KTransformers (거대 MoE 전용 — 현 박스 부적합, RAM 대증설 시 재검토)
- 이질 GPU/CPU MoE 최적화 프레임워크(SOSP'25). DeepSeek-R1/V3 671B Q4를 **14GB VRAM + 382GB DRAM**으로 구동. AMX 특화 커널.
- **현 30GB RAM엔 부적합**(382GB DRAM 전제) + **AMD CPU라 AMX 미적용**. → **제외**(RAM 128GB+ & Intel AMX 박스 전제 시에만 가치).

### E. 극단 양자화 (보조 레버)
- Unsloth **Dynamic GGUF**(레이어별 importance-aware 비트배분)·`IQ2/IQ1`·iMatrix — 큰 모델을 더 작게. 예: Qwen3-235B를 Q2로 ~80GB, DeepSeek-R1 1.58bit ~140GB. 품질 손실 있으나 dynamic quant가 완화.
- 역할: Tier B(RAM 증설)에서 거대 MoE를 RAM에 맞추는 보조 수단.

### 배제 후보
- **airllm**(디스크 레이어 스트리밍): 70B를 4GB VRAM서 돌리나 **토큰당 수십초**(디스크 I/O) → 품질우선이어도 비실용.
- **vLLM**: 36모델 플릿 비호환(기록됨) + CPU 오프로드 미성숙. 현 llama.cpp 스택 유지.

---

## 5. 후보 모델 (라이선스·크기·활성·게이트)

| 모델 | 총/활성 | Q4 크기 | 라이선스 | 품질(공개벤치) | 하이브리드 적합 |
|---|---|---|---|---|---|
| **gpt-oss-20B** | 21B / 3.6B | ~13.3GB(MXFP4) | Apache 2.0 | MMLU 85.3 · HealthBench 42.5 | ✅ **Tier A** expert→RAM ~10GB. 30GB RAM 여유 |
| **Qwen3-30B-A3B** | 30B / 3.3B | ~18GB | Apache 2.0 | AIME'24 80.4 · LiveBench 74.3 | ✅ **Tier A** expert→RAM ~15GB. 타이트하나 가능 |
| Qwen3-235B-A22B | 235B / 22B | ~88GB(Q2~) | Apache 2.0 | 플래그십급 | ⏸ **Tier B** — RAM 128GB 증설 필요 |
| DeepSeek-V3/R1 | 671B / 37B | 140GB+ | MIT(가중치) | 최상위 | ❌ RAM 극대증설+KTransformers(AMX) 전제 → 현 제외 |

- 비교: 현 상주 `qwen2.5:14b`(KorMedMCQA 67.5%·32K서 CPU스필). Tier A 후보는 **현재 16GB에 못 올리던 30B급** — 품질 상단을 여는 첫 후보.
- 의료: gpt-oss HealthBench 42.5는 참고치(영어). 프로덕션은 RAG-grounded 유지 — 모델 교체 아닌 **품질 tier 추가**([[project_model_benchmark]] 위계 재평가 플래그와 연동).

---

## 5.5 PoC 실측 결과 (Phase 0 — 2026-07-05, GPU 라이브·프로덕션 무중단)

무증설·격리 실행으로 실측. 상주 qwen2.5:14b 일시 축출→복원(현재 정상 복원·health200).

### 자원·속도 — 하이브리드 메커니즘 ✅ 검증
| 모델 | tok/s | GPU VRAM | CPU 오프로드 | 판정 |
|---|---|---|---|---|
| gpt-oss:20b (13GB) | **171.6** | 12.7GB | 0% | 16GB 단독 적재(swap tier)·초고속 |
| **qwen3-coder:30b (18GB)** | **56.7** | 13.6GB | **31%(6GB→RAM)** | **16GB 초과 → Ollama 자동 오프로드로 하이브리드 실동작** |

- **🔑 핵심 검증**: 16GB를 초과하는 MoE(18GB)가 **Ollama 자동 레이어 오프로드만으로** 56 tok/s로 동작. §3의 "MoE 오프로드는 실용적"을 실측 입증(dense 부분오프로드 3~8 tok/s와 대조). **RAM은 +6GB만** 사용(30GB 내 여유).
- **⚠️ `num_moe_offload` 게이트**: **Ollama 0.30.11은 이 파라미터 미지원**("unknown parameter" — PR #12333 미포함). 즉 §4-B 네이티브 파인-오프로드는 **Ollama 추가 업그레이드 or llama.cpp 사이드카(§4-C)** 필요. **단, 자동 레이어 오프로드로도 이미 실용** → 파인 제어는 최적화 옵션이지 필수 아님.

### 품질 — KorMedMCQA doctor N=40, ⚠️ 이득 미입증
| 모델 | 정답 | 정확도 | 비고 |
|---|---|---|---|
| qwen2.5:14b (상주 베이스라인) | 24/40 | **60.0%** | format·plain 동일 |
| **qwen3:30b-a3b (think off)** | 24/40 | **60.0%** | **동률 — 개선 없음**(비추론 모드=qwen2.5와 공정 비교) |
| gpt-oss:20b | 0/40 | **측정불가**(pf=40) | harmony 출력이 format·plain 양쪽서 파싱 실패 |
| qwen3:30b-a3b (think on) | — | **미완료** | 2048토큰 추론×40문항 **28분+ 미완**(~40s/문항) = 벤치 불가할 만큼 느림 |

- **결론(측정선행이 낙관 교정)**: 30B MoE가 상주 14B 대비 **KorMedMCQA doctor에서 품질 이득 없음**([[project_model_benchmark]] "범용>의료특화·큰≠나음" 패턴과 정합). ⚠️캐비엇: N=40 단일config 소표본(신뢰구간 넓음)·MCQA=파라메트릭 지식≠프로덕션 RAG-grounded·think-off는 qwen3 추론강점 제거.
- **reasoning 통합비용 실재**: ①**gpt-oss harmony** 형식이 현 제약디코딩 하네스(그리고 잠재적으로 채팅 파이프라인)와 비호환→harmony 파서 필요 ②**qwen3 thinking**: off=빠르나 강점제거(동률)·on=단일쿼리 ~40s(품질tier면 수용가능하나 벤치·다중요청 비현실적).

### Phase 0.5 실측 — RAG-grounded 실경로 A/B (2026-07-05, ❌ 30B MoE 열등)
MCQA는 파라메트릭이라 프로덕션(RAG-grounded)을 안 잡음 → **동일 의료법 RAG 근거에 생성 모델만 교체**해 충실도(`core/faithfulness`)+품질(`core/llm_judge` G-Eval)로 A/B(의료법 5질의, `scripts` 자산 재사용). judge=상주 qwen2.5:14b.

| arm | 충실도 | 환각 | 품질(G-Eval) | 생성 |
|---|---|---|---|---|
| **qwen2.5:14b (상주)** | **0.847** | 3/5 | **4.30/5** | 29s |
| qwen3:30b-a3b (think off) | 0.676 | 4/5 | 3.50/5 | 140s(**5×**) |

- **🔴 결정적**: 30B MoE가 **전 지표 열등**(충실도↓·환각↑·품질↓)+**5배 느림**. 파라메트릭 동률을 넘어 **grounded 실경로선 오히려 나쁨**. ⚠️캐비엇: N=5·judge 자기선호 가능(qwen2.5 judge가 자기 스타일 선호)—단 충실도(claim 검증=스타일 비의존)도 동일 방향이라 견고. qwen3 think-off(추론 제거)나 think-on은 실경로에 비현실적 지연.

### 大모델 확장 검토 — 기보유 모델(pull 0)로 재비교 (2026-07-05)
"MoE라 활성 3.3B라 진 것 아니냐 → 진짜 큰 dense·의료특화는?"에 답하기 위해 기보유 大모델 비교.

| 모델 | 크기/구조 | 활성 | grounded 품질 | 생성 속도 | 판정 |
|---|---|---|---|---|---|
| qwen2.5:14b (상주) | 9GB dense | 14B | **faith 0.847·qual 4.30** | ~6s/문항 | 기준(최고) |
| qwen3:30b-a3b | 18GB MoE | 3.3B | faith 0.676·qual 3.50 | 28s | ❌ 열등(측정) |
| **medgemma:27b** | 17GB dense·**의료특화** | 27B | **미측정**(인프라) | **88s**/문항 | ❌ 속도 실격 |
| **gemma4:26b** | 17GB dense·범용 | 26B | **미측정**(인프라) | 50s/문항 | ❌ 속도 실격 |
| qwen3:14b | 9GB dense·신세대 | 14B | 미측정(인프라) | 14s | — |

- **⚠️ 정직한 한계**: 17GB 대형 dense(medgemma:27b·gemma4:26b)는 **하이브리드 오프로드 시 50~88s/문항**(qwen2.5의 8~14×). 반복 로드가 30GB RAM을 **스왑 스래싱**시켜 grounded 품질 측정을 완주 못함(측정 인프라 한계, 모델 품질 판정 아님).
- **그러나 결론은 명확**: ①**속도만으로 실격** — 88s/문항은 실용 tier 불가(qwen2.5 대비 14×) ②**품질도 우위 근거 없음** — MedGemma/Gemma 계열은 **한국어 약함**이 이미 실측됨([[project_model_benchmark]] KorMedMCQA: MedGemma 35.2%·med42 52.5% ≪ qwen2.5:14b 67.5%). 의료특화(medgemma)라도 **영어 중심**이라 한국어 의료법 grounded서 qwen2.5를 이길 개연성 낮음.
- **핵심 재확인**: "큰 dense(활성 27B)면 이기나?" → **속도 실격 + 언어정렬 열세로 실질 부적합**. 파라미터 총량·활성 연산 어느 쪽을 봐도, **이 태스크(한국어 의료 grounded)의 승자는 도메인·언어 정렬된 qwen2.5:14b**. 크기가 아니라 정렬이 지배.

### PoC 종합 판정 (Phase 0 + 0.5 + 大모델 확장)
- **메커니즘 GO**: 하이브리드는 무증설·무인프라로 작동, 속도 수용가능(56 tok/s).
- **품질 ❌ NO-GO(결정적)**: 30GB RAM 내 후보(qwen3:30b-a3b·gpt-oss:20b)는 상주 14B 대비 **품질 이득 없음(MCQA)~열등(grounded)**. gpt-oss는 harmony로 통합 불가. → **Tier A 채택 정당화 안 됨.** [[project_model_benchmark]] "범용 30B < 잘 맞는 14B(한국어·의료)" 패턴 재확인.
- **Tier B(RAM 증설→235B급)**만 남으나 **투기적**: 30B가 이미 지는 상황서 "더 크면 이긴다"는 미검증. 큰 모델을 "그냥 크니까" 넣는 것은 ethos 위배 → §9 권고(RAM 투자 전 235B급을 오프라인으로 grounded eval 선입증).

---

## 6. 하이브리드 아키텍처 설계 (상주 fast-lane 불변)

**핵심 원칙**: 상주 `qwen2.5:14b`(저지연 fast-lane)는 **그대로**. 대형 MoE는 **품질 요청 전용 on-demand tier**로 추가 — 느린 하이브리드 추론은 명시적으로 요청한 경로에서만.

```
요청 ──┬─ (기본/저지연)     → qwen2.5:14b 상주 (현행, 무변경)
        └─ tier=quality 플래그 → 대형 MoE (expert→RAM 하이브리드)
                                   ├ 방식1: Ollama num_moe_offload 모델 (MAX_LOADED 스왑)
                                   └ 방식2: llama.cpp 사이드카 (:8xxx, 오케스트레이터 capability)
```

- **진입점**: 오케스트레이터에 `quality`/`heavy-reasoning` capability 추가(detect→iMac 위임 선례). 또는 `/api/chat` `model="qwen3-30b-a3b"` 직접 지정 + `tier` 파라미터.
- **VRAM 조정**: 대형 MoE 로드 시 상주 qwen 스왑 or expert 전량 RAM으로 GPU 소형화(co-reside 여부는 PoC 실측). SANA `_unload`·GPUResourceManager 상호배제 패턴 재사용.
- **격리**: 사이드카 방식이면 SANA `sana.service` 판박이(systemd·127.0.0.1·idle-unload). Ollama 방식이면 별도 인프라 0.
- **관측**: `/api/image/sana/status` 선례처럼 tier 상태(로드/오프로드/tok_s) 노출 + Admin 카드.

---

## 7. Phase 로드맵

### Phase 0 — 측정 선행 PoC ✅ 완료 (2026-07-05, §5.5)
gpt-oss:20b·qwen3-coder:30b·qwen3:30b-a3b 실측 완료. **결과: 메커니즘 GO / 품질 NO-GO(현 근거).** `num_moe_offload`는 Ollama 0.30.11 미지원(자동 오프로드로 대체). → 상세 §5.5.

### Phase 0.5 — 엄밀 품질 재평가 ✅ 완료 (2026-07-05, §5.5 — ❌ NO-GO)
RAG-grounded 실경로 A/B 실측: **qwen3:30b-a3b가 상주 14B보다 열등**(충실도 0.68<0.85·품질 3.5<4.3·5×느림). → Tier A 게이트 **불통과**. (표본·벤치 추가 확대는 결론 방향이 명확해 저ROI — 필요 시만.)

### Phase 1 — on-demand quality tier 통합 (❌ 현 보류 — Phase 0.5 NO-GO)
- 방식 선택(Ollama num_moe_offload vs llama.cpp 사이드카) = PoC 결과로 결정.
- 오케스트레이터 capability or `tier=quality` 라우팅. 상주 fast-lane·마킹·인증·큐 재사용. 회귀 테스트 + Admin 카드 + status 엔드포인트.

> **운영 상태 변경 (2026-07-07, [[maintenance-disk-heavy-20260707]])**: 디스크 정리 중 실험 잔재 `qwen3:30b-a3b`(18G·NO-GO 확정 모델)·`llama3.3:70b-q2_K`(26G·미배선) 삭제. heavy는 `heavy_enabled=false` 유지, `heavy_model` 기본값을 삭제된 30b-a3b → **설치·검증된 `gpt-oss:20b`로 재지정**(재활성 시 없는 모델 로드 실패 방지). 재개 트리거 불변(235B 오프라인 grounded 우위 or RAM 증설). 별도로 **이미 설치된 `qwen3:14b` 상주 A/B**를 낮은 비용의 다음 스텝으로 권고.

### Phase 2 — RAM 증설 tier (사용자 결정 · 거대 모델 진짜 해금)
- **RAM 30→64/128GB 증설**(DDR5 ECC 아님 기준 대략 ₩150~400K = 32GB GPU 교체 대비 ~1/10). → Qwen3-235B-A22B(Q2~Q4 하이브리드) 등 **플래그십급 품질**을 하이브리드로.
- Unsloth dynamic quant(§4-E)로 RAM 예산 맞춤. 필요 시 KTransformers 재평가(단 AMD=AMX 미적용, 이득 제한).

---

## 8. 제약 / 리스크 / 기조

- **RAM이 상한**(측정): 무증설 시 ~30B MoE가 천장. 70B+/거대 MoE는 RAM 증설 전제 — 기획서가 이를 정직히 명시.
- **속도**: 하이브리드는 수~수십 tok/s. 사용자 "품질 우선·속도 비이슈" 전제라 수용, 단 **저지연 요청은 상주 fast-lane 유지**(tier 분리로 사용자 경험 회귀 0).
- **상주 경합**: 대형 MoE 로드가 qwen 스왑 유발 가능 → on-demand·상호배제로 격리(상시 상주 아님).
- **품질 미검증 교체 금지**([[project_model_benchmark]]): 벤치 정답률 단독으로 라우팅 교체 안 함. tier 추가일 뿐 의료 RAG-grounded 스탠스·med42 자문보조 불변.
- **라이선스**: gpt-oss/Qwen3 Apache 2.0 · llama.cpp/KTransformers 오픈소스 · 전부 자체호스팅([[feedback_no_external_api]] 부합). 도입 모델별 라이선스 최종 확인.
- **플릿 보존**([[feedback_ollama_fleet_wired]]): 기존 35모델 삭제 없이 tier 추가.

---

## 9. 결론 및 권고 (Phase 0+0.5 완료)

- ~~Phase 0 PoC~~ ✅ + ~~Phase 0.5 grounded A/B~~ ✅ **완료**. **핵심 결론: 하이브리드는 작동하나, 품질 이득이 없다**(30B MoE가 MCQA 동률·grounded 실경로 열등·5배 느림). "큰 모델 = 나은 품질" 가정이 양축서 반증.
- **📌 권고 = 현 시점 도입 보류(NO-GO)**. 하이브리드 인프라(Ollama 자동 오프로드)는 이미 준비됐고 언제든 켤 수 있으나, **켤 이유(품질)가 실측으로 없다**. 상주 qwen2.5:14b fast-lane이 30GB RAM 내 모든 후보보다 우수.

### 남은 유일한 경로 = Tier B (거대 MoE), 단 조건부
- **RAM 증설(30→128GB) 의향**: DDR5는 저비용이나, **30B가 이미 지는데 235B가 이긴다는 보장 없음**. → **투자 전 선입증 필수**: 235B급(Qwen3-235B-A22B)을 **오프라인 1회 grounded eval**(빌린/임시 대용량 RAM 환경)로 qwen2.5:14b 대비 우위 확인 후에만 RAM 구매. 우위 미확인 시 Tier B도 폐기.
- **대안 우선순위**: 하이브리드로 "큰 범용 모델"을 좇기보다, **의료/한국어 특화 파인튜닝**이나 **RAG 품질 개선**([[project_llm_eval_hallucination_plan]] retrieval 튜닝 계열)이 실측상 더 확실한 품질 레버.

### 재개 트리거
- 특정 대형 모델이 오프라인 grounded eval서 qwen2.5:14b 대비 **명확한 우위** 입증 → Tier B RAM 증설 + 통합 재개.
- 그 전까지 본 기획은 **"인프라 검증 완료·도입 보류"** 상태로 보존(재작업 없이 즉시 재개 가능).

---

## 부록 — 조사 출처
- llama.cpp MoE 오프로드 가이드(HF blog, Doctor-Shotgun) · llama.cpp `--override-tensor`/`--n-cpu-moe`(ggml-org discussions)
- Ollama `num_moe_offload`(ollama/ollama PR #12333, issue #11772)
- KTransformers(kvcache-ai, SOSP'25 · DeepSeek 671B on 14GB VRAM+382GB DRAM)
- gpt-oss-20B(Apache 2.0, 21B/3.6B active, MMLU 85.3) · Qwen3-30B-A3B(Apache 2.0, 30B/3.3B active)
- Unsloth Dynamic GGUF(극단 양자화)
