"""c2pa_sign 단위테스트.

순수 분기(미설치/인증서 없음 → passthrough)는 항상 실행.
실제 서명/판독은 c2pa-python + 인증서 필요 → 없으면 skip(iMac skip, GPU 실행).
GPU 실행 시 임시 인증서를 openssl로 생성해 round-trip 검증.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import c2pa_sign as cs  # noqa: E402

pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _png(w=128, h=128):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (100, 140, 170)).save(buf, "PNG")
    return buf.getvalue()


def test_passthrough_when_no_cert():
    src = _png()
    # 존재하지 않는 인증서 경로 → 원본 그대로
    out = cs.sign_image_bytes(src, cert_path="/nonexistent/c.pem", key_path="/nonexistent/k.pem")
    assert out == src


def test_available_false_for_missing_paths():
    assert cs.available("", "") is False
    assert cs.available("/no/cert.pem", "/no/key.pem") is False


def test_sign_empty_bytes():
    assert cs.sign_image_bytes(b"", cert_path="/x", key_path="/y") == b""


def test_read_manifest_none_on_plain_image():
    if not cs.is_c2pa_installed():
        pytest.skip("c2pa 미설치")
    assert cs.read_manifest(_png()) is None


def _gen_certs(d):
    """openssl 2단 체인 생성 (테스트용). 실패 시 None."""
    if not shutil.which("openssl"):
        return None

    def run(c):
        subprocess.run(c, check=True, capture_output=True, shell=True, cwd=d)
    try:
        run("openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key")
        run('openssl req -new -x509 -key ca.key -out ca.crt -days 3650 -sha256 '
            '-subj "/CN=Test CA/O=T" -addext "basicConstraints=critical,CA:TRUE" '
            '-addext "keyUsage=critical,keyCertSign,cRLSign"')
        run("openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out sign.key")
        run('openssl req -new -key sign.key -out sign.csr -subj "/CN=Test Signer/O=T"')
        run('printf "keyUsage=critical,digitalSignature\\nextendedKeyUsage=critical,'
            'emailProtection\\n" > ext.cnf')
        run("openssl x509 -req -in sign.csr -CA ca.crt -CAkey ca.key -CAcreateserial "
            "-out sign.crt -days 3650 -sha256 -extfile ext.cnf")
        run("cat sign.crt ca.crt > chain.pem")
        return (os.path.join(d, "chain.pem"), os.path.join(d, "sign.key"))
    except Exception:
        return None


@pytest.mark.skipif(not cs.is_c2pa_installed(), reason="c2pa-python 미설치 (GPU에서 실행)")
@pytest.mark.skipif(__import__("platform").system() == "Darwin",
                    reason="macOS는 c2pa 실서명 미지원(LibreSSL/플랫폼 — v2.79 전제, GPU에서 검증)")
def test_sign_and_read_roundtrip():
    d = tempfile.mkdtemp()
    certs = _gen_certs(d)
    if not certs:
        pytest.skip("openssl 없음")
    cert_path, key_path = certs
    assert cs.available(cert_path, key_path)
    src = _png()
    out = cs.sign_image_bytes(src, cert_path=cert_path, key_path=key_path, model="flux-schnell")
    assert out != src
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    man = cs.read_manifest(out)
    assert man is not None
    assert man.get("active_manifest")
