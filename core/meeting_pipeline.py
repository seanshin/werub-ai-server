"""Meeting Analysis Pipeline — orchestrates download → STT → LLM → callback.

Runs in worker threads with semaphore-based concurrency control (max 2).
"""

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

# ---------------------------------------------------------------------------
# 설계 메모 (배치 STT 파이프라인)
#
# - VRAM 은 Ollama 만 쓰는 게 아니다. 상주 LLM·실시간 STT·SANA 사이드카가 같은 16GB 를
#   나눠 쓴다. 배치 시작 전 **유휴 점유자에게 반납을 요청**하되, **진행 중인 진료는 절대
#   끊지 않는다**(활성 세션·최근 발화면 반납하지 않고 배치를 실패시켜 대기열이 다시 태운다).
# - 백엔드마다 응답 키가 다르다(iMac 워커=text / 로컬 래퍼=full_text). 한 곳
#   (normalize_stt_result)에서 흡수하지 않으면 **위임 경로가 조용히 100% 실패**한다.
#   오케스트레이터 폴백은 예외/available:false 만 실패로 보기 때문에 200+이형 응답을 못 잡는다.
# - 접수 상한은 **동시 처리 수가 아니라 대기열 기준**이다. 동시 상한으로 접수를 거절하면
#   일괄 업로드가 통째로 튕긴다. 또 전체 상한만 두면 한 테넌트가 큐를 독점하므로
#   **키당 상한**을 함께 본다.
#
# 상세: docs/DEVELOPER_GUIDE.md §1-8, §2
# ---------------------------------------------------------------------------
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

import requests

from core import model_router
from core.model_router import OllamaAdapter
from core.korean import force_korean, korean_quality_check
from core.meeting_stt import extract_audio, transcribe, get_audio_duration
from core.meeting_diarize import diarize, merge_stt_diarization, is_available as diarize_available
from core.meeting_vision import extract_keyframes, analyze_frames

logger = logging.getLogger("ollama-admin")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONCURRENT = int(os.environ.get("MEETING_MAX_CONCURRENT", "2"))
# 동시 상한 초과분은 '실패'가 아니라 '대기'가 맞다. 종전 5초 후 실패는 제출이 몰릴 때
# (집합교육 일괄 업로드 등) 정상 잡을 queue 단계에서 떨어뜨렸다.
# 기본 4시간: 버스트 실측 기준(1시간 강의 ≈ 3~5분 처리, 동시 2) 20~30건 일괄 업로드의
# 꼬리 잡까지 흡수한다(20건×4분/2 = 40분·30건이면 60분). 30분 기본값은 꼬리가 잘렸다.
QUEUE_WAIT_S = int(os.environ.get("MEETING_QUEUE_WAIT_S", "14400"))
# 접수 상한(대기+진행). 무한 적체 방지용 안전판이며 동시 처리 수와는 별개 —
# 버스트 20~30건은 그대로 받고 순차 처리한다.
MAX_QUEUE = int(os.environ.get("MEETING_MAX_QUEUE", "50"))
# 호출자(키)당 접수 상한 — 전체 상한만 두면 **한 병원의 일괄 업로드가 큐를 채워 다른 병원을
# 막는다**(edu 제보: 멀티테넌트에서 이게 먼저 걸린다). 전체 50 안에서 1개 키는 최대 30까지.
MAX_QUEUE_PER_KEY = int(os.environ.get("MEETING_MAX_QUEUE_PER_KEY", "30"))
# 실시간 STT(VoiceEMR) 상주 모델 반납 기준 — 배치와 VRAM을 공유하기 때문.
# 평상시엔 넉넉히(5분 유휴), OOM으로 몰린 뒤에는 좀 더 공격적으로(1분) 반납한다.
_RT_IDLE_S = int(os.environ.get("MEETING_RT_IDLE_S", "300"))
_RT_IDLE_FORCE_S = int(os.environ.get("MEETING_RT_IDLE_FORCE_S", "60"))
CALLBACK_MAX_RETRIES = 3
DOWNLOAD_TIMEOUT = 600  # 10분
DOWNLOAD_MAX_MB = 4096  # 회의 녹화 최대 4GB (장시간 영상 허용·악성 무한스트림 디스크 필 방지)
LLM_MODEL = "qwen3:8b"


