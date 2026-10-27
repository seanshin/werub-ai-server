# Meeting Analysis Design Document

> **Summary**: 화상회의 녹화본(MP4) AI 분석 파이프라인 — STT + Vision + LLM 요약 + 콜백
>
> **Project**: WeRU.B AI Server
> **Version**: v2.63.0 (예정)
> **Author**: AI_Server
> **Date**: 2026-06-02
> **Status**: Draft

---

## 1. Overview

### 1.1 Design Goals

- hospital-jitsi의 `recording.completed` webhook을 받아 완전 자동 분석
- 기존 counseling.py 오케스트레이션 패턴을 재사용하여 일관된 아키텍처 유지
- GPU 리소스를 순차 파이프라인으로 관리하여 기존 서비스 영향 최소화
- 분석 결과를 콜백으로 push하되, 폴링도 지원하여 복원력 확보

### 1.2 Design Principles

- **기존 패턴 재사용**: counseling.py의 Blueprint/init/DB/세션 패턴 그대로 적용
- **순차 파이프라인**: VRAM 충돌 방지 — 다운로드 → STT → Vision → LLM → 콜백
- **외부 의존 0**: faster-whisper + ffmpeg + Ollama, 외부 API 호출 없음
- **Fail-safe**: 각 단계 실패 시 상태 기록, 재시도 가능, 콜백 실패 시 폴링 대체

---

## 2. Architecture

### 2.1 Component Diagram

```
hospital-jitsi                     AI Server
    │                                  │
    │  POST /api/meeting/analyze       │
    │ ──────────────────────────────→  │
    │  {file_url, callback_url, meta}  │
    │                                  │
    │  ←── 202 {job_id, status}        │
    │                                  │
    │         ┌────────────────────────┤
    │         │  Worker Thread         │
    │         │  ┌──────────────────┐  │
    │         │  │ ① download_mp4   │  │
    │         │  │ ② extract_audio  │  │
    │         │  │ ③ transcribe_stt │  │
    │         │  │ ④ extract_frames │  │
    │         │  │ ⑤ analyze_slides │  │
    │         │  │ ⑥ llm_analyze    │  │
    │         │  │ ⑦ post_process   │  │
    │         │  │ ⑧ send_callback  │  │
    │         │  └──────────────────┘  │
    │         └────────────────────────┤
    │                                  │
    │  ←── POST callback_url           │
    │  {job_id, result}                │
    │                                  │
    │  GET /api/meeting/job/{id}       │
    │ ──────────────────────────────→  │ (폴링 대체)
```

### 2.2 내부 모듈 구조

