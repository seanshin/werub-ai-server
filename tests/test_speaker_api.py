"""v2.125.51 — /api/speaker/* (HIS VoiceEMR B2 성문 계약).

계약 정본 = HIS 호출부 `hospital-run/apps/api/src/modules/voice-emr/stt-chunk.client.ts`:
  enroll → {speaker_id, samples, quality} · match → {enrolled, score}(HIS가 τ=0.7로 isOwner)
  delete → 200(HIS는 404/405/501을 '미가용=파기 못함'으로 읽는다) · 오디오=raw PCM 16-bit LE mono.
모델 없이 계약을 검증하려고 결정적 임베더를 주입한다(주파수가 다르면 다른 '목소리').
"""
import sqlite3

import numpy as np
import pytest
from flask import Flask

from core import speaker_store as S
from routes.speaker import speaker_bp


def _fake_embed(wave):
    spec = np.abs(np.fft.rfft(wave[:16000]))[:S.EMB_DIM * 4]
    return spec.reshape(S.EMB_DIM, 4).sum(axis=1).astype(np.float32)


def _pcm(freq=220.0, sec=2.0, sr=16000, amp=0.3):
    t = np.arange(int(sec * sr)) / sr
    return (np.sin(2 * np.pi * freq * t) * amp * 32767).astype("<i2").tobytes()


@pytest.fixture
def c(tmp_path, monkeypatch):
    # app.py가 speaker_bp에 인증 훅을 먼저 단다 — 단독 앱에 먼저 등록하면 이후 훅 부착이 거부된다
    import app  # noqa: F401
    monkeypatch.setattr(S, "DB_PATH", str(tmp_path / "speaker.db"))
    S.set_embedder(_fake_embed)
    app = Flask(__name__)
    app.register_blueprint(speaker_bp)
    yield app.test_client()
    S.set_embedder(None)


def _post(c, path, pcm, sid="user-1", sr=None, extra=None):
    h = {"Content-Type": "application/octet-stream", "X-Speaker-Id": sid}
    if sr:
        h["X-Sample-Rate"] = str(sr)
    h.update(extra or {})
    return c.post(path, data=pcm, headers=h)


def test_enroll_accumulates_and_reports_quality(c):
    r1 = _post(c, "/api/speaker/enroll", _pcm(220), extra={"X-Prompt-Id": "p1"})
    assert r1.status_code == 200
    assert r1.get_json() == {"speaker_id": "user-1", "samples": 1, "quality": None}
    r2 = _post(c, "/api/speaker/enroll", _pcm(220)).get_json()
    assert r2["samples"] == 2 and r2["quality"] > 0.99


def test_match_same_voice_high_other_voice_lower(c):
    for _ in range(3):
        _post(c, "/api/speaker/enroll", _pcm(220))
    same = _post(c, "/api/speaker/match", _pcm(220)).get_json()
    other = _post(c, "/api/speaker/match", _pcm(1800)).get_json()
    assert same["enrolled"] is True and same["samples"] == 3 and same["score"] > 0.99
    assert other["enrolled"] is True and other["score"] < same["score"]


def test_his_client_parsing_parity(c):
    """HIS matchSpeaker 판독을 그대로 재현: isOwner = !!enrolled && score >= τ(0.7)."""
    _post(c, "/api/speaker/enroll", _pcm(220))
    j = _post(c, "/api/speaker/match", _pcm(220)).get_json()
    score = j["score"] if isinstance(j.get("score"), (int, float)) else 0
    assert (bool(j.get("enrolled")) and score >= 0.7) is True


def test_match_unknown_is_not_error(c):
    r = _post(c, "/api/speaker/match", _pcm(220), sid="nobody")
    assert r.status_code == 200
    assert r.get_json() == {"enrolled": False, "score": 0.0, "samples": 0}


def test_delete_is_idempotent_never_404(c):
    _post(c, "/api/speaker/enroll", _pcm(220))
    r = c.delete("/api/speaker/user-1")
    assert r.status_code == 200 and r.get_json() == {"speaker_id": "user-1", "deleted": True}
    r2 = c.delete("/api/speaker/user-1")
    assert r2.status_code == 200 and r2.get_json()["deleted"] is False   # 404 금지(HIS=파기 못함)
    assert _post(c, "/api/speaker/match", _pcm(220)).get_json()["enrolled"] is False