# ---------------------------------------------------------------------------
# STT 결과 정합 (순수 함수 — 백엔드별 응답 형태 차이를 한 곳에서 흡수)
# ---------------------------------------------------------------------------

def normalize_stt_result(res):
    """백엔드 응답 → {available, full_text, segments, engine, ...} 표준형.

    iMac 워커(mlx-whisper)는 `text`, 로컬 faster-whisper 래퍼는 `full_text`로 반환한다.
    이 키 불일치 때문에 위임 성공 응답이 파이프라인에서 실패로 처리됐다(에러 문자열
    'unknown'). 여기서 흡수해 두 백엔드의 계약을 동일하게 만든다.
    """
    if not isinstance(res, dict):
        return {"available": False, "error": "invalid stt result", "segments": []}
    out = dict(res)
    if "full_text" not in out and isinstance(out.get("text"), str):
        out["full_text"] = out["text"]
    if not isinstance(out.get("segments"), list):
        out["segments"] = []
    return out


def _stt_usable(res):
    """전사 결과를 다음 단계로 넘길 수 있는지."""
    return bool(res.get("available")) and isinstance(res.get("full_text"), str)


def _stt_local_only(lang, prompt, hotwords) -> bool:
    """STT를 iMac 위임 없이 로컬 faster-whisper로 강제할 조건.

    prompt/hotwords: 워커 mlx-whisper 미수용 — 위임하면 조용히 사라짐(기존 처치).
    language: X7에서 hints 미전송으로 인한 무성 ko 낙착 때문에 비-ko를 로컬 강제했다.
    v2.125.24에서 client.call의 hints 전송 구조는 복구됐으나(language가 워커에 실제로
    도달), **mlx-whisper의 비-ko 전사 품질이 GPU faster-whisper와 동등한지 미검증**(X10
    STT WER은 ko만 잰다) → 효과 미확인 처치 금지 규율상 위임 허용은 **X10 비-ko 확장
    측정 후**로 보류하고, 지금은 로컬 강제를 유지한다(구조 복구와 정책 완화를 분리)."""
    return bool(prompt or hotwords) or lang != "ko"


def stt_options(options):
    """요청 options → (language, initial_prompt, hotwords, hint_report) 정규화.

    용어집(`vocabulary`/`hotwords`)은 **hotwords**로, 문맥 프롬프트는 `initial_prompt`로
    분리해 보낸다 — edu 실사용에서 용어집을 initial_prompt로 넣었더니 개별 용어 교정이
    안 됐던 원인이다(v2.124.42). 배열은 쉼표로 합쳐 whisper 인자 형태로 만든다.

    🔴 힌트는 **토큰 기준으로 캡**한다(v2.124.63) — 종전 600자 캡은 whisper 프롬프트
    창(224토큰)보다 커서, 힌트가 많으면 전사가 **조용히 70%까지 잘리거나** 실패했다
    (edu 실측). 잘린 개수는 `hint_report`로 호출자에게 돌려준다.
    """
    from core.stt_hints import cap_hints, hint_report
    options = options or {}

    def _join(v):
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x).strip() for x in v if str(x).strip()) or None
        v = (v or "").strip() if isinstance(v, str) else v
        return v or None

    raw_hot = _join(options.get("vocabulary") or options.get("hotwords"))
    sent = len([p for p in (raw_hot or "").split(",") if p.strip()])
    hot, used, dropped = cap_hints(raw_hot)
    # "auto"/"" → None(자동 감지·X7). 무효 값은 그대로 통과 — 접수 검증(400) 이전에
    # 저장된 구 잡은 종전대로 transcribe에서 크게 실패한다(조용한 ko 저하 금지).
    from core.meeting_stt import normalize_language
    lang, _lang_err = normalize_language(options.get("language"))
    if _lang_err:
        lang = options.get("language")
    return (lang,
            _join(options.get("initial_prompt")),
            hot,
            hint_report(sent, used, dropped) if sent else None)