```
routes/meeting.py              ← Blueprint (6 API, HTTP 계층)
    │
    ├── core/meeting_pipeline.py   ← 파이프라인 오케스트레이터
    │       │
    │       ├── core/meeting_stt.py    ← faster-whisper 래퍼
    │       ├── core/meeting_vision.py ← 프레임 추출 + Vision API 호출 (선택)
    │       ├── core/korean.py         ← force_korean (기존)
    │       └── core/korean_humanize.py ← 품질 검증 (기존)
    │
    └── meeting.db                 ← SQLite (작업/결과 저장)
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `routes/meeting.py` | `core/meeting_pipeline.py` | 파이프라인 실행 위임 |
| `core/meeting_pipeline.py` | `core/meeting_stt.py` | STT 처리 |
| `core/meeting_pipeline.py` | `core/meeting_vision.py` | 슬라이드 분석 (선택) |
| `core/meeting_pipeline.py` | `core/korean.py` | force_korean 후처리 |
| `core/meeting_pipeline.py` | `core/korean_humanize.py` | 품질 검증 |
| `core/meeting_stt.py` | `faster-whisper` | CTranslate2 STT 엔진 |
| `core/meeting_vision.py` | Ollama `qwen3-vl:8b` | 이미지 분석 |
| `core/meeting_pipeline.py` | Ollama `qwen3:8b` | LLM 요약/추출 |

---

## 3. Data Model

### 3.1 DB 스키마 (meeting.db)

```sql
CREATE TABLE IF NOT EXISTS meeting_jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    -- pending → downloading → transcribing → analyzing → completed / failed

    -- 입력
    file_url TEXT NOT NULL,
    callback_url TEXT,
    options TEXT DEFAULT '{}',           -- JSON: {summary, action_items, highlights, slides, language}
    metadata TEXT DEFAULT '{}',          -- JSON: {meeting_id, room_name, participants, started_at, duration_sec}

    -- 결과
    transcript TEXT,                     -- 전체 녹취록 텍스트
    segments TEXT,                       -- JSON: [{start, end, text}, ...]
    summary TEXT,                        -- 요약 텍스트
    action_items TEXT,                   -- JSON: [{assignee, task, deadline, context}, ...]
    highlights TEXT,                     -- JSON: [{time, text, importance}, ...]
    slides TEXT,                         -- JSON: [{time, content}, ...]
    korean_quality TEXT,                 -- JSON: {korean_ratio, risk_band}

    -- 에러
    error TEXT,
    error_stage TEXT,                    -- 실패 단계: download/transcribe/analyze/callback

    -- 타이밍
    stt_duration_ms INTEGER DEFAULT 0,
    vision_duration_ms INTEGER DEFAULT 0,
    llm_duration_ms INTEGER DEFAULT 0,
    total_duration_ms INTEGER DEFAULT 0,

    -- 콜백
    callback_sent INTEGER DEFAULT 0,     -- 0/1
    callback_attempts INTEGER DEFAULT 0,
    callback_error TEXT,

    -- 타임스탬프
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_meeting_jobs_status ON meeting_jobs(status);
CREATE INDEX IF NOT EXISTS idx_meeting_jobs_created ON meeting_jobs(created_at);
```

---

## 4. API Specification

### 4.1 Endpoint List

| # | Method | Path | Description | Auth |
|---|--------|------|-------------|------|
| 1 | POST | `/api/meeting/analyze` | 분석 요청 접수 | Bearer |
| 2 | GET | `/api/meeting/job/<id>` | 작업 상태/결과 조회 | Bearer |
| 3 | GET | `/api/meeting/jobs` | 작업 목록 (최근 50건) | Bearer |
| 4 | POST | `/api/meeting/job/<id>/retry` | 실패 작업 재시도 | Bearer |
| 5 | DELETE | `/api/meeting/job/<id>` | 작업 삭제 | Bearer |
| 6 | GET | `/api/meeting/status` | 시스템 상태 | Bearer |

### 4.2 Detailed Specification

#### `POST /api/meeting/analyze`

**Request:**
```json
{
    "file_url": "https://s3.wasabisys.com/bucket/recording.mp4?X-Amz-...",
    "callback_url": "https://hospital-jitsi/meet-api/internal/ai-callback",
    "options": {
        "summary": true,
        "action_items": true,
        "highlights": true,
        "slides": true,
        "language": "ko"
    },
    "metadata": {
        "meeting_id": "mtg-20260602-001",
        "room_name": "주간정례회의",
        "participants": [
            {"name": "김팀장", "jid": "user1@jitsi"},
            {"name": "이대리", "jid": "user2@jitsi"}
        ],
        "started_at": "2026-06-02T09:00:00Z",
        "duration_sec": 3600
    }
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file_url` | string | Yes | S3 Presigned URL (MP4) |
| `callback_url` | string | No | 분석 완료 시 결과 전송 URL |
| `options` | object | No | 분석 옵션 (기본 모두 true) |
| `options.summary` | bool | No | 요약 생성 여부 (default: true) |
| `options.action_items` | bool | No | 액션아이템 추출 여부 (default: true) |
| `options.highlights` | bool | No | 하이라이트 추출 여부 (default: true) |
| `options.slides` | bool | No | 슬라이드 분석 여부 (default: false) |
| `options.language` | string | No | 분석 언어 (default: "ko") |
| `metadata` | object | No | 회의 메타데이터 (콜백 시 그대로 반환) |

**Response (202 Accepted):**
```json
{
    "job_id": "meet-a1b2c3d4",
    "status": "pending",
    "estimated_minutes": 15,
    "poll_url": "/api/meeting/job/meet-a1b2c3d4"
}
```

**Error Responses:**
- `400`: file_url 누락
- `401`: 인증 실패
- `503`: 동시 작업 한도 초과 (2건)

---

#### `GET /api/meeting/job/<id>`

**Response (completed):**
```json
{
    "id": "meet-a1b2c3d4",
    "status": "completed",
    "file_url": "https://s3...",
    "metadata": {"meeting_id": "mtg-20260602-001", "room_name": "주간정례회의", "...": "..."},
    "result": {
        "transcript": {
            "full_text": "전체 녹취록...",
            "segments": [
                {"start": 0.0, "end": 5.2, "text": "회의를 시작하겠습니다."},
                {"start": 5.5, "end": 12.1, "text": "지난주 진행 사항부터 보겠습니다."}
            ],
            "word_count": 5200
        },
        "summary": "이번 주간 정례회의에서는...",
        "action_items": [
            {
                "assignee": "이대리",
                "task": "보고서 수정 후 재제출",
                "deadline": "2026-06-05",
                "context": "15:30 발언 참조"
            }
        ],
        "highlights": [
            {"time": "00:15:30", "text": "핵심 발언 내용...", "importance": "high"}
        ],
        "slides": [
            {"time": "00:05:00", "content": "분기별 매출 현황 슬라이드 내용..."}
        ],
        "korean_quality": {
            "korean_ratio": 0.95,
            "risk_band": "low"
        }
    },
    "pipeline": {
        "stt_model": "faster-whisper/large-v3",
        "llm_model": "qwen3:8b",
        "vision_model": "qwen3-vl:8b",
        "stt_duration_ms": 120000,
        "vision_duration_ms": 15000,
        "llm_duration_ms": 30000,
        "total_duration_ms": 180000
    },
    "callback_sent": true,
    "created_at": "2026-06-02T09:30:00Z",
    "completed_at": "2026-06-02T09:45:00Z"
}
```

**Response (in progress):**
```json
{
    "id": "meet-a1b2c3d4",
    "status": "transcribing",
    "progress": "STT 처리 중 (3/6 단계)",
    "created_at": "2026-06-02T09:30:00Z",
    "started_at": "2026-06-02T09:30:05Z"
}
```

**Response (failed):**
```json
{
    "id": "meet-a1b2c3d4",
    "status": "failed",
    "error": "S3 다운로드 실패: 403 Forbidden (Presigned URL 만료)",
    "error_stage": "download",
    "created_at": "2026-06-02T09:30:00Z"
}
```

---

#### `GET /api/meeting/jobs`

Query params: `?limit=50&status=completed`

**Response:**
```json
{
    "jobs": [
        {"id": "meet-...", "status": "completed", "metadata": {"room_name": "..."}, "created_at": "...", "total_duration_ms": 180000}
    ],
    "total": 15
}
```

---

#### `POST /api/meeting/job/<id>/retry`

- `failed` 상태 작업만 재시도 가능
- 상태를 `pending`으로 리셋, worker가 재처리

**Response:** `{"ok": true, "job_id": "meet-...", "status": "pending"}`

---

#### `DELETE /api/meeting/job/<id>`

- `completed` 또는 `failed` 상태만 삭제 가능

**Response:** `{"ok": true}`

---

#### `GET /api/meeting/status`

**Response:**
```json
{
    "service": "meeting-analysis",
    "whisper_available": true,
    "whisper_model": "large-v3",
    "llm_model": "qwen3:8b",
    "vision_model": "qwen3-vl:8b",
    "ffmpeg_available": true,
    "active_jobs": 1,
    "max_concurrent": 2,
    "total_completed": 42,
    "total_failed": 2
}
```

---

## 5. Pipeline Detail Design

### 5.1 `core/meeting_pipeline.py` — 오케스트레이터

```python
class MeetingPipeline:
    """회의 분석 파이프라인 오케스트레이터"""

    def __init__(self, db_path, ollama_base, ollama_session, tmp_dir):
        self._db_path = db_path
        self._ollama_base = ollama_base
        self._session = ollama_session
        self._tmp_dir = tmp_dir
        self._semaphore = threading.Semaphore(2)  # 동시 2건

    def submit(self, job_id, file_url, callback_url, options, metadata):
        """작업 접수 → worker thread 시작"""
        thread = threading.Thread(
            target=self._run_pipeline,
            args=(job_id,), daemon=True
        )
        thread.start()

    def _run_pipeline(self, job_id):
        """순차 파이프라인 실행"""
        with self._semaphore:
            try:
                self._update_status(job_id, "downloading")
                mp4_path = self._download_mp4(job_id)

                self._update_status(job_id, "transcribing")
                audio_path = self._extract_audio(mp4_path)
                transcript, segments = self._transcribe(audio_path)

                slides = []
                if self._get_option(job_id, "slides"):
                    frames = self._extract_frames(mp4_path)
                    slides = self._analyze_slides(frames)

                self._update_status(job_id, "analyzing")
                summary, action_items, highlights = self._llm_analyze(
                    transcript, segments, slides, job_id
                )

                # 한국어 후처리
                summary = self._postprocess_korean(summary)
                # action_items 텍스트 필드도 개별 처리

                self._save_result(job_id, transcript, segments,
                                  summary, action_items, highlights, slides)
                self._update_status(job_id, "completed")
                self._send_callback(job_id)

            except Exception as e:
                self._mark_failed(job_id, str(e), stage)
            finally:
                self._cleanup_tmp(job_id)
```

### 5.2 `core/meeting_stt.py` — STT 래퍼

```python
from faster_whisper import WhisperModel

class MeetingSTT:
    """faster-whisper 싱글톤 래퍼"""

    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = WhisperModel(
                "large-v3",
                device="cuda",
                compute_type="float16"
            )
        return cls._instance

    @classmethod
    def transcribe(cls, audio_path, language="ko"):
        model = cls.get_instance()
        segments, info = model.transcribe(
            audio_path,
            language=language,
            beam_size=5,
            word_timestamps=False,
            vad_filter=True,          # 무음 구간 자동 제거
            vad_parameters={
                "min_silence_duration_ms": 500
            }
        )
        result_segments = []
        full_text_parts = []
        for seg in segments:
            result_segments.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip()
            })
            full_text_parts.append(seg.text.strip())

        return " ".join(full_text_parts), result_segments
