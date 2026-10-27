"""core.stt_realtime 순수 단위테스트 — PCM 변환·세션 버퍼(모델/GPU 불요).

전사(transcribe_pcm)는 faster-whisper 모델이 필요해 GPU 라이브에서 검증한다.
여기서는 PCM→배열 변환과 세션 롤링버퍼(누적/슬라이딩/flush/카운트)만 검증.
"""
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import stt_realtime as sr  # noqa: E402


def _pcm(samples):
    """int16 샘플 리스트 → raw 16-bit LE mono PCM bytes."""
    return struct.pack("<%dh" % len(samples), *samples)


def test_pcm_to_array_scaling():
    arr = sr._pcm_to_array(_pcm([0, 32767, -32768, 16384]), sr.SAMPLE_RATE)
    assert arr.dtype == np.float32
    assert len(arr) == 4
    assert abs(arr[0]) < 1e-6
    assert 0.99 < arr[1] <= 1.0          # 32767/32768
    assert -1.0 <= arr[2] < -0.99        # -32768/32768 = -1.0
    assert abs(arr[3] - 0.5) < 1e-3      # 16384/32768


def test_pcm_to_array_empty():
    arr = sr._pcm_to_array(b"", sr.SAMPLE_RATE)
    assert len(arr) == 0


def test_pcm_to_array_resample():
    # 8kHz 100샘플 → 16kHz면 약 2배 길이
    arr = sr._pcm_to_array(_pcm([100] * 100), 8000)
    assert abs(len(arr) - 200) <= 2


def test_session_append_accumulates_and_flushes():
    sr.clear_sessions()
    sid = "sess-A"
    a, _ = sr.session_append(sid, _pcm([1] * 10), final=False)   # (pcm, dropped_s)
    b, _ = sr.session_append(sid, _pcm([2] * 10), final=False)
    assert len(b) > len(a)               # 누적 증가
    assert sr.session_count() == 1
    # final → flush(폐기)
    sr.session_append(sid, _pcm([3] * 10), final=True)
    assert sr.session_count() == 0


def test_session_sliding_cap():
    sr.clear_sessions()
    sid = "sess-B"
    # 상한(30s) 초과 입력 — 누적 버퍼는 상한으로 클램프
    big = _pcm([7] * (sr.SAMPLE_RATE * 40))   # 40초분
    data, dropped = sr.session_append(sid, big, final=False)
    assert len(data) <= sr._SESSION_MAX_BYTES
    assert dropped == 10.0               # 40초 입력 → 10초 유실이 통지된다
    sr.clear_sessions()


def test_session_isolation():
    sr.clear_sessions()
    sr.session_append("s1", _pcm([1] * 5), final=False)
    sr.session_append("s2", _pcm([2] * 5), final=False)
    assert sr.session_count() == 2
    sr.session_append("s1", b"", final=True)
    assert sr.session_count() == 1       # s2만 남음
    sr.clear_sessions()


def test_status_shape():
    st = sr.status()
    assert "available" in st and "model" in st and "active_sessions" in st
    assert st["sample_rate"] == 16000


# ---------------------------------------------------------------------------
# 어휘 바이어싱(hotwords) — HIS VoiceEMR Q3. 순수 로직만(모델 미로드).
# ---------------------------------------------------------------------------

def test_normalize_terms_accepts_csv_and_list():
    from core.stt_realtime import _normalize_terms

    assert _normalize_terms("아스피린, 메트포르민") == "아스피린, 메트포르민"
    assert _normalize_terms(["아스피린", "메트포르민"]) == "아스피린, 메트포르민"
    assert _normalize_terms(None) == ""
    assert _normalize_terms([]) == ""


def test_normalize_terms_caps_length():
    """약품마스터 전량(24k) 주입 시도는 상한에서 잘린다 — 프롬프트 창 보호.

    상한은 문자수가 아니라 **토큰**이다(v2.124.63) — 600자 캡이 창(224토큰)을 넘겨
    전사가 조용히 잘리던 결함(edu 실측) 이후 기준을 바꿨다.
    """
    from core.stt_hints import DEFAULT_MAX_HINT_TOKENS, estimate_tokens
    from core.stt_realtime import _normalize_terms

    out = _normalize_terms(["가나다라마바사" for _ in range(1000)])
    assert out and estimate_tokens(out) <= DEFAULT_MAX_HINT_TOKENS


