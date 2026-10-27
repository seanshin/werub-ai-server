# 의료 영상 AI 모델 검토 보고서

> 작성일: 2026-05-12 | 대상: RTX 5080 16GB VRAM | WeRUBLLMManager v2.34.0

## 1. 개요

의료 영상(X-ray, CT, MRI, 병리, 안과 등) 판독을 위한 로컬 배포 가능 AI 모델을 조사하고,
RTX 5080 16GB 환경에서의 실행 가능성을 평가함.

## 2. HuggingFace 모델 비교

| 모델 | 파라미터 | VRAM (Q4) | 전문 영역 | GGUF/Ollama | HuggingFace |
|------|----------|-----------|-----------|-------------|-------------|
| **MedGemma 4B** (Google) | 4B | ~4GB | 방사선/피부/병리/안과 | ✅ GGUF 있음 | google/medgemma-4b-it |
| **CheXagent-2 3B** (Stanford) | 3B | ~3GB | 흉부 X-ray | 변환 필요 | StanfordAIMI/CheXagent-2-3b |
| **Radiology-Infer-Mini** | 7B | ~7GB | X-ray/MRI/CT/초음파 | 변환 필요 | prithivMLmods/Radiology-Infer-Mini |
| **UniMedVL 14B** | 14B | ~14GB | 9개 모달리티 | 변환, 빡빡 | General-Medical-AI/UniMedVL |
| **CONCH v1.5** (Harvard) | 307M | ~1.2GB | 병리 슬라이드 분류 | 비생성형 | MahmoodLab/conchv1_5 |
| **LLaVA-Rad 7B** (Microsoft) | 7B | ~7GB | 흉부 X-ray | 변환 필요 | microsoft/LLaVA-Rad |
| **BiomedCLIP** (Microsoft) | ~86M | ~400MB | 분류/검색/VQA | 비생성형 | microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224 |

## 3. Ollama 직접 사용 가능 모델

| 모델 | 크기 | 전문 영역 | 비고 |
|------|------|-----------|------|
| ALIENTELLIGENCE/medicalimaginganalysis | ~4GB | 의료 영상 전반 | 커뮤니티 모델, 임상 검증 X |
| llava:13b (프롬프트 활용) | ~8GB | 범용 비전 | 의료 파인튜닝 없음 |

## 4. 모델별 상세 분석

### 4.1 MedGemma 4B ⭐ (선정)

- **개발**: Google DeepMind
- **구조**: Gemma 2 기반 멀티모달 (이미지+텍스트)
- **전문 영역**: 방사선(CXR, CT), 피부과, 조직병리, 안과(안저/OCT)
- **VRAM**: ~4GB (Q4) — 기존 의료 LLM과 동시 로딩 가능
- **GGUF**: `unsloth/medgemma-4b-pt` 에서 제공
- **장점**:
  - Google 공식 모델 → 높은 신뢰도
  - 다영역 커버 (단일 모델로 4개+ 의료 영상 분야)
  - 가볍고 GGUF 준비 완료 → Ollama 즉시 탑재 가능
  - 기존 medllama2/meditron과 공존 가능 (VRAM 여유)

### 4.2 CheXagent-2 3B

- **개발**: Stanford AIMI
- **구조**: Instruction-tuned CXR 전문 모델
- **전문 영역**: 흉부 X-ray 전용
- **VRAM**: ~3GB — 매우 가벼움
- **장점**: GPT-4급 CXR 분석, 벤치마크 상위
- **한계**: 흉부 X-ray만 특화, GGUF 수동 변환 필요

### 4.3 UniMedVL 14B

- **개발**: General-Medical-AI
- **구조**: 14B 멀티모달
- **전문 영역**: CXR, CT, MRI, 초음파, 병리, 안저, OCT, 내시경, 피부 (9개)
- **VRAM**: ~14GB (Q4) — 16GB에 들어가지만 여유 없음
- **장점**: 가장 넓은 모달리티 커버
- **한계**: VRAM 빡빡, 다른 모델과 동시 사용 불가

### 4.4 LLaVA-Rad 7B (Microsoft)

- **구조**: LLaVA 백본, 흉부 X-ray 파인튜닝
- **성능**: GPT-4V, Med-PaLM M(84B) 능가 (Nature Communications 게재)
- **한계**: 공식 GGUF 없음, 흉부 X-ray만 특화

## 5. 선정 결과 및 도입 계획

### 1차 도입: MedGemma 4B
- Ollama GGUF 탑재 → Vision API 연동
- 기존 `/api/medical/*` + `/api/vision/*` Blueprint 활용

### 2차 검토 (필요 시):
- 흉부 X-ray 전문 → CheXagent-2 3B 추가
- 전 모달리티 → UniMedVL 14B (Q4, 단독 로딩)

## 6. 참고 자료

- [google/medgemma-4b-it](https://huggingface.co/google/medgemma-4b-it)
- [MedGemma - Google DeepMind](https://deepmind.google/models/gemma/medgemma/)
- [unsloth/medgemma-4b-pt (GGUF)](https://huggingface.co/unsloth/medgemma-4b-pt)
- [StanfordAIMI/CheXagent-2-3b](https://huggingface.co/StanfordAIMI/CheXagent-2-3b)
- [General-Medical-AI/UniMedVL](https://huggingface.co/General-Medical-AI/UniMedVL)
- [microsoft/LLaVA-Rad](https://github.com/microsoft/LLaVA-Rad)
- [ALIENTELLIGENCE/medicalimaginganalysis](https://ollama.com/ALIENTELLIGENCE/medicalimaginganalysis)
- [Open-Source LLMs in Radiology](https://pubs.rsna.org/doi/10.1148/radiol.241073)
- [Multimodal LLMs in Medical Imaging - PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC12479233/)