@pytest.mark.parametrize("pcm,code,status", [
    (_pcm(sec=0.5), "sample_too_short", 400),
    (b"\x00\x00" * 32000, "sample_silent", 400),
    (b"\x01\x02\x03", "bad_pcm", 400),
    (b"", "bad_pcm", 400),
    (_pcm(sec=31), "sample_too_long", 413),
])
def test_bad_samples_rejected(c, pcm, code, status):
    r = _post(c, "/api/speaker/enroll", pcm)
    assert r.status_code == status and r.get_json()["error"] == code


@pytest.mark.parametrize("sid", ["", "a b", "../x", "x" * 129, "id;drop"])
def test_bad_speaker_id(c, sid):
    r = _post(c, "/api/speaker/enroll", _pcm(), sid=sid)
    assert r.status_code == 400 and r.get_json()["error"] == "bad_speaker_id"


def test_bad_sample_rate(c):
    assert _post(c, "/api/speaker/enroll", _pcm(), sr="abc").status_code == 400


def test_resample_path_8k(c):
    pytest.importorskip("resampy")
    r = _post(c, "/api/speaker/enroll", _pcm(220, sr=8000), sr=8000)
    assert r.status_code == 200 and r.get_json()["samples"] == 1


def test_max_samples_409(c, monkeypatch):
    monkeypatch.setattr(S, "MAX_SAMPLES", 2)
    assert _post(c, "/api/speaker/enroll", _pcm()).status_code == 200
    assert _post(c, "/api/speaker/enroll", _pcm()).status_code == 200
    r = _post(c, "/api/speaker/enroll", _pcm())
    assert r.status_code == 409 and r.get_json()["error"] == "max_samples"


def test_model_unavailable_is_503(c):
    def boom(_):
        raise S.ModelUnavailable("no speechbrain")
    S.set_embedder(boom)
    r = _post(c, "/api/speaker/enroll", _pcm())
    assert r.status_code == 503 and r.get_json()["error"] == "speaker_model_unavailable"


def test_no_audio_is_stored(c, tmp_path):
    """저장소에는 임베딩 합(192×float32)과 표본 수만 — 원 오디오 바이트는 어디에도 없다."""
    pcm = _pcm(220)
    _post(c, "/api/speaker/enroll", pcm)
    db = sqlite3.connect(str(tmp_path / "speaker.db"))
    tables = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    assert tables == ["speakers"]
    (blob,) = db.execute("select emb_sum from speakers").fetchone()
    assert len(blob) == S.EMB_DIM * 4 and pcm[:64] not in blob
    raw = (tmp_path / "speaker.db").read_bytes()
    assert pcm[1000:1064] not in raw


def test_status_has_no_identifiers(c):
    _post(c, "/api/speaker/enroll", _pcm(220))
    j = c.get("/api/speaker/status").get_json()
    assert j["speakers"] == 1 and j["samples"] == 1 and j["stores_audio"] is False
    assert "user-1" not in str(j)


def test_registered_in_app_and_auth_enforced_externally():
    """앱에 배선됐고, 엣지 모양(루프백+XFF)·무인증이면 401 — 성문은 생체정보 파생물이다."""
    import app as A
    rules = {r.rule for r in A.app.url_map.iter_rules()}
    assert {"/api/speaker/enroll", "/api/speaker/match", "/api/speaker/<speaker_id>",
            "/api/speaker/status"} <= rules
    # HIS는 항상 Bearer로 부른다. 틀린 키 → 인증 훅이 401(앱 CSRF 가드는 Bearer면 통과시킨다 —
    # Bearer 없는 octet-stream POST는 인증 이전에 CSRF 가드가 415로 막는다: 기존 /api/stt/chunk와 동일)
    r = A.app.test_client().post("/api/speaker/enroll", data=_pcm(),
                                 headers={"X-Forwarded-For": "203.0.113.9", "X-Speaker-Id": "u",
                                          "Content-Type": "application/octet-stream",
                                          "Authorization": "Bearer sk-invalid-test-key"},
                                 environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 401