def test_session_vocab_roundtrip_and_truncation_reported():
    from core import stt_realtime

    info = stt_realtime.set_session_vocab("enc-1", ["아스피린", "와파린"])
    assert info["terms_applied"] == 2 and info["truncated"] is False
    assert stt_realtime.get_session_vocab("enc-1")["hotwords"] == "아스피린, 와파린"

    big = stt_realtime.set_session_vocab("enc-2", ["항생제%03d" % i for i in range(500)])
    assert big["truncated"] is True and big["terms_applied"] < big["terms_requested"]

    # 해제
    stt_realtime.set_session_vocab("enc-1", None)
    assert stt_realtime.get_session_vocab("enc-1") == {}
    stt_realtime.set_session_vocab("enc-2", None)


def test_unknown_session_has_no_vocab():
    from core import stt_realtime

    assert stt_realtime.get_session_vocab("nope") == {}
    assert stt_realtime.get_session_vocab("") == {}


# ---------------------------------------------------------------------------
# HIS Q9/Q11 — 슬라이딩 유실 통지 · vocab 워커 재생성 생존
# ---------------------------------------------------------------------------

def test_session_append_reports_dropped_audio():
    """30초 슬라이딩으로 버린 오디오를 응답으로 통지한다(종전 무성 절단)."""
    from core import stt_realtime as st

    st.clear_sessions()
    sec = st.SAMPLE_RATE * 2
    _, dropped = st.session_append("enc-drop", b"\x00" * (20 * sec), final=False)
    assert dropped == 0.0
    _, dropped = st.session_append("enc-drop", b"\x00" * (15 * sec), final=False)
    assert dropped == 5.0, "35초 누적 → 5초 유실이 보고돼야 한다"
    state = st.session_state("enc-drop")
    assert state["buffered_s"] == 30.0 and state["dropped_s"] == 5.0
    st.session_append("enc-drop", b"", final=True)
    assert st.session_state("enc-drop") == {}


def test_session_state_empty_for_unknown():
    from core import stt_realtime as st

    assert st.session_state("nope") == {}


def test_vocab_survives_process_restart(tmp_path, monkeypatch):
    """워커 재생성(=인메모리 소실) 후에도 저장소에서 복구된다."""
    from core import stt_realtime as st

    monkeypatch.setenv("STT_VOCAB_DIR", str(tmp_path))
    st.set_session_vocab("enc-persist", ["아스피린", "와파린"])
    st._vocab.clear()                      # 워커 재생성 시뮬레이션
    assert st.get_session_vocab("enc-persist")["hotwords"] == "아스피린, 와파린"


def test_vocab_release_also_clears_store(tmp_path, monkeypatch):
    from core import stt_realtime as st

    monkeypatch.setenv("STT_VOCAB_DIR", str(tmp_path))
    st.set_session_vocab("enc-rel", ["아스피린"])
    st.set_session_vocab("enc-rel", None)
    st._vocab.clear()
    assert st.get_session_vocab("enc-rel") == {}


# ---------------------------------------------------------------------------
# 무음/환각 억제 (HIS 2026-07-29 요청) — 프롬프트 에코·정형 환각구·VAD 게이트
# ---------------------------------------------------------------------------

def test_subseq_ratio_detects_skipping_echo():
    """에코는 프롬프트 어휘를 건너뛰며 재조합돼 나온다 — 연속 substring이 아닌
    순서 보존 부분열로 잡혀야 한다(HIS 실측 사례 그대로)."""
    prompt_n = sr._norm_text(sr.STT_INITIAL_PROMPT)
    echo_n = sr._norm_text("진료, 복용, 약국, 예약 등 의료 용어가 사용됩니다.")
    assert echo_n not in prompt_n            # substring 검사로는 못 잡는 형태임을 고정
    assert sr._subseq_ratio(echo_n, prompt_n) >= 0.85


def test_detect_suppression_prompt_echo():
    reason = sr._detect_suppression(
        "진료, 복용, 약국, 예약 등 의료 용어가 사용됩니다.",
        sr.STT_INITIAL_PROMPT, None)
    assert reason == "prompt_echo"


def test_detect_suppression_hotwords_echo():
    hot = "아모잘탄, 트윈스타, 리피토, 크레스토, 자누비아"
    reason = sr._detect_suppression("아모잘탄 트윈스타 리피토 크레스토", None, hot)
    assert reason == "prompt_echo"


def test_detect_suppression_keeps_real_speech():
    """실발화는 조사·동사가 프롬프트에 없어 에코 비율이 낮다 — 절대 지우면 안 된다."""
    real = "혈압이 높아서 약을 처방받으러 왔어요"
    assert sr._detect_suppression(real, sr.STT_INITIAL_PROMPT, None) is None


def test_detect_suppression_protects_short_term_mention():
    """프롬프트에 있는 단일 용어를 실제로 말한 짧은 발화는 길이 게이트로 보호."""
    assert sr._detect_suppression("처방전", sr.STT_INITIAL_PROMPT, None) is None
    assert sr._detect_suppression("네 진료요", sr.STT_INITIAL_PROMPT, None) is None


