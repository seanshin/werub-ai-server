# RELEASE v2.124.4 — heavy_model qwen3.6 + iMac 링크 복구 + 보조 추론 사이드카 착수

**날짜**: 2026-07-09
**헤드라인**: ①heavy_model을 grounded A/B(N=40 at-parity)로 검증된 `qwen3.6:35b-a3b`로 재지정
+think 주입, ②iMac 연계(8586 터널·헬스체크) 복구, ③의료영상 **보조 추론 사이드카** 착수
(클라이언트 레이어+테스트+기획+정확도 실측). 운영 정비(디스크 231G 회수·배포 위생)도 포함.

---

## 1. 배포된 변경 (라이브)

### heavy_model = qwen3.6:35b-a3b + think 제어 (`routes/heavy.py`)
- 디스크 정리 중 heavy 실험 잔재(`qwen3:30b-a3b` 18G·`llama3.3:70b-q2_K` 26G) 제거.
- **grounded A/B(의료법 N=40)**: qwen3.6:35b-a3b 충실도 **0.931** vs 상주 qwen2.5:14b 0.908
  ·환각/품질 동률 ·6× 느림 = **at-parity**(구 30b-a3b NO-GO 0.676에서 세대 개선). 상주 교체 아닌
  품질-tier로 `heavy_model` 기본값 지정.
- ⚠️ qwen3.6는 thinking 모델 → `/api/chat/heavy` payload에 **`think=false` 기본 주입**
  (추론 토큰만 내는 빈응답 방지, `data.think=true`로 오버라이드). swagger think 파라미터 추가.

### iMac 연계 복구 (`routes/imac_link.py` + GPU 브리지)
- **8586 헬스체크 오탐 fix**: `_ping_8586`이 raw Ollama용 `GET /api/tags`(iMac admin에선 405)를
  찔러 터널 정상에도 red → `/api/status`(200+모델목록)로 판정 변경.
- **8586 터널 복구**: GPU `ollama-worker-bridge.service`가 8587만 포워딩 → `-L 8586` 추가.
  (웹서버엔 8586 정상 바인딩이었고 GPU 포워드만 누락.)
- detect/mark/STT 위임(8587)은 정상 확인. 오케스트레이터 노드 도달성=워커 리사이클 시 리로드
  후 registry/reload로 즉시 복구.

## 2. 보조 추론 사이드카 착수 — 신형 VLM 의료영상 (연구+스캐폴딩)

Ollama 0.30.11이 신형 VLM vision(Qwen3-VL·gemma4)을 못 돌리는 갭(빈 응답)을 GPU 서비스 무중단으로
메꾸는 **Ollama 밖 온디맨드 사이드카** 트랙 착수. 기획 `docs/01-plan/features/auxiliary-inference-sidecar.plan.md`.

- **클라이언트 레이어**(`core/aux_infer.py`+`tests/test_aux_infer.py` 13 passed): OpenAI 호환
  `/v1/chat/completions` 계약·engine(ollama|sidecar|dual) 안전 강등·status·graceful None(호출자 폴백).
  **기본 off=기존 동작 불변·라이브 의료 경로 무접촉**(아직 미배선).
- **통합 설계**: 기존 `/api/vision/medical/*`(analyze·report·**dual-read**) 뒤 내부 engine 분기
  =**호출자 API 무변경**. 반쪽 난 dual-read(삭제된 LLaVA-Med 슬롯)를 사이드카 모델로 부활.
- **📊 정확도 실측(KorMedMCQA-V)**: MedGemma-1.5-4b(현행) **32.5%** → Qwen3-VL-8B **40%**(무의미차)
  → Qwen3-VL-32B-Instruct-FP8 **60%**(N10) → 32B-Thinking **83.7%**(논문). **스케일이 답=큰 오픈
  VLM이 한국어 의료영상 실제 개선**. 단 **32B(32GB)가 16GB VRAM 초과→CPU 오프로드로 느림**(6.3s/문항).
  fittable 8B는 무의미.
- **판정**: 사이드카 개념 검증. 실용화는 ①단기=비실시간 "하드케이스 2차의견"(dual-read) ②근본=
  **24GB+ GPU**(16GB 천장·heavy tier와 동일 업그레이드).
- **인프라 실측**(재현): 시스템 무변경 GPU 경로=**HF transformers+torch cu128 격리 venv**(PyTorch 자체
  CUDA=Blackwell sm_120 구동). ⛔ Vulkan(가상화 환경 alloc 실패)·CPU(hung)·시스템 CUDA 12.0(Blackwell
  미지원)·Docker(nvidia-container-toolkit 미설치)는 모두 사(死). FP8=`kernels==0.15.2`+Triton 폴백.

## 3. 운영 정비

- **디스크 231G 회수**(91%→64%): 범위 밖 Portal 프로젝트 188G(소스 백업 후 삭제)+heavy 잔재 44G
  +journal/eval셋. [[project_disk_cleanup_20260621]].
- **배포 위생**: `chromadb_data/` 런타임 벡터스토어 추적 해제+gitignore(런타임 수정이 git pull 차단하던 문제).
  ⚠️ `clinic_codes.db`(런타임 기록+시드 겹침·3버전 분기)는 데이터 손실 위험으로 강제 동기화 보류(팀 정책 사안).

## 검증

- 전 과정 GPU 서비스 무손상(heavy·imac 변경 라이브 반영·health 200·NRestarts 0). 사이드카 PoC는
  서비스 창(자동 복원 안전장치, 매번 health 200) 또는 격리 venv로 무중단 실행.
- 회귀: `test_aux_infer` 13 passed(신규). 사전 문법훅 262 .py 통과.
- ⚠️ 사이드카는 **미배선 스캐폴딩+연구**(프로덕션 기능 아님) — 실배선은 24GB GPU 또는 비실시간 통합 결정 후.
