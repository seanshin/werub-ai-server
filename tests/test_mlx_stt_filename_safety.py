"""⑤ mlx_stt 파일명 경로 순회 봉쇄 (v2.125.23 · v2.125.18 이월분).

`/worker/stt`가 클라이언트 filename(multipart 또는 JSON)을 검증 없이
`os.path.join(tmp, filename)`에 넣어, `../../x`·절대경로면 tmp 밖 임의 경로에
audio_bytes를 썼다. 게이트는 `@worker_auth`(공유 토큰)라 임의 API 키는 아니지만,
워커 토큰 보유자가 워커 계정 권한으로 파일을 쓸 수 있었다(secure_filename 미적용).

처치=싱크에서 basename+확장자만 취해 결정적 이름(`input{ext}`)으로 씀. 확장자는
ffmpeg 포맷 힌트로만 쓰이므로 그 정보만 보존하면 충분. mlx-whisper 미설치 환경
(GPU·CI)에서도 경로 계산부만 단위 검증할 수 있게 헬퍼 없이 로직을 재현해 고정한다
— 실제 봉쇄는 core/mlx_stt.transcribe의 tmp 안이라 소스 스캔으로도 확인.
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def _safe_name(filename, tmp="/tmp/xdir"):
    """core/mlx_stt.transcribe의 파일명 정규화 로직과 동일 — 여기서 고정한다."""
    ext = os.path.splitext(os.path.basename(filename or ""))[1]
    if not re.match(r"^\.[A-Za-z0-9]{1,8}$", ext):
        ext = ".bin"
    return os.path.join(tmp, "input" + ext)


def test_traversal_and_absolute_stay_in_tmp():
    tmp = "/tmp/xdir"
    for bad in ("../../etc/passwd", "/etc/passwd", "../../../a.wav",
                "..\\..\\win.wav", "a/b/c.mp4", "....//x.m4a"):
        out = _safe_name(bad, tmp)
        assert os.path.dirname(out) == tmp, bad
        assert os.path.commonpath([tmp, out]) == tmp, bad
        assert ".." not in out


def test_extension_preserved_for_ffmpeg():
    assert _safe_name("meeting.mp4").endswith("/input.mp4")
    assert _safe_name("/x/y/z.flac").endswith("/input.flac")
    assert _safe_name("../a.WAV").endswith("/input.WAV")


def test_missing_or_weird_ext_falls_back_bin():
    for n in (None, "", "noext", "a.", "a.verylongextension", "a.экс"):
        assert _safe_name(n).endswith("/input.bin"), n


def test_source_uses_basename_not_raw_filename():
    """소스 스캔(v2.125.16 규율) — 싱크가 raw filename join으로 회귀하면 잡는다."""
    with open(os.path.join(_ROOT, "core", "mlx_stt.py"), encoding="utf-8") as f:
        src = f.read()
    assert 'os.path.join(tmp, filename)' not in src
    assert "os.path.basename(filename" in src