def test_detect_suppression_hallucination_pattern():
    assert sr._detect_suppression(
        "이 영상은 유료 광고를 포함하고 있습니다.", sr.STT_INITIAL_PROMPT, None
    ) == "hallucination_pattern"
    # 실발화에 섞인 긴 문장은 문구 단독이 아니므로 미차단
    assert sr._detect_suppression(
        "광고 얘기가 아니라 혈압약 복용법을 여쭤보러 왔는데요 어제부터 어지럽습니다",
        sr.STT_INITIAL_PROMPT, None) is None


def test_transcribe_empty_pcm_reports_no_speech():
    out = sr.transcribe_pcm(b"")
    assert out["text"] == "" and out["no_speech_prob"] == 1.0
    assert out["avg_logprob"] is None and out["compression_ratio"] is None


def test_transcribe_exposes_metrics_and_suppresses_echo(monkeypatch):
    """모의 모델로 계약 검증 — no_speech_prob 집계 + 에코 시 text 비움/suppressed."""
    class Seg:
        def __init__(self, text, alp, nsp, cr):
            self.text, self.avg_logprob = text, alp
            self.no_speech_prob, self.compression_ratio = nsp, cr

    class FakeModel:
        def __init__(self, segs):
            self.segs = segs
            self.calls = []

        def transcribe(self, audio, **kw):
            self.calls.append(kw)
            return iter(self.segs), None

    echo = FakeModel([Seg("진료, 복용, 약국, 예약 등 의료 용어가 사용됩니다.", -0.2, 0.87, 1.1)])
    monkeypatch.setattr(sr, "_get_model", lambda: echo)
    pcm = struct.pack("<8000h", *([100] * 8000))
    out = sr.transcribe_pcm(pcm, vad=True, min_speech_ms=300)
    assert out["text"] == "" and out["suppressed"] == "prompt_echo"
    assert out["no_speech_prob"] == 0.87 and out["compression_ratio"] == 1.1
    assert out["avg_logprob"] == -0.2
    # VAD 옵트인이 모델 호출 인자로 실제 전달됐는지(배선 무검증 함정 방지)
    kw = echo.calls[0]
    assert kw["vad_filter"] is True
    assert kw["vad_parameters"] == {"min_speech_duration_ms": 300}

    real = FakeModel([Seg("혈압이 높아서 약을 처방받으러 왔어요", -0.3, 0.05, 1.0)])
    monkeypatch.setattr(sr, "_get_model", lambda: real)
    out2 = sr.transcribe_pcm(pcm)
    assert out2["text"] == "혈압이 높아서 약을 처방받으러 왔어요"
    assert "suppressed" not in out2 and out2["no_speech_prob"] == 0.05
    assert real.calls[0]["vad_filter"] is False


def test_transcribe_no_segments_means_no_speech(monkeypatch):
    """VAD가 전부 걸러 세그먼트 0개 → text=""·no_speech_prob=1.0(HIS 드롭 일관성)."""
    class FakeModel:
        def transcribe(self, audio, **kw):
            return iter([]), None

    monkeypatch.setattr(sr, "_get_model", lambda: FakeModel())
    pcm = struct.pack("<8000h", *([0] * 8000))
    out = sr.transcribe_pcm(pcm, vad=True)
    assert out["text"] == "" and out["no_speech_prob"] == 1.0


def test_detect_suppression_reordered_echo_live_case():
    """라이브 실측(2026-07-29) 재배열 에코 — 순서 보존 부분열로는 0.61이라 통과했던
    형태(당뇨가 약국·예약 뒤로 재배열+약국·예약 반복). 어절 커버리지 축이 잡아야 한다."""
    live = "진료, 복용, 약국, 예약, 당뇨, 검사, 증상, 진단, 투약, 약국, 예약 등 의료 용어가 사용됩니다."
    assert sr._subseq_ratio(sr._norm_text(live), sr._norm_text(sr.STT_INITIAL_PROMPT)) < 0.85
    assert sr._detect_suppression(live, sr.STT_INITIAL_PROMPT, None) == "prompt_echo"


def test_word_coverage_separates_real_speech():
    """실발화는 조사가 붙어 어절이 프롬프트 어휘와 달라진다 — 커버리지 낮아야 한다."""
    real = "혈압이 높아서 약을 처방받으러 왔어요 검사도 예약해 주세요"
    assert sr._word_coverage(real, (sr.STT_INITIAL_PROMPT, None)) < 0.5
    assert sr._detect_suppression(real, sr.STT_INITIAL_PROMPT, None) is None
