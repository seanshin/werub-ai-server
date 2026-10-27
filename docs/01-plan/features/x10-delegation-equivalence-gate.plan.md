# X10 — 위임 검증 게이트 실측 (사전 등록) · 2026-08-26

> §9 X10: detect cpu↔mps 동일성 100장 · STT 한국어 WER ≤2%p — 두 기획이 "위임 전 필수"로
> 지정했으나 v2.84~2.86 어디에도 측정 기록이 없다(라이브는 이미 위임 중). 이 문서는 **측정
> 기준·표본·판정을 실행 전에 못 박는다**(feedback_fix_the_measurement_first). 하네스는 만들되
> **실행은 iMac에서**(mps·mlx는 iMac에만) — 이 세션은 iMac SSH 셸 경로가 없어 운영자가 실행.

## 0. 왜 지금 준비만 하는가 (실행 블로커)

- **detect 동일성은 mps가 필요** → GPU 서버(CUDA)에선 못 잰다. iMac 장비 내 mps vs cpu 비교.
- **STT WER은 GPU(faster-whisper CUDA) 전사와 iMac(mlx) 전사를 둘 다** 필요로 하고, 기준선이
  되는 **정답 전사가 있는 한국어 회의 오디오가 레포에 0건**이다(운영자 실데이터).
- ⇒ 산출물 = 데이터/장비가 꽂히면 **한 줄로 도는 하네스 + 자기검증 임계값 내장 + 결과 JSON**.
  나(Claude)는 GPU에서 detect 절반(cpu-cpu sanity)만 돌려 하네스 자체를 검증한다.

## A. detect cpu↔mps 동일성 (사양 원문 확정)

- **기준(원문)**: `ai_probability`(0~1 float)의 **절대 편차 < 0.01 (1%p)**. 라벨 완전일치 아님.
  출처: `apple-silicon-worker.design.md:147,198` · `IMAC_M1_BENCHMARK.md:89` · 코드화 `tests/test_worker.py:233`.
- **표본**: **100장**. 모델은 양쪽 동일(`Organika/sdxl-detector`), device만 cpu/mps(`core/device.py`).
- **판정(사전 고정)**:
  - 통과 = 100장 **전부** |p_mps − p_cpu| < 0.01 · 최대 편차·평균 편차·초과 장수를 보고.
  - 🔴 초과 1장이라도 = **게이트 실패**(위임이 결과를 바꾼다는 증거) → 원인 분석(어느 확률
    대역에서·MPS 미지원 op 폴백 여부 `PYTORCH_ENABLE_MPS_FALLBACK`).
  - ⚠️ `ai_probability`가 None(detector 실패)인 장은 분모에서 빼지 말고 **명시 실패**로 센다
    (feedback_aggregate_metrics_dont_check_names — 결측을 0으로 세면 축이 깨끗해 보인다).
- **표본 조달·함정**:
  - 레포에 실이미지 0건. 하네스는 **결정적 합성 100장**(시드 고정·그라디언트/노이즈/도형/텍스트/
    주파수 패턴으로 `ai_probability`가 **여러 대역**을 갖게 — 단색은 극단값만 나와 중간 대역
    미검사). ⚠️ **이 측정 목적은 판별 정확도가 아니라 device 일관성**이므로 합성으로 충분하되,
    **합성 분포가 실트래픽과 다를 수 있다**(실이미지에서 편차가 더 클 가능성은 배제 못 함) →
    하네스에 `--image-dir`(운영자 실이미지 100장 투입) 경로를 함께 제공하고, 합성 실행은
    "1차 device 일관성", 실이미지 실행은 "확정"으로 라벨을 가른다.
- **실행**: iMac에서 `python scripts/verify_detect_equivalence.py --n 100`(합성) 또는
  `--image-dir <dir>`(실이미지). 결과 `logs/x10_detect_equiv_*.json`.

## B. STT 한국어 WER ≤2%p (사양 원문 + 미규정 결정)

- **기준(원문)**: **WER 열화 ≤ 2%p** = `WER(iMac mlx) − WER(GPU faster-whisper CUDA large-v3) ≤ 2%p`
  (절대 퍼센트포인트·상대 아님). 데이터셋 = **기존 회의 녹음 3건**. 출처:
  `apple-silicon-worker.plan.md:202` · `design.md:160` · `node-orchestrator-p2-stt.plan.md:53`.