```

### 5.3 `core/meeting_vision.py` — 프레임 추출 + Vision 분석 (선택)

```python
import subprocess
import base64

class MeetingVision:
    """ffmpeg 장면 전환 프레임 추출 + Ollama Vision 분석"""

    @staticmethod
    def extract_keyframes(mp4_path, output_dir, threshold=0.3, max_frames=20):
        """장면 전환 감지로 키프레임 추출"""
        cmd = [
            "ffmpeg", "-i", mp4_path,
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-vsync", "vfr",
            "-frames:v", str(max_frames),
            f"{output_dir}/frame_%04d.png",
            "-y", "-loglevel", "warning"
        ]
        subprocess.run(cmd, check=True, timeout=120)
        # 추출된 PNG 파일 목록 반환

    @staticmethod
    def analyze_frame(ollama_base, session, image_path):
        """단일 프레임 Vision 분석"""
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        resp = session.post(f"{ollama_base}/api/generate", json={
            "model": "qwen3-vl:8b",
            "prompt": "이 이미지의 텍스트와 내용을 한국어로 설명하세요. 슬라이드나 화면 공유 내용이면 핵심 내용을 요약하세요.",
            "images": [img_b64],
            "stream": False
        })
        return resp.json().get("response", "")
```

### 5.4 LLM 분석 프롬프트

#### 요약 프롬프트
```
당신은 회의록 요약 전문가입니다.

