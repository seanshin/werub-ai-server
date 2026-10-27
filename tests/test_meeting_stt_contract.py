"""회의 STT 백엔드 계약 정합 회귀 — edu 제보(imac-worker 'unknown' 산발 실패) 봉합분.

근본원인: iMac 워커(mlx-whisper)는 `text`, 로컬 faster-whisper 래퍼는 `full_text`로
반환하는데 파이프라인이 `full_text`만 인정 → 위임 성공 응답이 실패로 처리됐다.
client.call은 예외/available:false만 실패로 보므로 dispatch 폴백도 발동하지 않았다.
"""
import pytest

from core.meeting_pipeline import _stt_usable, normalize_stt_result


def test_worker_text_key_normalized_to_full_text():
    """워커 응답(text)이 파이프라인 계약(full_text)으로 흡수된다."""
    res = normalize_stt_result({
        "available": True, "text": "안녕하세요", "engine": "imac-worker",
        "segments": [{"start": 0.0, "end": 1.0, "text": "안녕하세요"}],
    })
    assert res["full_text"] == "안녕하세요"
    assert _stt_usable(res)


def test_local_full_text_unchanged():
    """기존 로컬 응답은 그대로 통과(동작 불변)."""
    res = normalize_stt_result({"available": True, "full_text": "회의 시작",
                                "segments": [], "engine": "gpu-local"})
    assert res["full_text"] == "회의 시작"
    assert _stt_usable(res)


def test_missing_segments_defaults_to_list():
    res = normalize_stt_result({"available": True, "text": "x"})
    assert res["segments"] == []


@pytest.mark.parametrize("bad", [None, "err", 42, []])
def test_non_dict_result_is_unusable(bad):
    res = normalize_stt_result(bad)
    assert not _stt_usable(res)
    assert res["error"]


def test_error_shaped_remote_result_is_unusable():
    """200 + 에러 페이로드(폴백 미발동 케이스) → 사용 불가로 판정되어야 로컬 재시도가 걸린다."""
    res = normalize_stt_result({"available": True, "error": "gpu busy",
                                "engine": "imac-worker"})
    assert not _stt_usable(res)


def test_available_false_is_unusable():
    res = normalize_stt_result({"available": False, "error": "mlx-whisper not installed"})
    assert not _stt_usable(res)


def test_empty_transcript_still_usable():
    """무음 녹화 = 빈 전사도 정상 결과(기존 동작 유지·실패로 뒤집지 않는다)."""
    res = normalize_stt_result({"available": True, "text": "", "segments": []})
    assert _stt_usable(res)


def test_initial_prompt_is_capped_and_optional():
    """용어 힌트는 길이 상한으로 잘리고, 미지정 시 None(기존 동작)."""
    from core.meeting_stt import MAX_INITIAL_PROMPT_CHARS

    long_prompt = "요로감염 " * 500
    capped = (long_prompt or "").strip()[:MAX_INITIAL_PROMPT_CHARS] or None
    assert capped is not None and len(capped) == MAX_INITIAL_PROMPT_CHARS
    assert (("" or "").strip()[:MAX_INITIAL_PROMPT_CHARS] or None) is None


# ---------------------------------------------------------------------------
# edu 2차 요청 — 화자 병합 / 용어집 hotwords 경로
# ---------------------------------------------------------------------------

def test_speaker_merged_into_transcript_segments():
    """화자분리 결과가 transcript.segments[].speaker에도 채워진다(종전엔 별도 배열만)."""
    from core.meeting_pipeline import merge_speakers_into_segments

    segments = [{"start": 0.0, "end": 3.0, "text": "강의를 시작합니다"},
                {"start": 3.0, "end": 6.0, "text": "질문 있습니다"}]
    speaker_segments = [{"start": 0.0, "end": 3.0, "text": "강의를 시작합니다", "speaker": "SPEAKER_01"},
                        {"start": 3.0, "end": 6.0, "text": "질문 있습니다", "speaker": "SPEAKER_00"}]
    assert merge_speakers_into_segments(segments, speaker_segments) is True
    assert [s["speaker"] for s in segments] == ["SPEAKER_01", "SPEAKER_00"]


def test_speaker_merge_skipped_on_length_mismatch():
    """길이가 어긋나면 틀린 화자를 붙이지 않고 병합을 건너뛴다."""
    from core.meeting_pipeline import merge_speakers_into_segments

    segments = [{"start": 0.0, "end": 3.0, "text": "a"}, {"start": 3.0, "end": 6.0, "text": "b"}]
    assert merge_speakers_into_segments(segments, [{"speaker": "SPEAKER_00"}]) is False
    assert merge_speakers_into_segments(segments, []) is False
    assert all("speaker" not in s for s in segments)