def _free_realtime_stt_vram(force_idle_s=None):
    """유휴 상태인 실시간 STT 상주 모델을 반납시켜 배치 STT의 VRAM을 확보 → 반납 여부.

    실측(2026-07-28): 상주 LLM + 실시간 medium(1.96GB)이 올라가 있으면 여유가 3.18GB로
    떨어져 배치 large-v3(3.84GB)가 **CUDA OOM**이 난다(07-08 실패 이력과 동일 패턴).
    **진행 중인 진료(활성 세션·최근 발화)는 절대 끊지 않는다** — 유휴일 때만 반납한다.
    """
    idle_gate = _RT_IDLE_S if force_idle_s is None else force_idle_s
    try:
        from core import stt_realtime
        st = stt_realtime.status()
        if not st.get("loaded"):
            return False
        if st.get("active_sessions"):
            return False
        if stt_realtime.idle_seconds() < idle_gate:
            return False
        return stt_realtime.unload()
    except Exception as e:  # noqa: BLE001 — 완화 장치라 실패해도 본 경로를 막지 않는다
        logger.debug("realtime STT VRAM 반납 skip: %s", e)
        return False


def _free_sana_vram():
    """유휴 SANA 사이드카에 VRAM 반납을 요청한다 → 요청 성공 여부.

    2026-07-29 실측: SANA가 `loaded:false`인데도 **2.36GB를 점유**해 여유가 3.1GB로 떨어지고
    배치 STT(large-v3 float16 3.84GB)가 CUDA OOM으로 죽었다. 실시간 STT 반납(_free_realtime_stt_vram)
    과 같은 규율로, 배치 시작 전에 비워달라고 요청한다. 생성 중이면 사이드카가 알아서 거절/무시한다.
    """
    try:
        import requests as _rq
        url = (os.environ.get("SANA_SERVICE_URL") or "http://127.0.0.1:8189").rstrip("/")
        r = _rq.post(url + "/unload", timeout=30)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001 — 완화 장치라 실패해도 본 경로를 막지 않는다
        logger.debug("SANA VRAM 반납 skip: %s", e)
        return False


def merge_speakers_into_segments(segments, speaker_segments):
    """화자분리 결과를 transcript 세그먼트에 채운다 → 병합 여부(bool).

    종전에는 화자가 `speaker_segments`에만 실려 호출자가 두 배열을 조인해야 했다(edu 제보).
    두 배열은 같은 STT 세그먼트에서 파생돼 순서·구간이 일치하므로 인덱스로 대응시키고,
    길이가 어긋나면 **틀린 화자를 붙이느니 병합하지 않는다**.
    """
    if not speaker_segments or len(speaker_segments) != len(segments):
        return False
    for seg, sp in zip(segments, speaker_segments):
        seg["speaker"] = sp.get("speaker")
    return True


SUMMARY_SYSTEM_PROMPT = """당신은 회의록 분석 전문가입니다. 반드시 한국어로 작성하세요.

아래 회의 녹취록을 분석하여 정확히 다음 형식으로 출력하세요.
추측하지 말고 녹취록에 있는 내용만 기반으로 작성하세요.

SUMMARY:
(회의 핵심 내용 3~5줄 요약. 존댓말 사용.)

ACTION_ITEMS:
(후속 조치 목록. 없으면 "없음")
- 담당자: 할 일 (기한)

HIGHLIGHTS:
(중요 발언/결정 사항. 없으면 "없음")
- [시간] 내용"""


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------

