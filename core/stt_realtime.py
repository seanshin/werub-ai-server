"""실시간 청크 STT — 워밍(상주) faster-whisper 싱글톤 (GPU/CUDA).

호스트 STT 파이프라인(`POST /api/stt/chunk`) 백엔드. 배치형 `meeting_stt`(large-v3
온디맨드)와 달리 **소형 모델을 상주(warm)** 시켜 실시간 청크 지연을 낮춘다.

- 입력: raw 16-bit LE mono PCM (기본 16kHz). ffmpeg 불요(원시 PCM → numpy 직접 변환).
- 동시성: 호스트 회의 게이팅으로 1~3, **단일 직렬**(`_infer_lock`)이라 iMac/GPU 컨텍스트 안전.
- 세션: `session_id`별 롤링 버퍼(슬라이딩 상한·TTL 폐기)로 partial 누적, final 시 flush.
  세션 미지정 시 청크 단독 전사.

모델: env `STT_REALTIME_MODEL`(기본 small — 한국어 정확/지연 균형). device는
`meeting_stt._resolve_device_compute`(cuda/float16 우선)를 재사용한다.
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

# ---------------------------------------------------------------------------
# 설계 메모 (실시간 STT)
#
# - Flask threaded 는 요청마다 새 스레드를 만들어 cuDNN 워밍업이 리셋된다. 그래서 추론을
#   **단일 락(_infer_lock)으로 직렬화**한다(동시 요청은 거부가 아니라 큐잉).
# - 세션 버퍼는 **최근 30초 슬라이딩**이다. 초과분을 그냥 버리면 장문 구술에서 전사가
#   조용히 사라지므로 **버린 길이를 dropped_s 로 통지**한다(무성 실패 금지 규율).
# - partial 은 증분이 아니라 **누적분 전체 재인식**이다. 호출자는 화면을 replace 해야 한다.
# - 어휘 바이어싱(hotwords)은 whisper 프롬프트 창(224토큰) 때문에 **shortlist 전제**다.
#   상한 초과분은 조용히 버리지 않고 등록 응답의 truncated 로 알린다.
# - 어휘는 sqlite 에 영속화한다 — gunicorn 워커가 ~40분마다 재생성되면서 인메모리 등록이
#   통째로 사라졌기 때문이다(진료 중 바이어싱이 조용히 꺼지던 문제).
#
# 상세: docs/DEVELOPER_GUIDE.md §1-8
# ---------------------------------------------------------------------------

import logging
import math
import os
import re
import threading
import time

from core.stt_hints import DEFAULT_MAX_HINT_TOKENS as _HINT_MAX_TOKENS, cap_hints

logger = logging.getLogger(__name__)

# medium = 한국어 의료용어 정확도/VRAM 균형(small은 '처방전'→'초광전' 오인식, large-v3는
# 상주 LLM과 16GB GPU에서 CUDA OOM). 운영/실험은 env로 교체.
STT_REALTIME_MODEL = os.environ.get("STT_REALTIME_MODEL", "medium")
SAMPLE_RATE = 16000
# 의료 도메인 initial_prompt — 한국어 의료 용어 인식 바이어스(OOV 오인식 완화). 라이브서
# '초방전'→'처방전' 교정 확인. env로 도메인 어휘 교체 가능.
STT_INITIAL_PROMPT = os.environ.get(
    "STT_INITIAL_PROMPT",
    "의료 상담입니다. 처방전, 진료, 복용, 혈압, 당뇨, 검사, 증상, 진단, 투약, 약국, 예약 "
    "등 의료 용어가 사용됩니다.")
STT_BEAM_SIZE = int(os.environ.get("STT_BEAM_SIZE", "5"))   # 정확도 우선(greedy=1 대비)
# 저신뢰 STT는 번역 cascade 보류(환각 전파 방지). conf < 임계면 번역 생략 + low_confidence 플래그.
STT_TRANSLATE_MIN_CONF = float(os.environ.get("STT_TRANSLATE_MIN_CONF", "0.5"))
# 무음/저에너지 입력에서 initial_prompt·hotwords가 전사문으로 새는 환각(HIS 실측 제보) 억제.
# 프롬프트는 서버가 주입하는 값이라 에코 차단도 서버 책임이다. 0으로 끄면 노출만 하고 미차단.
STT_ECHO_SUPPRESS = os.environ.get("STT_ECHO_SUPPRESS", "1") not in ("0", "false")
# whisper가 유튜브 학습데이터에서 가져오는 정형 환각구(무음에서 방출). 진료 발화로 성립할 수
# 없는 문구만 넣는다 — 실발화 오삭제 위험이 있는 일반 문구는 넣지 않는다. env CSV로 확장.
_HALLU_PATTERNS = [p.strip() for p in (
    os.environ.get("STT_HALLU_PATTERNS", "") or
    "이 영상은 유료 광고를 포함하고 있습니다,시청해 주셔서 감사합니다,시청해주셔서 감사합니다,"
    "구독과 좋아요 부탁드립니다,다음 영상에서 만나요,자막은 설정에서 선택하실 수 있습니다"
).split(",") if p.strip()]
# whisper 창 단위 조건화(직전 창 텍스트를 프롬프트로 재주입)는 환각 연쇄의 통로다. 청크
# 오디오는 ≤30초(창 1개)라 끊어도 정확도 손실이 사실상 없어 기본 off(HIS 요청 반영).
STT_CONDITION_PREV = os.environ.get("STT_CONDITION_PREV", "0") not in ("0", "false")

# 세션 버퍼 정책 — '동시 N세션 무누수' 보장: 슬라이딩 상한 + 무활동 TTL 폐기.
_SESSION_TTL_S = 180                       # 무활동 3분 후 세션 폐기
_SESSION_MAX_SEC = 30                       # 발화 누적 상한(초) — 30s 초과분은 슬라이딩 폐기
_SESSION_MAX_BYTES = SAMPLE_RATE * 2 * _SESSION_MAX_SEC   # 16k * 2byte(int16) * 30s

_model = None
_model_lock = threading.Lock()
_last_used = 0.0                            # 마지막 전사 시각(유휴 판정 → 배치와의 VRAM 경합 완화)
_infer_lock = threading.Lock()             # 추론 직렬화(단일 컨텍스트 보호)

_sessions = {}                             # sid -> {"buf": bytearray, "ts": float}
_sessions_lock = threading.Lock()

# 어휘 바이어싱(hotwords) — 호출자(HIS 약품마스터 등)가 진료 단위 후보 목록을 등록하면
# 해당 세션의 이후 청크에 적용된다. whisper 프롬프트 창(224 토큰)이 상한이라 **전체 마스터를
# 넣을 수 없다** → 진료별 shortlist를 받아 아래 캡으로 자른다(초과분은 조용히 버리지 않고
# 등록 응답에 반영해 호출자가 알 수 있게 한다).
_VOCAB_TTL_S = 3600                        # 진료 세션 길이 고려(무활동 1시간 후 폐기)
_VOCAB_MAX_TERMS = 200
# 🔴 캡은 **토큰 기준**이다(v2.124.63). 종전 600자 캡은 프롬프트 창(224토큰)보다 커서,
# 힌트가 많으면 전사가 조용히 잘리거나 실패한다(edu 배치 STT 실측: 168자 −70%·207자 실패).
# 같은 whisper 제약이므로 실시간 경로도 같은 캡을 쓴다.
_vocab = {}                                # sid -> {"hotwords": str, "prompt": str|None, "ts": float}
_vocab_lock = threading.Lock()


def _normalize_terms(terms) -> str:
    """용어 목록(문자열 CSV 또는 배열) → 캡 적용된 hotwords 문자열."""
    if not terms:
        return ""
    if isinstance(terms, str):
        items = [t.strip() for t in terms.split(",")]
    else:
        items = [str(t).strip() for t in terms]
    items = [t for t in items if t][:_VOCAB_MAX_TERMS]
    capped, _used, _dropped = cap_hints(", ".join(items))
    return capped or ""


def _vocab_db():
    """어휘 영속 저장소 — 워커 재생성(~40분) 후에도 등록이 살아남게 한다.

    진료(세션)는 워커 수명보다 길 수 있는데 인메모리 dict는 재생성 시 통째로 사라져
    호출자가 매번 재등록해야 했다(HIS Q11). 저장 내용은 **약품·검사명 등 어휘뿐**이고
    발화·전사는 저장하지 않는다(PHI 비저장 원칙 유지).
    """
    import sqlite3
    path = os.path.join(os.environ.get("STT_VOCAB_DIR", os.getcwd()), "stt_vocab.db")
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS session_vocab ("
                 "session_id TEXT PRIMARY KEY, hotwords TEXT, prompt TEXT, ts REAL)")
    # 어휘가 환자 활성 처방에서 파생될 수 있어 준민감으로 다룬다 — 서버의 전 DB 600 규율
    # (2026-07-25 하드닝·제2 로컬 계정 존재)을 신규 파일에도 적용한다.
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(path + suffix, 0o600)
        except OSError:
            pass
    return conn


def _vocab_persist(session_id, hot, prompt, ts):
    try:
        conn = _vocab_db()
        with conn:
            if not hot and not prompt:
                conn.execute("DELETE FROM session_vocab WHERE session_id=?", (session_id,))
            else:
                conn.execute("INSERT OR REPLACE INTO session_vocab VALUES (?,?,?,?)",
                             (session_id, hot, prompt, ts))
            conn.execute("DELETE FROM session_vocab WHERE ts < ?", (ts - _VOCAB_TTL_S,))
        conn.close()
    except Exception as e:  # noqa: BLE001 — 영속화 실패가 등록 자체를 막지 않는다
        logger.warning("vocab 영속화 실패(인메모리는 유효): %s", e)


def _vocab_restore(session_id):
    """인메모리에 없으면 저장소에서 복구 → dict 또는 None."""
    try:
        conn = _vocab_db()
        row = conn.execute("SELECT hotwords, prompt, ts FROM session_vocab WHERE session_id=?",
                           (session_id,)).fetchone()
        conn.close()
        if not row or time.time() - (row[2] or 0) > _VOCAB_TTL_S:
            return None
        return {"hotwords": row[0] or "", "prompt": row[1], "ts": row[2]}
    except Exception as e:  # noqa: BLE001
        logger.debug("vocab 복구 skip: %s", e)
        return None


def set_session_vocab(session_id: str, terms=None, initial_prompt: str = None) -> dict:
    """세션(진료) 단위 어휘 등록 → 적용된 용어 수/절단 여부 반환. terms 비우면 해제."""
    hot = _normalize_terms(terms)
    requested = len(terms.split(",")) if isinstance(terms, str) else len(terms or [])
    applied = len([t for t in hot.split(",") if t.strip()]) if hot else 0
    now = time.time()
    with _vocab_lock:
        for k, v in [(k, v) for k, v in _vocab.items() if now - v["ts"] > _VOCAB_TTL_S]:
            _vocab.pop(k, None)
        if not hot and not initial_prompt:
            _vocab.pop(session_id, None)
        else:
            _vocab[session_id] = {"hotwords": hot, "prompt": initial_prompt or None, "ts": now}
    _vocab_persist(session_id, hot, initial_prompt or None, now)   # 워커 재생성 생존
    return {"session_id": session_id, "terms_requested": requested, "terms_applied": applied,
            "truncated": applied < requested, "chars": len(hot),
            "max_terms": _VOCAB_MAX_TERMS, "max_tokens": _HINT_MAX_TOKENS,
            "initial_prompt": bool(initial_prompt)}


def get_session_vocab(session_id: str) -> dict:
    if not session_id:
        return {}
    now = time.time()
    with _vocab_lock:
        v = _vocab.get(session_id)
        if not v:
            restored = _vocab_restore(session_id)     # 워커 재생성 후 첫 조회 → 저장소 복구
            if not restored:
                return {}
            _vocab[session_id] = restored
            v = restored
        if now - v["ts"] > _VOCAB_TTL_S:
            _vocab.pop(session_id, None)
            return {}
        v["ts"] = now                       # 진료 중 갱신(무활동 기준 TTL)
        return {"hotwords": v["hotwords"], "prompt": v["prompt"]}


def vocab_count() -> int:
    with _vocab_lock:
        return len(_vocab)


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                from core.meeting_stt import _resolve_device_compute
                dev, comp = _resolve_device_compute()
                t0 = time.time()
                _model = WhisperModel(STT_REALTIME_MODEL, device=dev, compute_type=comp)
                logger.info("stt_realtime warm: model=%s device=%s compute=%s (%.1fs)",
                            STT_REALTIME_MODEL, dev, comp, time.time() - t0)
    return _model


def warm() -> bool:
    """모델 사전 로드(상주). 기동 또는 첫 세션 전 호출해 첫 청크 콜드로드 제거."""
    try:
        _get_model()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("stt_realtime warm failed: %s", e)
        return False


def idle_seconds() -> float:
    """마지막 전사 이후 경과 초. 한 번도 안 썼으면 매우 큰 값(=유휴)."""
    return 1e9 if _last_used == 0.0 else time.time() - _last_used


def unload() -> bool:
    """상주 모델 해제 → VRAM 반납. 배치 STT와의 VRAM 경합 완화용(유휴일 때만 호출할 것).

    실측(2026-07-28): 실시간 medium 상주 시 여유 3.18GB인데 배치 large-v3는 3.84GB가 필요해
    **동시 적재가 OOM**이다. 진료가 진행 중이 아닐 때 반납해 배치가 돌 수 있게 한다.
    다음 사용 시 자동 재로드되며, 콜드 로드(~18s)를 피하려면 `/api/stt/chunk/warm` 재호출.
    """
    global _model, _last_used
    with _model_lock:
        if _model is None:
            return False
        _model = None
        _last_used = 0.0
    try:
        import gc
        import torch
        gc.collect()
        torch.cuda.empty_cache()
    except Exception as e:  # noqa: BLE001 — torch 없거나 CPU 환경이면 무시
        logger.debug("unload cache clear skipped: %s", e)
    logger.info("stt_realtime unloaded (VRAM 반납)")
    return True


def status() -> dict:
    return {
        "available": is_available(),
        "model": STT_REALTIME_MODEL,
        "loaded": _model is not None,
        "active_sessions": session_count(),
        "sample_rate": SAMPLE_RATE,
        "beam_size": STT_BEAM_SIZE,
        "translate_min_conf": STT_TRANSLATE_MIN_CONF,
        "initial_prompt": bool(STT_INITIAL_PROMPT),
        "hotwords_supported": True,
        "vocab_sessions": vocab_count(),
        "vocab_max_terms": _VOCAB_MAX_TERMS,
        # 실효 상한은 문자수가 아니라 **토큰**이다 — whisper 프롬프트 창(224토큰)을
        # 넘기면 전사가 조용히 잘린다(v2.124.63·edu 실측).
        "vocab_max_tokens": _HINT_MAX_TOKENS,
        "vocab_persistent": True,   # 워커 재생성(~40분) 후에도 유지 — 재등록 불요
        "serialized_inference": True,   # 단일 _infer_lock — 동시 요청은 큐잉된다(계약)
        # 무음/환각 처리(HIS 2026-07-29 요청): 응답에 no_speech_prob·avg_logprob·
        # compression_ratio 노출 + 프롬프트 에코 서버 억제 + X-VAD 옵트인 게이트
        "no_speech_prob_exposed": True,
        "echo_suppression": STT_ECHO_SUPPRESS,
        "vad_gate_supported": True,
        "idle_seconds": round(idle_seconds(), 1) if _model is not None else None,
    }


def _pcm_to_array(pcm: bytes, sample_rate: int):
    """raw 16-bit LE mono PCM → float32 [-1,1] numpy 배열(16kHz)."""
    import numpy as np
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if sample_rate and sample_rate != SAMPLE_RATE and len(audio) > 1:
        n = int(len(audio) * SAMPLE_RATE / sample_rate)
        if n > 0:
            audio = np.interp(
                np.linspace(0, len(audio), n, endpoint=False),
                np.arange(len(audio)), audio).astype(np.float32)
    return audio


# ---------------------------------------------------------------------------
# 세션 롤링 버퍼 (partial 누적 / final flush) — 무누수
# ---------------------------------------------------------------------------

def _evict_expired(now: float):
    stale = [k for k, v in _sessions.items() if now - v["ts"] > _SESSION_TTL_S]
    for k in stale:
        _sessions.pop(k, None)
    if stale:
        logger.info("stt_realtime evicted %d stale session(s)", len(stale))


def session_append(session_id: str, pcm: bytes, final: bool) -> bytes:
    """세션 버퍼에 청크 누적 후 누적 PCM 반환. 슬라이딩 상한 적용. final이면 flush(폐기)."""
    now = time.time()
    with _sessions_lock:
        _evict_expired(now)
        s = _sessions.get(session_id)
        if s is None:
            s = {"buf": bytearray(), "ts": now}
            _sessions[session_id] = s
        s["buf"].extend(pcm)
        s["ts"] = now
        if len(s["buf"]) > _SESSION_MAX_BYTES:    # 슬라이딩: 최근 N초만 유지
            dropped = len(s["buf"]) - _SESSION_MAX_BYTES
            del s["buf"][:dropped]
            # 버린 만큼을 누적 보고한다 — 종전에는 **조용히** 버려서 호출자가 앞부분
            # 유실을 알 수 없었다(장문 구술에서 전사 누락으로 직결). 호출자는 이 값이
            # 0보다 커지면 더 짧은 주기로 final을 끊어야 한다.
            s["dropped"] = s.get("dropped", 0) + dropped
        data = bytes(s["buf"])
        dropped_s = round(s.get("dropped", 0) / (SAMPLE_RATE * 2), 2)
        if final:
            _sessions.pop(session_id, None)
        return data, dropped_s


def session_state(session_id: str) -> dict:
    """세션 관측용 — 누적 길이·유실 길이(없으면 빈 dict)."""
    with _sessions_lock:
        s = _sessions.get(session_id)
        if not s:
            return {}
        return {"buffered_s": round(len(s["buf"]) / (SAMPLE_RATE * 2), 2),
                "dropped_s": round(s.get("dropped", 0) / (SAMPLE_RATE * 2), 2),
                "max_s": _SESSION_MAX_SEC}


def session_count() -> int:
    with _sessions_lock:
        return len(_sessions)


def clear_sessions():
    with _sessions_lock:
        _sessions.clear()


# ---------------------------------------------------------------------------
# 무음 환각 억제 (프롬프트 에코 · 정형 환각구) — 순수 함수
# ---------------------------------------------------------------------------

def _norm_text(s: str) -> str:
    """비교용 정규화 — 공백·문장부호 제거(전사와 프롬프트의 표기 차이 흡수)."""
    return re.sub(r"[\s.,·…‥'\"“”‘’?!()\[\]{}:;~\-—]+", "", s or "")


def _subseq_ratio(needle: str, haystack: str) -> float:
    """needle 문자가 haystack 안에 **순서대로** 존재하는 비율(0~1).

    프롬프트 에코는 프롬프트를 통째가 아니라 **어휘를 건너뛰며** 재조합해 방출한다
    (HIS 실측: "진료, 복용, 약국, 예약 … 의료 용어가 사용됩니다" = 기본 프롬프트에서
    혈압·당뇨 등을 건너뛴 부분열). 그래서 연속 부분문자열 검사로는 못 잡고, 순서 보존
    부분열 매칭이어야 잡힌다. 실발화는 조사·동사가 프롬프트에 없어 비율이 낮게 나온다.
    """
    if not needle or not haystack:
        return 0.0
    pos, hit = 0, 0
    for ch in needle:
        j = haystack.find(ch, pos)
        if j >= 0:
            hit += 1
            pos = j + 1
    return hit / len(needle)


_ECHO_MIN_CHARS = 6          # 이보다 짧은 전사("진료", "네 처방전")는 실발화 보호 — 미판정
_ECHO_RATIO = 0.85           # 순서 보존 부분열 비율 임계
_ECHO_MIN_WORDS = 4          # 어절 커버리지 판정 최소 어절 수(짧은 실발화 보호)
_ECHO_WORD_COVER = 0.8       # 어절이 프롬프트 어휘로만 구성된 비율 임계

_SPLIT_RE = re.compile(r"[\s.,·…‥'\"“”‘’?!()\[\]{}:;~\-—]+")


def _word_coverage(text: str, srcs) -> float:
    """전사 어절이 프롬프트/hotwords **어절 사전**에 속하는 비율(무순서).

    라이브 실측(2026-07-29): 에코는 어휘를 건너뛸 뿐 아니라 **재배열·반복**한다
    ("…약국, 예약, 당뇨…" — 프롬프트 원문 순서와 다름) → 순서 보존 부분열만으로는
    임계 미달로 통과했다. 실발화는 명사에 조사가 붙어 어절 자체가 달라지므로
    ("혈압이"≠"혈압") 무순서 어절 대조가 정확한 분리축이다.
    """
    toks = [t for t in _SPLIT_RE.split(text or "") if t]
    if not toks:
        return 0.0
    vocab = set()
    for s in srcs:
        vocab.update(t for t in _SPLIT_RE.split(s or "") if t)
    return sum(1 for t in toks if t in vocab) / len(toks)


def _detect_suppression(text: str, prompt: str, hotwords: str):
    """전사문이 서버 주입 프롬프트의 에코이거나 정형 환각구면 사유 문자열, 아니면 None."""
    nt = _norm_text(text)
    if not nt:
        return None
    # 정형 환각구(유튜브 학습데이터 기원) — 문구가 전사 전체를 사실상 차지할 때만
    for pat in _HALLU_PATTERNS:
        np_ = _norm_text(pat)
        if np_ and np_ in nt and len(nt) - len(np_) <= 6:
            return "hallucination_pattern"
    if len(nt) < _ECHO_MIN_CHARS:
        return None   # 짧은 실발화(단일 용어 언급)는 길이 게이트로 보호
    # 에코 축 1: 순서 보존 부분열(프롬프트 문장형 에코 "…등 의료 용어가 사용됩니다")
    for src in (prompt, hotwords):
        ns = _norm_text(src)
        if ns and _subseq_ratio(nt, ns) >= _ECHO_RATIO:
            return "prompt_echo"
    # 에코 축 2: 무순서 어절 커버리지(재배열·반복 나열형 에코 — 라이브 실측 형태)
    toks = [t for t in _SPLIT_RE.split(text) if t]
    if len(toks) >= _ECHO_MIN_WORDS and _word_coverage(text, (prompt, hotwords)) >= _ECHO_WORD_COVER:
        return "prompt_echo"
    return None


# ---------------------------------------------------------------------------
# 전사
# ---------------------------------------------------------------------------

def transcribe_pcm(pcm: bytes, sample_rate: int = SAMPLE_RATE, language: str = "ko",
                   initial_prompt: str = None, hotwords: str = None,
                   vad: bool = False, min_speech_ms: int = 250) -> dict:
    """raw PCM(16-bit LE mono) → {text, confidence, no_speech_prob, ..., ms, duration_s}.

    confidence = 세그먼트 avg_logprob의 exp 평균([0,1] 클램프). 무음/빈입력은 text="".
    initial_prompt 미지정 시 의료 도메인 기본 프롬프트(STT_INITIAL_PROMPT) 사용.
    hotwords = 진료 단위 어휘 바이어싱(약품명·검사명 등). 미지정 시 미적용.
    vad=True면 Silero VAD로 무음 구간을 모델 앞단에서 제거(무음 청크 → 빈 전사).

    무음 판별 신호(HIS 청크 드롭용) — 세그먼트 값 집계:
      no_speech_prob   무음일 확률(평균·세그먼트 0개면 1.0). 프롬프트 에코 환각은
                       confidence가 높게 나와 기존 low_confidence 게이트를 통과하므로
                       이 값이 유일한 판별 신호다.
      avg_logprob      디코딩 로그확률 평균(낮을수록 저신뢰)
      compression_ratio 반복 환각 지표(높을수록 반복 루프 의심·최댓값 집계)
    """
    t0 = time.time()
    audio = _pcm_to_array(pcm, sample_rate)
    duration_s = round(len(audio) / SAMPLE_RATE, 2)
    if len(audio) == 0:
        return {"text": "", "confidence": 0.0, "ms": 0, "duration_s": 0.0,
                "no_speech_prob": 1.0, "avg_logprob": None, "compression_ratio": None}

    prompt = initial_prompt if initial_prompt is not None else STT_INITIAL_PROMPT
    model = _get_model()
    texts, probs, nsp, comp = [], [], [], []
    kwargs = {}
    if hotwords:
        kwargs["hotwords"] = hotwords         # faster-whisper >=1.1 (설치본 1.2.1 확인)
    if vad:
        # Silero VAD가 인코더 앞에서 무음을 제거 — 무음 청크는 세그먼트 0개로 끝난다.
        # 에너지 게이트보다 견고(저에너지 잡음도 비발화로 판정). 기본 off = 기존 계약 불변.
        kwargs["vad_parameters"] = {"min_speech_duration_ms": max(0, int(min_speech_ms))}
    with _infer_lock:                         # 단일 직렬(동시 추론 방지)
        segments, _info = model.transcribe(
            audio, language=language, beam_size=STT_BEAM_SIZE, vad_filter=bool(vad),
            condition_on_previous_text=STT_CONDITION_PREV,
            initial_prompt=prompt or None, **kwargs)
        for seg in segments:
            nsp.append(getattr(seg, "no_speech_prob", 0.0) or 0.0)
            comp.append(getattr(seg, "compression_ratio", 0.0) or 0.0)
            txt = (seg.text or "").strip()
            if txt:
                texts.append(txt)
                probs.append(seg.avg_logprob)

    global _last_used
    _last_used = time.time()

    text = " ".join(texts).strip()
    conf = round(sum(math.exp(p) for p in probs) / len(probs), 3) if probs else 0.0
    conf = max(0.0, min(1.0, conf))
    result = {
        "text": text,
        "confidence": conf,
        "ms": int((time.time() - t0) * 1000),
        "duration_s": duration_s,
        # 세그먼트 0개(무음/VAD 전체 제거)는 no_speech_prob=1.0 — HIS 드롭 로직 일관성
        "no_speech_prob": round(sum(nsp) / len(nsp), 3) if nsp else 1.0,
        "avg_logprob": round(sum(probs) / len(probs), 3) if probs else None,
        "compression_ratio": round(max(comp), 3) if comp else None,
    }

    # 프롬프트 에코·정형 환각구는 서버가 차단해 빈 전사로 반환(HIS 요청 2).
    # 원문은 응답에 싣지 않고 사유만 노출(suppressed) — 로그도 길이만 남긴다(PHI 원칙).
    if text and STT_ECHO_SUPPRESS:
        reason = _detect_suppression(text, prompt, hotwords)
        if reason:
            logger.info("stt_realtime suppressed %s (%d chars, nsp=%.2f)",
                        reason, len(text), result["no_speech_prob"])
            result["text"] = ""
            result["suppressed"] = reason
    return result