def test_transcribe_accepts_hotwords_kwarg():
    """용어집은 initial_prompt가 아니라 hotwords로 전달된다(용어 교정력)."""
    import inspect

    from core.meeting_stt import transcribe

    params = inspect.signature(transcribe).parameters
    assert "hotwords" in params and "initial_prompt" in params


def test_queue_wait_absorbs_burst():
    """버스트 실측(1시간 강의 ≈ 4분·동시 2)에서 30건 꼬리까지 대기 상한 안에 들어온다."""
    from core.meeting_pipeline import MAX_CONCURRENT, QUEUE_WAIT_S

    worst_case_s = 30 * 4 * 60 / MAX_CONCURRENT
    assert QUEUE_WAIT_S >= worst_case_s


def test_queue_admission_is_backlog_based_not_concurrency():
    """버스트 접수는 동시 상한이 아니라 대기열 상한으로 판정된다(503 오거절 봉합)."""
    from core.meeting_pipeline import MAX_CONCURRENT, MAX_QUEUE

    assert MAX_QUEUE > MAX_CONCURRENT, "대기열 상한이 동시 상한과 같으면 버스트가 접수에서 튕긴다"
    assert MAX_QUEUE >= 20, "집합교육 일괄 업로드(20~30건) 흡수"


# ---------------------------------------------------------------------------
# 실시간(VoiceEMR) ↔ 배치 VRAM 경합 완화 — 진행 중인 진료는 끊지 않는다
# ---------------------------------------------------------------------------

def test_realtime_vram_not_freed_while_consultation_active(monkeypatch):
    from core import meeting_pipeline, stt_realtime

    calls = []
    monkeypatch.setattr(stt_realtime, "status",
                        lambda: {"loaded": True, "active_sessions": 1})
    monkeypatch.setattr(stt_realtime, "idle_seconds", lambda: 99999)
    monkeypatch.setattr(stt_realtime, "unload", lambda: calls.append("unload") or True)

    assert meeting_pipeline._free_realtime_stt_vram() is False
    assert calls == [], "활성 세션(진료 중)에는 실시간 모델을 반납하면 안 된다"


def test_realtime_vram_not_freed_when_recently_used(monkeypatch):
    from core import meeting_pipeline, stt_realtime

    monkeypatch.setattr(stt_realtime, "status", lambda: {"loaded": True, "active_sessions": 0})
    monkeypatch.setattr(stt_realtime, "idle_seconds", lambda: 5)
    monkeypatch.setattr(stt_realtime, "unload", lambda: True)
    assert meeting_pipeline._free_realtime_stt_vram() is False


def test_realtime_vram_freed_when_idle(monkeypatch):
    from core import meeting_pipeline, stt_realtime

    monkeypatch.setattr(stt_realtime, "status", lambda: {"loaded": True, "active_sessions": 0})
    monkeypatch.setattr(stt_realtime, "idle_seconds", lambda: 99999)
    monkeypatch.setattr(stt_realtime, "unload", lambda: True)
    assert meeting_pipeline._free_realtime_stt_vram() is True


def test_realtime_vram_free_is_noop_when_not_loaded(monkeypatch):
    from core import meeting_pipeline, stt_realtime

    monkeypatch.setattr(stt_realtime, "status", lambda: {"loaded": False, "active_sessions": 0})
    assert meeting_pipeline._free_realtime_stt_vram() is False


# ---------------------------------------------------------------------------
# 옵션 → STT 인자 배선 (edu ③ 수정의 핵심 — 종전엔 시그니처만 검증됐다)
# ---------------------------------------------------------------------------

def test_stt_options_routes_vocabulary_to_hotwords():
    from core.meeting_pipeline import stt_options

    lang, prompt, hot, rep = stt_options({"vocabulary": ["요로감염", "재원일수"]})
    assert lang == "ko"
    assert rep == {"sent": 2, "used": 2, "dropped": 0, "max_tokens": 96}, "안 잘렸으면 사유 없음"
    assert hot == "요로감염, 재원일수", "용어집은 hotwords로 가야 한다(initial_prompt 아님)"
    assert prompt is None, "용어집이 문맥 프롬프트로 새면 개별 용어 교정력이 떨어진다"


def test_stt_options_accepts_csv_and_hotwords_alias():
    from core.meeting_pipeline import stt_options

    assert stt_options({"vocabulary": "가, 나"})[2] == "가, 나"
    assert stt_options({"hotwords": ["다"]})[2] == "다"
    # vocabulary 우선(둘 다 오면)
    assert stt_options({"vocabulary": ["가"], "hotwords": ["나"]})[2] == "가"


