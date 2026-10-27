# Meeting Analysis Phase 2 — 화자 분리 + Vision 슬라이드 분석

> **Feature**: meeting-phase2
> **Created**: 2026-06-02
> **Version Target**: v2.66.0
> **Priority**: P2 (High — hospital-jitsi 연동 확장)

## 1. 현황 (Phase 1 완료)

```
Phase 1 파이프라인 (v2.63.0):
  MP4 → ffmpeg 음성 추출 → faster-whisper STT → qwen3:8b 요약 → callback
```

| 항목 | 상태 |
|------|------|
| STT | ✅ faster-whisper large-v3 (GPU) |
| LLM 요약 | ✅ qwen3:8b (요약+액션아이템+하이라이트) |
| 화자 분리 | ❌ **없음** — 누가 말했는지 구분 불가 |
| 슬라이드 분석 | ❌ **없음** — 화면 공유 내용 분석 불가 |
| 다국어 | ⚠️ language 파라미터만 (자동 감지 없음) |

## 2. 목표

| 목표 | 설명 |
|------|------|
| 화자 분리 | pyannote-audio로 "누가 말했는지" 구분 → 요약에 화자별 발언 포함 |
| Vision 분석 | 키프레임 추출 → qwen3-vl:8b로 슬라이드/문서 내용 분석 |
| 언어 자동 감지 | whisper language=auto → 다국어 회의 지원 |
| 화자+STT 매핑 | 타임스탬프 기반 화자↔발언 매핑 |

## 3. 구현 범위

### Phase 2-1: 화자 분리 (pyannote-audio)

#### 의존성
```bash
pip install pyannote.audio  # torch 2.10+ 필요 (이미 설치)
```
- pyannote/speaker-diarization-3.1 모델 (HuggingFace, ~300MB)
- `HUGGINGFACE_TOKEN` 필요 (pyannote 모델 접근용)

#### core/meeting_diarize.py (신규)
```python
def diarize(wav_path, *, num_speakers=None, min_speakers=2, max_speakers=10):
    """화자 분리 실행

    Args:
        wav_path: WAV 파일 경로
        num_speakers: 화자 수 힌트 (None=자동)
        min_speakers: 최소 화자 수
        max_speakers: 최대 화자 수

    Returns:
        [{"speaker": "SPEAKER_00", "start": 0.5, "end": 5.3}, ...]
    """
```

#### STT + 화자 매핑
```python
def merge_stt_diarization(stt_segments, diarization_segments):
    """STT 세그먼트와 화자 분리 결과를 타임스탬프 기반으로 병합

    Returns:
        [{"speaker": "SPEAKER_00", "start": 0.5, "end": 5.3, "text": "..."}, ...]
    """
```

### Phase 2-2: Vision 슬라이드 분석

#### core/meeting_vision.py (신규)
```python
def extract_keyframes(mp4_path, output_dir, *, interval_sec=30, scene_detect=True):
    """MP4에서 키프레임 추출

    - interval_sec: 기본 30초 간격
    - scene_detect: ffmpeg scene 변화 감지 (슬라이드 전환)

    Returns:
        [{"path": "/tmp/.../frame_0030.jpg", "timestamp": 30.0}, ...]
    """

def analyze_frames(frames, *, model="qwen3-vl:8b", ollama_session=None, ollama_base=None):
    """키프레임 분석 — 슬라이드 내용 텍스트 추출

    Returns:
        [{"timestamp": 30.0, "description": "...", "slide_text": "..."}, ...]
    """
```

### Phase 2-3: 파이프라인 확장

```
Phase 2 파이프라인:
  MP4 → ffmpeg 음성 추출 → faster-whisper STT
     ├─ pyannote 화자 분리 (병렬)
     └─ ffmpeg 키프레임 추출 (병렬)
  → STT+화자 매핑 → Vision 슬라이드 분석
  → qwen3:8b 종합 요약 (화자별 + 슬라이드 컨텍스트)
  → force_korean → callback
```