아래 회의 녹취록을 읽고 다음을 수행하세요:

1. **요약** (3~5줄): 회의의 핵심 내용과 결론을 요약
2. **액션아이템**: 구체적인 후속 조치를 JSON 배열로 추출
   - assignee: 담당자 이름 (언급된 경우)
   - task: 할 일 내용
   - deadline: 기한 (언급된 경우)
   - context: 관련 발언 시간대
3. **하이라이트**: 중요한 발언/결정 사항을 JSON 배열로 추출
   - time: 발언 시간 (HH:MM:SS)
   - text: 발언 내용
   - importance: high/medium

규칙:
- 반드시 한국어로 작성
- 존댓말 사용
- 추측하지 말고 녹취록에 있는 내용만 기반
- JSON은 코드블록으로 감싸지 말고 순수 JSON으로 출력

참석자 정보: {participants}
슬라이드 내용: {slides_context}

## 녹취록:
{transcript}

## 출력 형식:
SUMMARY:
(요약 텍스트)

ACTION_ITEMS:
[{json array}]

HIGHLIGHTS:
[{json array}]
```

### 5.5 콜백 전송

```python
def _send_callback(self, job_id, max_retries=3):
    """분석 결과를 hospital-jitsi에 콜백 전송"""
    job = self._get_job(job_id)
    if not job["callback_url"]:
        return

    payload = self._build_callback_payload(job)

    for attempt in range(max_retries):
        try:
            resp = self._session.post(
                job["callback_url"],
                json=payload,
                timeout=30
            )
            if resp.status_code < 400:
                self._update_callback_status(job_id, sent=True, attempts=attempt + 1)
                return
        except Exception as e:
            logger.warning(f"Callback attempt {attempt+1} failed: {e}")

        # 지수 백오프: 5s, 25s, 125s
        time.sleep(5 ** (attempt + 1))

    self._update_callback_status(job_id, sent=False, attempts=max_retries,
                                  error="Max retries exceeded")