class MeetingPipeline:
    def __init__(self, db_path, ollama_base, ollama_session, tmp_dir):
        self._db_path = db_path
        self._ollama_base = ollama_base
        self._session = ollama_session or requests.Session()
        self._tmp_dir = tmp_dir
        self._semaphore = threading.Semaphore(MAX_CONCURRENT)
        os.makedirs(tmp_dir, exist_ok=True)

    # -- DB helpers ----------------------------------------------------------

    def _get_db(self):
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _update_status(self, job_id, status, **extra):
        conn = self._get_db()
        sets = ["status = ?"]
        vals = [status]
        if status == "downloading" and "started_at" not in extra:
            extra["started_at"] = datetime.now(timezone.utc).isoformat()
        if status in ("completed", "failed"):
            extra["completed_at"] = datetime.now(timezone.utc).isoformat()
        for k, v in extra.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(job_id)
        conn.execute(f"UPDATE meeting_jobs SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
        conn.close()

    def _get_job(self, job_id):
        conn = self._get_db()
        row = conn.execute("SELECT * FROM meeting_jobs WHERE id = ?", (job_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    # -- Public API ----------------------------------------------------------

    def can_accept(self, submitted_by=None):
        """신규 접수 가능 여부 → (bool, 사유). **동시 상한이 아니라 대기열 상한**으로 판정.

        종전에는 active >= MAX_CONCURRENT(2)면 제출을 503으로 거절해, 대기열(QUEUE_WAIT_S)을
        두고도 **버스트 업로드가 접수 단계에서 튕겼다**. 또 전체 상한만 있으면 **한 호출자가
        큐를 채워 다른 호출자를 막으므로**(멀티테넌트), 키당 상한을 함께 본다.
        """
        conn = self._get_db()
        active = ("'pending','downloading','transcribing','diarizing',"
                  "'analyzing_vision','analyzing'")
        backlog = conn.execute(
            f"SELECT COUNT(*) FROM meeting_jobs WHERE status IN ({active})").fetchone()[0]
        mine = 0
        if submitted_by:
            mine = conn.execute(
                f"SELECT COUNT(*) FROM meeting_jobs WHERE status IN ({active}) "
                "AND submitted_by = ?", (submitted_by,)).fetchone()[0]
        conn.close()
        if backlog >= MAX_QUEUE:
            return False, f"queue full ({backlog}/{MAX_QUEUE})"
        if submitted_by and mine >= MAX_QUEUE_PER_KEY:
            return False, f"per-caller queue limit ({mine}/{MAX_QUEUE_PER_KEY})"
        return True, ""

    def backlog(self):
        """대기+진행 중 잡 수(관측용)."""
        conn = self._get_db()
        n = conn.execute(
            "SELECT COUNT(*) FROM meeting_jobs "
            "WHERE status IN ('pending','downloading','transcribing','diarizing',"
            "'analyzing_vision','analyzing')"
        ).fetchone()[0]
        conn.close()
        return n

    def submit(self, file_url, callback_url=None, options=None, metadata=None,
               submitted_by=None):
        """Submit analysis job → returns job_id immediately."""
        job_id = f"meet-{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()

        conn = self._get_db()
        conn.execute(
            """INSERT INTO meeting_jobs (id, status, file_url, callback_url, options, metadata,
               submitted_by, created_at) VALUES (?, 'pending', ?, ?, ?, ?, ?, ?)""",
            (job_id, file_url, callback_url,
             json.dumps(options or {}, ensure_ascii=False),
             json.dumps(metadata or {}, ensure_ascii=False),
             submitted_by,
             now),
        )
        conn.commit()
        conn.close()

        # Start worker thread
        t = threading.Thread(target=self._run, args=(job_id,), daemon=True)
        t.start()
        return job_id

    def retry(self, job_id):
        """Retry a failed job."""
        job = self._get_job(job_id)
        if not job or job["status"] != "failed":
            return False
        self._update_status(job_id, "pending", error=None, error_stage=None,
                            started_at=None, completed_at=None)
        t = threading.Thread(target=self._run, args=(job_id,), daemon=True)
        t.start()
        return True

    # -- Pipeline stages -----------------------------------------------------

    def _run(self, job_id):
        """Main pipeline: download → STT → LLM → callback."""
        # 대기 중에는 status가 'pending' 그대로 유지된다(호출자 폴링에 실패로 보이지 않음).
        acquired = self._semaphore.acquire(timeout=QUEUE_WAIT_S)
        if not acquired:
            self._update_status(job_id, "failed",
                                error=f"Concurrency limit reached (waited {QUEUE_WAIT_S}s)",
                                error_stage="queue")
            return

        stage = "init"
        t0 = time.time()
        job_dir = os.path.join(self._tmp_dir, job_id)
        os.makedirs(job_dir, exist_ok=True)

        try:
            job = self._get_job(job_id)
            if not job:
                return
            options = json.loads(job["options"] or "{}")
            metadata = json.loads(job["metadata"] or "{}")

            # Stage 1: Download MP4
            stage = "download"
            self._update_status(job_id, "downloading")
            mp4_path = self._download_mp4(job["file_url"], job_dir)

            # Stage 2: Extract audio + STT
            stage = "transcribe"
            self._update_status(job_id, "transcribing")
            stt_t0 = time.time()
            wav_path = extract_audio(mp4_path, job_dir)
            lang, prompt, hotwords, hint_rep = stt_options(options)
            if hint_rep:
                self._update_status(job_id, "transcribing",
                                    hint_report=json.dumps(hint_rep, ensure_ascii=False))
                if hint_rep.get("dropped"):
                    logger.warning("용어 힌트 %d개 중 %d개만 사용(프롬프트 창 초과) job=%s",
                                   hint_rep["sent"], hint_rep["used"], job_id)
            # Node Orchestrator 위임 (node-orchestrator-p2-stt) — 워커가 stt를 advertise하고
            # delegate에 포함될 때만 iMac 위임, 그 전엔 로컬 faster-whisper(동작 100% 동일).
            _free_realtime_stt_vram()   # 유휴 실시간 모델 반납(VRAM 경합 예방)
            _free_sana_vram()           # 이미지 사이드카 VRAM 반납(2026-07-29 OOM 원인)

            def _local_stt():
                try:
                    ft, segs = transcribe(wav_path, language=lang, initial_prompt=prompt,
                                          hotwords=hotwords)
                except RuntimeError as e:
                    if "out of memory" not in str(e).lower():
                        raise
                    # 경합 실측: 실시간 medium 상주(1.96GB) + 배치 large-v3(3.84GB) > 여유.
                    # 진료가 진행 중이 아니면 반납받아 1회 재시도, 진행 중이면 그대로 실패시켜
                    # 라이브 진료를 끊지 않는다(대기열이 이 잡을 다시 태운다).
                    if not _free_realtime_stt_vram(force_idle_s=_RT_IDLE_FORCE_S):
                        raise
                    logger.warning("배치 STT OOM → 실시간 모델 반납 후 1회 재시도")
                    ft, segs = transcribe(wav_path, language=lang, initial_prompt=prompt,
                                          hotwords=hotwords)
                return {"available": True, "full_text": ft, "segments": segs,
                        "engine": "gpu-local"}
            from core.orchestrator import dispatch as _dispatch
            if _stt_local_only(lang, prompt, hotwords):
                # 용어 힌트는 로컬 faster-whisper만 지원(워커 mlx-whisper는 프롬프트 미수용).
                # 위임하면 힌트가 조용히 사라지므로, 힌트가 있으면 결정적으로 로컬에서 처리한다.
                # language도 같은 클래스(X7): 위임 클라이언트가 hints를 싣지 않아 워커는
                # form 기본 'ko'로 낙착 — ko 외(자동 감지 None 포함)는 로컬 강제.
                _stt = normalize_stt_result(_local_stt())
            else:
                _stt = normalize_stt_result(
                    _dispatch.run("stt", wav_path, local_fn=_local_stt,
                                  hints={"language": lang}))
            if not _stt_usable(_stt) and _stt.get("engine") != "gpu-local":
                # 원격이 200 + 에러/이형 페이로드로 응답하면 dispatch의 폴백이 발동하지 않는다
                # (client는 예외·available:false만 실패로 본다) → 여기서 로컬 1회 재시도.
                logger.warning("STT 원격(%s) 결과 사용 불가(%s) → 로컬 재시도",
                               _stt.get("engine"), str(_stt.get("error"))[:80])
                _stt = normalize_stt_result(_local_stt())
            if not _stt_usable(_stt):
                raise RuntimeError(f"STT failed ({_stt.get('engine')}): {_stt.get('error', 'unknown')}")
            full_text, segments = _stt["full_text"], _stt["segments"]
            stt_ms = int((time.time() - stt_t0) * 1000)

            # Stage 2-b: Speaker diarization (optional)
            speaker_segments = []
            diarize_ms = 0
            if options.get("diarize") and diarize_available():
                stage = "diarize"
                self._update_status(job_id, "diarizing")
                diar_t0 = time.time()
                diar_segs = diarize(
                    wav_path,
                    num_speakers=options.get("num_speakers"),
                    max_speakers=options.get("max_speakers", 10),
                )
                speaker_segments = merge_stt_diarization(segments, diar_segs)
                if not merge_speakers_into_segments(segments, speaker_segments):
                    logger.warning("speaker merge skipped: %d segs vs %d speaker segs",
                                   len(segments), len(speaker_segments))
                diarize_ms = int((time.time() - diar_t0) * 1000)
                logger.info(f"Diarization done: {len(set(s['speaker'] for s in speaker_segments))} speakers ({diarize_ms}ms)")

            # Stage 2-c: Vision keyframe analysis (optional)
            slides = []
            vision_ms = 0
            if options.get("vision"):
                stage = "vision"
                self._update_status(job_id, "analyzing_vision")
                vis_t0 = time.time()
                frames = extract_keyframes(
                    mp4_path, job_dir,
                    interval_sec=options.get("keyframe_interval", 30),
                )
                slides = analyze_frames(
                    frames,
                    ollama_session=self._session,
                    ollama_base=self._ollama_base,
                )
                vision_ms = int((time.time() - vis_t0) * 1000)
                logger.info(f"Vision done: {len(slides)} slides ({vision_ms}ms)")

            # Stage 3: LLM Analysis (화자 정보 + 슬라이드 컨텍스트 포함)
            stage = "analyze"
            self._update_status(job_id, "analyzing")
            llm_t0 = time.time()
            summary, action_items, highlights = self._llm_analyze(
                full_text, segments, metadata,
                speaker_segments=speaker_segments,
                slides=slides,
            )
            llm_ms = int((time.time() - llm_t0) * 1000)

            # Korean post-processing
            summary = force_korean(summary)
            action_items = self._postprocess_action_items(action_items)
            highlights = self._postprocess_highlights(highlights)

            # Quality check
            _valid, korean_ratio, _st = korean_quality_check(summary)
            quality = {"korean_ratio": round(korean_ratio, 3), "risk_band": "low" if korean_ratio > 0.8 else "medium"}

            speakers_detected = len(set(s["speaker"] for s in speaker_segments)) if speaker_segments else 0

            total_ms = int((time.time() - t0) * 1000)

            # Save results
            self._update_status(
                job_id, "completed",
                transcript=full_text,
                segments=json.dumps(segments, ensure_ascii=False),
                speaker_segments=json.dumps(speaker_segments, ensure_ascii=False) if speaker_segments else None,
                speakers_detected=speakers_detected,
                slides=json.dumps(slides, ensure_ascii=False) if slides else None,
                summary=summary,
                action_items=json.dumps(action_items, ensure_ascii=False),
                highlights=json.dumps(highlights, ensure_ascii=False),
                korean_quality=json.dumps(quality, ensure_ascii=False),
                stt_duration_ms=stt_ms,
                diarize_duration_ms=diarize_ms,
                vision_duration_ms=vision_ms,
                llm_duration_ms=llm_ms,
                total_duration_ms=total_ms,
            )

            # Stage 4: Callback
            stage = "callback"
            self._send_callback(job_id)

            logger.info(f"Meeting analysis complete: {job_id} ({total_ms}ms)")

        except Exception as e:
            logger.error(f"Meeting pipeline failed at {stage}: {e}", exc_info=True)
            self._update_status(job_id, "failed", error=str(e)[:500], error_stage=stage)
        finally:
            self._semaphore.release()
            self._cleanup(job_dir)

    def _download_mp4(self, url, job_dir):
        """Download MP4 from S3 Presigned URL or copy local file."""
        mp4_path = os.path.join(job_dir, "recording.mp4")

        # 로컬 파일 경로 지원 (테스트/내부 용도) — 기본 차단(외부 노출 엔드포인트의 임의 파일 read 방지).
        # 내부 테스트 시에만 MEETING_ALLOW_LOCAL_FILE=1 로 명시 허용. 라우트 검증과 이중 방어.
        if url.startswith("/") or url.startswith("file://"):
            if os.environ.get("MEETING_ALLOW_LOCAL_FILE") != "1":
                raise ValueError("Local file path not allowed (set MEETING_ALLOW_LOCAL_FILE=1 for internal use only)")
            local_path = url.replace("file://", "")
            if not os.path.isfile(local_path):
                raise FileNotFoundError(f"Local file not found: {local_path}")
            import shutil
            shutil.copy2(local_path, mp4_path)
            size = os.path.getsize(mp4_path)
            logger.info(f"Copied local {size / 1024 / 1024:.1f}MB → {mp4_path}")
            return mp4_path

        # SSRF 2차 방어: 도메인이 사설/메타데이터 IP로 해석되는 경우 차단(라우트는 IP리터럴만 검사).
        # 백그라운드 워커이므로 블로킹 DNS 안전(요청 gthread 영향 없음).
        from urllib.parse import urlparse as _urlparse
        from core.security import resolve_host_is_public
        _host = _urlparse(url).hostname
        if not resolve_host_is_public(_host):
            raise ValueError(f"Refusing download — host unresolvable or resolves to private/blocked IP: {_host}")

        # SSRF 리다이렉트 우회 차단: 공인 호스트가 302로 사설/메타데이터 IP 우회하는 것 차단
        # (resolve 가드는 최초 호스트만 검사) + 용량 캡(악성 무한 스트림 디스크 필 방지).
        resp = self._session.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=False)
        if resp.is_redirect or 300 <= resp.status_code < 400:
            raise ValueError("redirect not allowed (SSRF 리다이렉트 우회 차단)")
        resp.raise_for_status()

        cap = DOWNLOAD_MAX_MB * 1024 * 1024
        size = 0
        with open(mp4_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                size += len(chunk)
                if size > cap:
                    raise ValueError(f"download exceeds {DOWNLOAD_MAX_MB}MB cap")

        logger.info(f"Downloaded {size / 1024 / 1024:.1f}MB → {mp4_path}")
        return mp4_path

    def _llm_analyze(self, transcript, segments, metadata,
                     speaker_segments=None, slides=None):
        """LLM 분석: 요약 + 액션아이템 + 하이라이트."""
        from core.llm_resilience import get_adaptive_timeout, ollama_breaker

        participants = metadata.get("participants", [])
        participant_str = ", ".join(p.get("name", "") for p in participants) if participants else "참석자 정보 없음"

        # 화자별 녹취록 생성 (화자 분리 결과가 있으면)
        if speaker_segments:
            lines = []
            for seg in speaker_segments[:200]:  # 최대 200개 세그먼트
                lines.append(f"[{seg['speaker']}] {seg['text']}")
            text_for_llm = "\n".join(lines)
        else:
            text_for_llm = transcript

        # 긴 녹취록은 앞뒤 + 중간 샘플링
        if len(text_for_llm) > 8000:
            mid = len(text_for_llm) // 2
            text_for_llm = text_for_llm[:3000] + "\n...(중략)...\n" + text_for_llm[mid-1000:mid+1000] + "\n...(중략)...\n" + text_for_llm[-3000:]

        # 슬라이드 컨텍스트 추가
        slide_context = ""
        if slides:
            slide_lines = []
            for s in slides[:10]:
                ts = s.get("timestamp", 0)
                desc = s.get("description", "")[:200]
                slide_lines.append(f"  [{ts:.0f}초] {desc}")
            slide_context = "\n\n## 화면 공유 내용:\n" + "\n".join(slide_lines)

        user_prompt = f"참석자: {participant_str}\n\n## 녹취록:\n{text_for_llm}{slide_context}"

        if not ollama_breaker.can_execute():
            raise RuntimeError("Ollama temporarily unavailable (circuit open)")

        timeout = get_adaptive_timeout(LLM_MODEL, "meeting_analysis", default=180)
        # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_core_app.py)
        # 종전 계약: 전송 예외·HTTP 오류(raise_for_status) 모두 예외로 위로 → 파이프라인 failed.
        res = model_router.chat(
            "meeting.summarize",
            [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=LLM_MODEL, timeout=timeout,
            adapter=OllamaAdapter(base=self._ollama_base, session=self._session))
        if not res.ok:
            raise RuntimeError(res.error or "LLM request failed")
        ollama_breaker.record_success()
        raw = res.raw.get("message", {}).get("content", "")

        return self._parse_llm_response(raw)

    def _parse_llm_response(self, raw):
        """Parse LLM structured response into summary, action_items, highlights."""
        # Remove thinking tags if present (qwen3 thinking mode)
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

        summary = ""
        action_items = []
        highlights = []

        # Split by sections
        parts = re.split(r'\n(?=SUMMARY:|ACTION_ITEMS:|HIGHLIGHTS:)', raw)
        for part in parts:
            part = part.strip()
            if part.startswith("SUMMARY:"):
                summary = part[len("SUMMARY:"):].strip()
            elif part.startswith("ACTION_ITEMS:"):
                body = part[len("ACTION_ITEMS:"):].strip()
                if body != "없음":
                    for line in body.split("\n"):
                        line = line.strip().lstrip("- ")
                        if not line:
                            continue
                        # "담당자: 할 일 (기한)" 파싱
                        m = re.match(r'(.+?):\s*(.+?)(?:\s*\((.+?)\))?$', line)
                        if m:
                            action_items.append({
                                "assignee": m.group(1).strip(),
                                "task": m.group(2).strip(),
                                "deadline": m.group(3).strip() if m.group(3) else "",
                            })
                        else:
                            action_items.append({"assignee": "", "task": line, "deadline": ""})
            elif part.startswith("HIGHLIGHTS:"):
                body = part[len("HIGHLIGHTS:"):].strip()
                if body != "없음":
                    for line in body.split("\n"):
                        line = line.strip().lstrip("- ")
                        if not line:
                            continue
                        m = re.match(r'\[(.+?)\]\s*(.+)', line)
                        if m:
                            highlights.append({"time": m.group(1), "text": m.group(2), "importance": "high"})
                        else:
                            highlights.append({"time": "", "text": line, "importance": "medium"})

        # Fallback: 구조 파싱 실패 시 전체를 summary로
        if not summary:
            summary = raw[:2000] if raw else "(분석 결과 없음)"

        return summary, action_items, highlights

    def _postprocess_action_items(self, items):
        """Apply force_korean to action item text fields."""
        for item in items:
            if item.get("task"):
                item["task"] = force_korean(item["task"])
        return items

    def _postprocess_highlights(self, items):
        """Apply force_korean to highlight text fields."""
        for item in items:
            if item.get("text"):
                item["text"] = force_korean(item["text"])
        return items

    def _send_callback(self, job_id):
        """Send results to hospital-jitsi callback URL."""
        job = self._get_job(job_id)
        if not job or not job.get("callback_url"):
            return

        metadata = json.loads(job["metadata"] or "{}")
        payload = {
            "job_id": job_id,
            "status": job["status"],
            "result": {
                "transcript": {
                    "full_text": job["transcript"] or "",
                    "segments": json.loads(job["segments"] or "[]"),
                    "word_count": len((job["transcript"] or "").split()),
                },
                "hints": json.loads(job["hint_report"]) if job.get("hint_report") else None,
                "speaker_segments": json.loads(job["speaker_segments"] or "[]") if job.get("speaker_segments") else [],
                "speakers_detected": job.get("speakers_detected", 0),
                "slides": json.loads(job["slides"] or "[]") if job.get("slides") else [],
                "summary": job["summary"] or "",
                "action_items": json.loads(job["action_items"] or "[]"),
                "highlights": json.loads(job["highlights"] or "[]"),
                "korean_quality": json.loads(job["korean_quality"] or "{}"),
            },
            "metadata": metadata,
            "pipeline": {
                "stt_model": "faster-whisper/large-v3",
                "llm_model": LLM_MODEL,
                "stt_duration_ms": job["stt_duration_ms"],
                "diarize_duration_ms": job.get("diarize_duration_ms", 0),
                "vision_duration_ms": job.get("vision_duration_ms", 0),
                "llm_duration_ms": job["llm_duration_ms"],
                "total_duration_ms": job["total_duration_ms"],
            },
        }

        conn = self._get_db()
        for attempt in range(CALLBACK_MAX_RETRIES):
            try:
                resp = self._session.post(job["callback_url"], json=payload, timeout=30)
                if resp.status_code < 400:
                    conn.execute(
                        "UPDATE meeting_jobs SET callback_sent=1, callback_attempts=? WHERE id=?",
                        (attempt + 1, job_id),
                    )
                    conn.commit()
                    conn.close()
                    logger.info(f"Callback sent: {job_id} → {job['callback_url']}")
                    return
                logger.warning(f"Callback {attempt+1}: HTTP {resp.status_code}")
            except Exception as e:
                logger.warning(f"Callback {attempt+1} failed: {e}")
            time.sleep(5 ** (attempt + 1))  # 5s, 25s, 125s

        conn.execute(
            "UPDATE meeting_jobs SET callback_sent=0, callback_attempts=?, callback_error=? WHERE id=?",
            (CALLBACK_MAX_RETRIES, "Max retries exceeded", job_id),
        )
        conn.commit()
        conn.close()

    def _cleanup(self, job_dir):
        """Remove temporary files."""
        try:
            if os.path.isdir(job_dir):
                shutil.rmtree(job_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Cleanup failed: {e}")