def test_stt_options_keeps_prompt_separate_and_defaults():
    from core.meeting_pipeline import stt_options

    lang, prompt, hot, rep = stt_options({"language": "en", "initial_prompt": "임상 강의입니다."})
    assert (lang, prompt, hot, rep) == ("en", "임상 강의입니다.", None, None)
    assert stt_options({}) == ("ko", None, None, None)
    assert stt_options(None) == ("ko", None, None, None)


def test_stt_options_blank_values_are_none():
    """빈 문자열·공백 목록이 whisper에 빈 인자로 흘러가지 않게 한다."""
    from core.meeting_pipeline import stt_options

    assert stt_options({"vocabulary": ["", "  "], "initial_prompt": "   "})[1:] == (None, None, None)


# ---------------------------------------------------------------------------
# 멀티테넌트 접수 공정성 (edu: 병원이 늘면 이게 먼저 걸린다)
# ---------------------------------------------------------------------------

def _pipeline(tmp_path):
    import sqlite3

    from core.meeting_pipeline import MeetingPipeline

    db = str(tmp_path / "m.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE meeting_jobs (id TEXT PRIMARY KEY, status TEXT,
                    file_url TEXT, callback_url TEXT, options TEXT, metadata TEXT,
                    submitted_by TEXT, created_at TEXT)""")
    conn.commit()
    conn.close()
    return MeetingPipeline(db_path=db, ollama_base="http://x", ollama_session=None,
                           tmp_dir=str(tmp_path / "tmp"))


def _fill(p, key, n, status="pending"):
    import sqlite3

    conn = sqlite3.connect(p._db_path)
    conn.executemany("INSERT INTO meeting_jobs (id,status,file_url,submitted_by) VALUES (?,?,?,?)",
                     [(f"{key}-{i}", status, "u", key) for i in range(n)])
    conn.commit()
    conn.close()


def test_one_caller_cannot_fill_the_whole_queue(tmp_path):
    from core.meeting_pipeline import MAX_QUEUE_PER_KEY

    p = _pipeline(tmp_path)
    _fill(p, "hospital-A", MAX_QUEUE_PER_KEY)
    ok, reason = p.can_accept("hospital-A")
    assert not ok and "per-caller" in reason


def test_other_caller_still_accepted_when_first_is_capped(tmp_path):
    """A가 자기 상한을 채워도 B는 받아야 한다 — 이게 없으면 한 병원이 전체를 막는다."""
    from core.meeting_pipeline import MAX_QUEUE_PER_KEY

    p = _pipeline(tmp_path)
    _fill(p, "hospital-A", MAX_QUEUE_PER_KEY)
    ok, _ = p.can_accept("hospital-B")
    assert ok


def test_global_cap_still_applies(tmp_path):
    from core.meeting_pipeline import MAX_QUEUE

    p = _pipeline(tmp_path)
    for i in range(MAX_QUEUE):
        _fill(p, f"key-{i}", 1)
    ok, reason = p.can_accept("key-new")
    assert not ok and "queue full" in reason


def test_completed_jobs_do_not_count(tmp_path):
    p = _pipeline(tmp_path)
    _fill(p, "hospital-A", 40, status="completed")
    ok, _ = p.can_accept("hospital-A")
    assert ok, "완료 잡이 상한을 잡아먹으면 안 된다"


def test_anonymous_caller_only_bound_by_global_cap(tmp_path):
    """키 없이(로컬 등) 들어온 요청은 키당 상한 판정 대상이 아니다."""
    p = _pipeline(tmp_path)
    _fill(p, "hospital-A", 5)
    ok, _ = p.can_accept(None)
    assert ok


def test_batched_and_compute_are_config_toggles(monkeypatch):
    """처리량 레버는 재배포 없이 켜고 끌 수 있어야 한다(systemd 드롭인은 sudo 필요)."""
    from core import meeting_stt as ms

    monkeypatch.setattr(ms, "_cfg", lambda k, d=None: {"meeting_stt_batched": True,
                                                       "meeting_stt_batch_size": 4,
                                                       "whisper_compute": "int8_float16"}.get(k, d))
    assert ms.stt_batched() is True
    assert ms.stt_batch_size() == 4
    assert ms._resolve_device_compute()[1] == "int8_float16"


def test_config_absent_falls_back_to_previous_behaviour(monkeypatch):
    from core import meeting_stt as ms

    monkeypatch.setattr(ms, "_cfg", lambda k, d=None: None)
    monkeypatch.delenv("MEETING_STT_BATCHED", raising=False)
    assert ms.stt_batched() is False, "미설정 시 기존 동작(배치 off)"


def test_sana_vram_release_is_non_blocking(monkeypatch):
    """SANA 반납 실패가 배치 STT를 막으면 안 된다(완화 장치)."""
    import core.meeting_pipeline as mp

    monkeypatch.setattr("requests.post", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert mp._free_sana_vram() is False
