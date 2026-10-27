# Release v2.78.0 — AI 판별 ML 화소 분류 (B2/B3)

> 2026-06-07 | v2.77.0(마킹 A1/A2 + 판별 B1 메타데이터)에 ML 화소 분류 추가

## 신규: ML 기반 AI 이미지 판별 — `core/ai_image_detector.py`

v2.77.0의 `POST /api/detect/image`는 메타데이터/C2PA·타툴 휴리스틱만 판독했다(마킹·메타가 있는 경우만 탐지). v2.78.0은 **마킹/메타가 없는 임의 이미지**를 화소 패턴으로 AI 추정하는 ML 분류기를 추가한다.

- **모델**: `Organika/sdxl-detector` (umm-maybe AI-art-detector 개선판, transformers image-classification). 라벨 `human`/`artificial` → `artificial` score = `ai_probability`.
- **실행**: 오픈소스 자체 호스팅(외부 API 0). CPU 추론 ~0.1s(로드 1회 ~8s, lazy). VRAM 경합 없도록 기본 CPU.
- **응답 확장**: `ml`(available/ai_probability/label/model/scores), 최상위 `ai_probability`, 종합 `confidence`(high=마킹/C2PA, medium=타툴 메타, low=ML 추정만, none), `method`(metadata[+ml]).
- **config**: `ai_detector_enabled`(기본 True) / `ai_detector_model` / `ai_detector_device`(cpu|cuda) / `ai_detector_threshold`(기본 0.7). 요청별 `ml` 파라미터(form/json)로 on/off 오버라이드.
- **동시성**: 모델 로드/추론에 Lock(파이프라인 보호). transformers/torch 없으면 `available:false`로 graceful degrade.

### ⚠️ 정확도·면책
ML 추정치는 신규 생성기·압축·스크린샷·편집본에 **오탐/미탐**이 있다. 확정 신호는 우리 마킹(`read_marking`)이 우선이며, 본 결과는 **법적 판단의 단독 근거가 될 수 없다**(응답 disclaimer 명시). SynthID 워터마크는 비공개라 미탐지.

## 검증
- `check_syntax` 123 파일 통과.
- `tests/test_ai_image_detector.py`(순수 로직 5건 + 실추론 1건) + `test_content_marking.py` 14건 → iMac 19 PASS / 1 skip(실추론은 GPU). GPU 모델 로드·라벨 검증 완료(human/artificial, 추론 0.09s).

## v2.78.1 (2026-06-07) — torch 스레드 캡 (부분 완화)
- **발견**: 라이브 검증 중 detect ML 첫 호출로 워커에 모델 로드 시 torch가 CPU 코어수만큼 스레드 생성 → 워커 nlwp 9→**69**(추론은 0.1s라 불필요).
- **수정**: `_cap_torch_threads()` — 모델 로드 전 `torch.set_num_threads(4)`(config `ai_detector_threads`, 기본 4).
- **결과(정직)**: 69→**~50**으로 감소(torch intra-op 풀만 캡됨). 잔여 ~46은 OpenMP/MKL **네이티브 풀**로, 전역 `OMP_NUM_THREADS` env로만 완전 캡 가능하나 그러면 **whisper STT 성능 저하** 트레이드오프 → 미적용. **~50은 인시던트 고갈 임계(90+) 미만이고 스레드는 유휴** → 허용. 진단 시 detector 사용 후 워커 베이스라인 ~50임을 인지할 것(→ INCIDENT_DIAG). 완전 격리가 필요하면 detector를 별도 프로세스로 분리(후속 후보).

## 잔여 (후속)
- A3 config 가이드 문서 / A4 C2PA 서명 매니페스트 / 영상 마킹(ffmpeg) / (선택) 딥페이크 얼굴조작 전용 모델.