- **🔴 사양이 규정 안 한 것 4건 → 여기서 결정(사전 고정)**:
  1. **ground truth 출처**: WER은 정답 전사가 있어야 계산된다. 결정 = **회의 3건의 사람 검수
     전사**(운영자 제공). GPU 전사를 의사 정답으로 쓰는 방식(b안)은 "열화 ≤2%p"가 아니라 "두
     엔진 불일치율"이 되어 사양과 다르므로 **기각**.
  2. **WER 정규화**: 한국어 **어절(공백) 단위** Levenshtein. 전처리 = 문장부호 제거·연속 공백
     1칸·영문 소문자화·유니코드 NFC. CER(문자 단위)도 병기(한국어는 어절 경계 모호).
  3. **디코딩 교란 통제**: GPU는 `beam_size=5+VAD`, mlx는 없음(조사 확인) → **순수 백엔드 차이가
     아니다**. 1차는 각 엔진의 프로덕션 기본 설정 그대로 재고(라이브 재현), 편차가 크면 mlx에
     동일 조건 부여 후 재측정(2차·기전 분리).
  4. **오디오 조달**: **실 회의 3건**(운영자). 🔑 **TTS 합성 금지** — 합성 음성으로 실 회의 WER을
     외삽할 수 없다(feedback_measure_the_real_thing). 실 한국어 음성 부재는 별건 문서도 확인
     (`voice-emr-stt-reply-to-his.md:181`).
- **판정(사전 고정)**: 3건 각각 열화 계산 → **최악 1건이라도 >2%p면 게이트 실패**(평균으로 가리지
  말 것). 3건은 표본이 작아 **발생률이 아니라 열화 크기·기전**으로 읽는다.
- **미비(하네스가 채우는 것)**: WER 유틸 부재 → `core/wer.py` 자체 구현(외부 의존 0·순수 Python
  Levenshtein). 정답 전사 있는 오디오 0건·meeting.db 부재 → **운영자 입력 슬롯**.
- **실행**: (오디오+ref 준비 후) GPU에서 faster-whisper 전사 → iMac에서 mlx 전사 →
  `python scripts/verify_stt_wer.py --audio <dir> --ref <dir>` 결과 `logs/x10_stt_wer_*.json`.

## C. 산출물 (이 세션)

1. 이 사전 등록 문서.
2. `core/wer.py` + `tests/test_wer.py` — 순수 Python WER/CER(외부 의존 0)·라벨 통제군으로 검증.
3. `scripts/verify_detect_equivalence.py` — 합성/실이미지 100장·편차<0.01 자기검증·결과 JSON.
   나는 GPU에서 **cpu-cpu sanity**(같은 device 두 번=편차 0)로 하네스 로직만 검증(mps는 iMac).
4. `scripts/verify_stt_wer.py` — GPU/iMac 전사 → normalize → WER 비교·데이터 슬롯·결과 JSON.
5. 운영자 브리프에 **X10 실행 항목**(iMac 실행·회의 3건+정답 전사 요구).

## D. 하네스 검증 (2026-08-26 · GPU에서 로직만)

산출물 5개 커밋 완료. **실측이 아니라 하네스 자체 검증**(mps·회의 데이터는 iMac/운영자):
- `core/wer.py` — 라벨 통제군 **10/10 통과**(치환·삽입·삭제·정규화·2%p 열화 시맨틱·CER 조사 구분).
- `scripts/verify_detect_equivalence.py --sanity`(cpu-cpu·12장·GPU) — **max_delta=0.00000 · PASS**
  = classify 결정성·합성 이미지 생성·결측 처리·판정 로직 정상(mps 자리에 cpu를 넣어 편차 0 확인).
- `scripts/verify_stt_wer.py` — CLI 3서브커맨드 파싱 정상(전사는 각 엔진 하드웨어 필요).

## E. 실측 결과 (대기 — iMac 실행·회의 데이터)

- detect: iMac에서 `--n 100`(합성 1차) → `--image-dir`(실이미지 확정). 운영자 브리프 §5-c.
- STT: 회의 3건+사람 정답 전사 투입 후 3단계. 운영자 브리프 §5-c.
- 두 결과가 PASS면 X10 종결·게이트 빈칸 채움. FAIL이면 위임이 결과를 바꾸는 것 → 위임 정책 재검토.