## 4. API 변경

### POST /api/meeting/analyze (확장, 하위 호환)

```json
{
  "file_url": "https://s3.wasabisys.com/.../recording.mp4",
  "callback_url": "https://hospital-jitsi/...",
  "options": {
    "language": "auto",
    "diarize": true,
    "num_speakers": null,
    "vision": true,
    "keyframe_interval": 30
  }
}
```

- `diarize` 생략/false → Phase 1 동작 (하위 호환)
- `vision` 생략/false → 키프레임 분석 건너뜀

### GET /api/meeting/job/{id} (응답 확장)

```json
{
  "id": "meet-xxx",
  "status": "completed",
  "transcript": "전체 텍스트",
  "speaker_segments": [
    {"speaker": "화자1", "start": 0.5, "end": 5.3, "text": "오늘 안건은..."}
  ],
  "slides": [
    {"timestamp": 30.0, "description": "매출 현황 슬라이드", "slide_text": "2026 Q2 매출..."}
  ],
  "summary": "화자별 요약 포함",
  "speakers_detected": 3
}
```

## 5. 수정 파일 목록

| 파일 | 변경 | 설명 |
|------|------|------|
| `core/meeting_diarize.py` | **신규** | pyannote 화자 분리 래퍼 |
| `core/meeting_vision.py` | **신규** | 키프레임 추출 + Vision 분석 |
| `core/meeting_pipeline.py` | 확장 | 화자 분리/Vision 단계 추가 |
| `core/meeting_stt.py` | 소폭 | language=auto 지원 |
| `routes/meeting.py` | 확장 | options 파라미터 + 응답 필드 추가 |
| `meeting.db` | 확장 | speaker_segments, slides 컬럼 |

## 6. 구현 순서

```
Step 1: pyannote-audio 설치 + HuggingFace 토큰 설정
  ↓
Step 2: core/meeting_diarize.py — 화자 분리 + STT 매핑
  ↓
Step 3: core/meeting_vision.py — 키프레임 추출 + Vision 분석
  ↓
Step 4: core/meeting_pipeline.py 확장 — 병렬 처리 + 종합 요약
  ↓
Step 5: routes/meeting.py + DB 스키마 확장
  ↓
Step 6: E2E 테스트 (테스트 MP4 → 화자 분리 + 슬라이드 분석)
  ↓
Step 7: GPU 서버 배포 + 문서 갱신
```

## 7. 리스크 & 대응

| 리스크 | 대응 |
|--------|------|
| pyannote HuggingFace 토큰 필요 | 무료 계정으로 접근 가능, 사전 동의 필요 |
| GPU VRAM 부족 (whisper+pyannote+ollama) | 순차 실행으로 VRAM 교대 사용, pyannote는 ~2GB |
| 화자 분리 정확도 | num_speakers 힌트로 개선, 참가자 목록 활용 |
| 키프레임 디스크 사용량 | JPG 압축 + 분석 후 즉시 삭제 |
| Phase 1 하위 호환 | diarize/vision 기본값 false |

## 8. GPU 메모리 계획

```
RTX 5080: 16GB VRAM

순차 실행 (VRAM 교대):
  1. faster-whisper large-v3: ~4GB → 완료 후 해제
  2. pyannote 3.1: ~2GB → 완료 후 해제
  3. Ollama qwen3-vl:8b: ~6GB → Vision 분석
  4. Ollama qwen3:8b: ~5GB → 종합 요약
```

## 9. 성공 기준

- [ ] pyannote 화자 분리 동작 (2인 이상 구분)
- [ ] STT+화자 타임스탬프 매핑 정확도 > 80%
- [ ] 키프레임 추출 + Vision 분석 동작
- [ ] 종합 요약에 "화자1이 ... 발언" 형태 포함
- [ ] Phase 1 하위 호환 (diarize/vision 없으면 기존 동작)
- [ ] 총 처리 시간 < 30분 (30분 녹화 기준)