```

### 5.6 긴 녹화본 청크 처리

```python
CHUNK_DURATION_SEC = 1800  # 30분

def _transcribe_chunked(self, audio_path, duration_sec):
    """30분 단위 청크 분할 STT"""
    if duration_sec <= CHUNK_DURATION_SEC:
        return MeetingSTT.transcribe(audio_path)

    chunks = self._split_audio(audio_path, CHUNK_DURATION_SEC)
    all_segments = []
    all_text_parts = []
    offset = 0.0

    for chunk_path in chunks:
        text, segments = MeetingSTT.transcribe(chunk_path)
        # 타임스탬프 오프셋 보정
        for seg in segments:
            seg["start"] += offset
            seg["end"] += offset
        all_segments.extend(segments)
        all_text_parts.append(text)
        offset += CHUNK_DURATION_SEC

    return " ".join(all_text_parts), all_segments
```

---

## 6. Error Handling

### 6.1 단계별 에러 처리

| Stage | Error | Handling |
|-------|-------|----------|
| `download` | URL 만료/403 | `failed` + error_stage="download", 재시도 가능 |
| `download` | 네트워크 타임아웃 | 3회 재시도 후 `failed` |
| `download` | 디스크 부족 | `failed` + 경고 로그 |
| `transcribe` | Whisper 모델 미로드 | lazy init 재시도, CUDA OOM 시 CPU 폴백 |
| `transcribe` | 오디오 없음 (무음) | 빈 녹취록으로 완료 (경고 포함) |
| `analyze` | LLM 응답 파싱 실패 | 원본 텍스트로 폴백, 구조화 없이 전달 |
| `analyze` | Ollama 미가동 | `failed` + error_stage="analyze" |
| `callback` | 대상 서버 응답 없음 | 3회 지수 백오프 재시도, 폴링 API 안내 |

### 6.2 Error Response Format

```json
{
    "error": {
        "code": "DOWNLOAD_FAILED",
        "message": "S3 Presigned URL이 만료되었습니다.",
        "stage": "download",
        "retryable": true
    }
}
```

---

## 7. Security Considerations

- [x] Bearer 인증 필수 (hospital-jitsi 전용 API 키)
- [x] S3 Presigned URL 검증 (HTTPS만 허용)
- [x] 원본 MP4 다운로드 후 분석 완료 시 즉시 삭제
- [x] tmp 디렉토리 자동 정리 (실패 시에도)
- [x] callback_url 검증 (HTTPS만 허용, 내부 IP 차단)
- [x] 동시 작업 제한 (세마포어 2건) — DoS 방지
- [x] 입력 크기 제한 (file_url 2048자, metadata 10KB)

---

## 8. Test Plan

### 8.1 Test Scope

| Type | Target | Method |
|------|--------|--------|
| Unit | STT 래퍼 | 짧은 WAV 파일로 검증 |
| Unit | LLM 프롬프트 파싱 | 모의 응답으로 파싱 로직 검증 |
| Integration | 전체 파이프라인 | 실제 MP4 → 결과 JSON E2E |
| Integration | 콜백 전송 | mock 서버로 수신 확인 |
| Load | 동시 2건 | 세마포어 동작 + 3번째 요청 503 확인 |

### 8.2 Test Cases (Key)

- [x] Happy path: 1시간 MP4 → 녹취록+요약+액션아이템+콜백 정상 완료
- [x] 에러: 만료된 Presigned URL → `failed` + error_stage="download"
- [x] 에러: 무음 녹화본 → 빈 녹취록으로 완료 (경고 포함)
- [x] 에러: 콜백 서버 다운 → 3회 재시도 후 callback_sent=false
- [x] 에러: 동시 3건 요청 → 3번째 503 반환
- [x] Edge: 2시간+ 녹화본 → 30분 청크 분할 정상 처리
- [x] Retry: failed 작업 → POST retry → 재처리 성공

---

## 9. Coding Convention (프로젝트 기존 패턴 준수)

| Item | Convention |
|------|-----------|
| Blueprint 패턴 | `meeting_bp = Blueprint("meeting", __name__)` |
| init 함수 | `init_meeting(*, base_dir, ollama_session, ollama_base)` |
| DB 접근 | `_get_db()` → `sqlite3.connect`, `Row` factory, WAL mode |
| 로거 | `logger = logging.getLogger("ollama-admin")` |
| 한국어 후처리 | `force_korean()` → `korean_quality_check()` |
| 응답 형식 | `jsonify({...})` |
| ID 생성 | `f"meet-{uuid.uuid4().hex[:8]}"` |
| 임시 파일 | `tmp/meeting/{job_id}/` → 완료 후 삭제 |

---

## 10. Implementation Order

### Phase 1: Core (필수)
1. [ ] `core/meeting_stt.py` — faster-whisper 래퍼 (싱글톤, transcribe)
2. [ ] `core/meeting_pipeline.py` — 파이프라인 오케스트레이터 (download → STT → LLM → callback)
3. [ ] `routes/meeting.py` — Blueprint 6 API + init_meeting + DB 초기화
4. [ ] `app.py` — meeting Blueprint 등록 + init 호출

### Phase 2: 확장 (선택)
5. [ ] `core/meeting_vision.py` — 프레임 추출 + Vision 슬라이드 분석
6. [ ] 청크 분할 처리 (30분 단위)
7. [ ] 문서 업데이트 (CLAUDE.md, API_REFERENCE.md 등)

### Phase 3: 연동
8. [ ] hospital-jitsi 전용 API 키 발급
9. [ ] 연동 테스트 (E2E)
10. [ ] 서버 배포

---

## 11. app.py 등록 패턴

```python
# app.py에 추가
from routes.meeting import meeting_bp, init_meeting

app.register_blueprint(meeting_bp)

init_meeting(
    base_dir=BASE_DIR,
    ollama_session=ollama_session,
    ollama_base=OLLAMA_BASE,
)
```

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-06-02 | Initial draft | AI_Server |
