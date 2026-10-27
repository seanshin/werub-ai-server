"""관리 콘솔 설정 경로 결함 봉합 회귀 — admin-menu-settings-cleanup Phase 0a/0b.

기획: docs/01-plan/features/admin-menu-settings-cleanup.plan.md §2.4·§4.2·§6
- D1 Ollama 성능 설정: 화면이 drop-in만 읽어 /etc/default/ollama에 가려진 값을 보여 줬고
  (라이브 화면 MAX_LOADED=1·실제 3), 저장은 권한 오류로 500이었다.
- D2 GET/POST /api/config가 시크릿 원문을 브라우저로 보냈다.
- D3 인증 완화가 외부 관리자 세션에서도 체크박스 한 번으로 저장됐다.
"""
import os
import stat
from unittest.mock import patch

import pytest

import routes.ollama as ro

EXTERNAL = {"X-Forwarded-For": "203.0.113.7"}


def _systemctl_calls(run_mock):
    """subprocess.run 패치는 전역이라 백그라운드 GPU 모니터의 nvidia-smi 호출도 잡힌다
    (assert_not_called가 15회 중 4회 흔들렸다) — 우리 경로의 systemctl 호출만 센다."""
    return [c for c in run_mock.call_args_list
            if c.args and isinstance(c.args[0], (list, tuple)) and "systemctl" in c.args[0]]


# ── D1: 유효값 계산 ────────────────────────────────────────────────

@pytest.fixture
def ollama_env(tmp_path, monkeypatch):
    """drop-in + EnvironmentFile 픽스처 (라이브 GPU 서버와 같은 모양)."""
    dropin = tmp_path / "environment.conf"
    dropin.write_text(
        "[Service]\n"
        'Environment="OLLAMA_FLASH_ATTENTION=1"\n'
        'Environment="OLLAMA_MAX_LOADED_MODELS=1"\n'
        'Environment="OLLAMA_CONTEXT_LENGTH=8192"\n'
        'Environment="CUDA_VISIBLE_DEVICES=0"\n'
        "LimitNOFILE=65536\n")
    default = tmp_path / "ollama.default"
    default.write_text(
        "# comment\n"
        "OLLAMA_FLASH_ATTENTION=1\n"
        "OLLAMA_MAX_LOADED_MODELS=3\n"
        'OLLAMA_HOST="127.0.0.1:11434"\n')
    monkeypatch.setattr(ro, "_IS_LINUX", True)
    monkeypatch.setattr(ro, "_OLLAMA_SYSTEMD_ENV", str(dropin))
    monkeypatch.setattr(ro, "_OLLAMA_ENV_FILES", (str(default),))
    return dropin, default


def test_effective_env_file_overrides_dropin(ollama_env):
    dropin, default = ollama_env
    eff = ro._effective_ollama_env()
    # 라이브에서 드러난 모양 그대로: drop-in 1 / EnvironmentFile 3 → 실제 3
    assert eff["OLLAMA_MAX_LOADED_MODELS"]["value"] == "3"
    assert eff["OLLAMA_MAX_LOADED_MODELS"]["source"] == str(default)
    assert eff["OLLAMA_MAX_LOADED_MODELS"]["shadowed"] == {"value": "1", "source": str(dropin)}
    # drop-in에만 있는 키는 drop-in이 출처
    assert eff["OLLAMA_CONTEXT_LENGTH"] == {"value": "8192", "source": str(dropin), "shadowed": None}
    # 따옴표 벗김
    assert eff["OLLAMA_HOST"]["value"] == "127.0.0.1:11434"


def test_get_perf_reports_effective_value(client, ollama_env):
    r = client.get("/api/ollama/perf")
    assert r.status_code == 200
    d = r.get_json()
    assert d["current"]["OLLAMA_MAX_LOADED_MODELS"] == "3"  # 구 코드: "1"
    assert d["effective"]["OLLAMA_MAX_LOADED_MODELS"]["shadowed"]["value"] == "1"


def test_post_perf_read_only_is_409_not_500(client, ollama_env):
    dropin, _ = ollama_env
    os.chmod(dropin, stat.S_IRUSR)
    try:
        with patch.object(ro.subprocess, "run") as run:
            # UI가 다루는 키(PERF_OPTIONS)여야 한다 — 목록 밖 키는 '변경 없음'으로 무시된다
            r = client.post("/api/ollama/perf", json={"OLLAMA_KV_CACHE_TYPE": "f16"})
        assert r.status_code == 409
        assert r.get_json()["error"] == "read_only"
        assert _systemctl_calls(run) == []
        assert client.get("/api/ollama/perf").get_json()["writable"] is False
    finally:
        os.chmod(dropin, stat.S_IRUSR | stat.S_IWUSR)


def test_post_perf_shadowed_key_is_refused(client, ollama_env):
    """쓸 수 있어도 EnvironmentFile에 있는 키는 drop-in에 써 봐야 가려진다(무성 무효 금지)."""
    with patch.object(ro.subprocess, "run"):
        r = client.post("/api/ollama/perf", json={"OLLAMA_MAX_LOADED_MODELS": "2"})
    assert r.status_code == 409
    assert "OLLAMA_MAX_LOADED_MODELS" in r.get_json()["keys"]


def test_post_perf_same_as_effective_is_no_change(client, ollama_env):
    """화면은 실제값(3)을 보여 주므로 그대로 저장하면 '변경 없음'이어야 한다."""
    with patch.object(ro.subprocess, "run") as run:
        r = client.post("/api/ollama/perf", json={"OLLAMA_MAX_LOADED_MODELS": "3"})
    assert r.status_code == 200
    assert r.get_json()["restart_required"] is False
    assert _systemctl_calls(run) == []


def test_post_perf_write_preserves_non_ollama_lines(client, ollama_env):
    dropin, _ = ollama_env
    with patch.object(ro.subprocess, "run"):
        r = client.post("/api/ollama/perf", json={"OLLAMA_KV_CACHE_TYPE": "q8_0"})
    assert r.status_code == 200 and r.get_json()["restart_required"] is True
    text = dropin.read_text()
    assert 'Environment="CUDA_VISIBLE_DEVICES=0"' in text
    assert "LimitNOFILE=65536" in text
    assert 'Environment="OLLAMA_KV_CACHE_TYPE=q8_0"' in text
    assert text.count("[Service]") == 1


# ── D2: 시크릿 마스킹 ───────────────────────────────────────────────

@pytest.fixture
def mem_config(monkeypatch):
    cfg = {
        "auth_enabled": True, "local_bypass": True, "default_model": "qwen2.5:14b",
        "s3_secret_key": "SECRET-abcdefghijklmnop", "datago_api_key": "dg-1234567890abcd",
        "apple_worker_token": "tok-zzzzzzzzzzzz9999", "admin_password_hash": "pbkdf2:xxxxxxxxxxxx",
        "alert_slack_url": "https://hooks.slack.com/services/T0/B0/abcdWXYZ",
        "alert_discord_url": None,
        "brand_new_signing_secret": "should-be-masked-by-pattern",
    }
    monkeypatch.setattr(ro, "_load_config", lambda: dict(cfg))
    monkeypatch.setattr(ro, "_save_config", lambda c: (cfg.clear(), cfg.update(c)))
    return cfg


SECRET_VALUES = ("SECRET-abcdefghijklmnop", "dg-1234567890abcd", "tok-zzzzzzzzzzzz9999",
                 "pbkdf2:xxxxxxxxxxxx", "hooks.slack.com", "should-be-masked-by-pattern")


def test_get_config_masks_secrets(client, mem_config):
    r = client.get("/api/config")
    body = r.get_data(as_text=True)
    for v in SECRET_VALUES:
        assert v not in body, v
    d = r.get_json()
    assert d["alert_slack_url"] == {"set": True, "hint": "…WXYZ"}
    assert d["alert_discord_url"] == {"set": False}
    # 화면이 실제로 읽는 비시크릿 값은 그대로
    assert d["default_model"] == "qwen2.5:14b" and d["auth_enabled"] is True and d["local_bypass"] is True


def test_post_config_response_masked_and_mask_roundtrip_ignored(client, mem_config):
    masked = client.get("/api/config").get_json()
    r = client.post("/api/config", json={
        "alert_slack_url": masked["alert_slack_url"],   # 마스크 객체를 그대로 돌려보냄
        "alert_webhook_url": "",                         # 빈 칸 = 변경 없음
        "apple_worker_token": None,
        "default_model": "qwen2.5:7b",
    })
    assert r.status_code == 200
    assert "hooks.slack.com" not in r.get_data(as_text=True)
    assert mem_config["alert_slack_url"] == "https://hooks.slack.com/services/T0/B0/abcdWXYZ"
    assert mem_config["apple_worker_token"] == "tok-zzzzzzzzzzzz9999"
    assert mem_config["default_model"] == "qwen2.5:7b"


def test_post_config_clear_and_set_secret(client, mem_config):
    client.post("/api/config", json={"alert_slack_url": {"clear": True},
                                     "alert_discord_url": "https://discord.com/api/webhooks/1/new"})
    assert mem_config["alert_slack_url"] is None
    assert mem_config["alert_discord_url"] == "https://discord.com/api/webhooks/1/new"


# ── D3/0b: 보안 완화는 localhost만 ──────────────────────────────────

def test_external_session_cannot_disable_auth(client, mem_config):
    r = client.post("/api/config", json={"auth_enabled": False}, headers=EXTERNAL)
    assert r.status_code == 403 and r.get_json()["error"] == "local_only"
    assert mem_config["auth_enabled"] is True


def test_external_session_cannot_enable_bypass(client, mem_config):
    mem_config["local_bypass"] = False
    r = client.post("/api/config", json={"local_bypass": True}, headers=EXTERNAL)
    assert r.status_code == 403
    assert mem_config["local_bypass"] is False


def test_external_session_can_tighten(client, mem_config):
    r = client.post("/api/config", json={"auth_enabled": True, "local_bypass": False}, headers=EXTERNAL)
    assert r.status_code == 200
    assert mem_config["local_bypass"] is False


def test_external_session_resave_unchanged_is_allowed(client, mem_config):
    """화면은 두 값을 함께 보낸다 — 이미 켜진 bypass를 그대로 보내는 것은 완화가 아니다."""
    r = client.post("/api/config", json={"auth_enabled": True, "local_bypass": True}, headers=EXTERNAL)
    assert r.status_code == 200


def test_localhost_can_disable_auth(client, mem_config):
    r = client.post("/api/config", json={"auth_enabled": False})
    assert r.status_code == 200
    assert mem_config["auth_enabled"] is False


# ── Phase 0c (D6): 관리 API는 역할 admin만 ──────────────────────────────────

def _as_role(client, role):
    with client.session_transaction() as s:
        s["web_logged_in"] = True
        s["web_user"] = "someone"
        if role is None:
            s.pop("web_role", None)
        else:
            s["web_role"] = role


@pytest.mark.parametrize("role", ["operator", "viewer", None])
def test_non_admin_session_cannot_use_admin_api(client, mem_config, role):
    """예전엔 로그인만 하면 관리자였다 — operator가 config·키 발급·재시작 가능(역할표는 호출 0)."""
    _as_role(client, role)
    assert client.get("/api/config", headers=EXTERNAL).status_code == 403
    assert client.post("/api/keys", json={"name": "x"}, headers=EXTERNAL).status_code == 403
    assert client.post("/api/config", json={"default_model": "x"}, headers=EXTERNAL).status_code == 403
    assert mem_config["default_model"] == "qwen2.5:14b"


def test_admin_session_can_use_admin_api(client, mem_config):
    _as_role(client, "admin")
    assert client.get("/api/config", headers=EXTERNAL).status_code == 200


def test_localhost_is_admin_regardless_of_session(client, mem_config):
    _as_role(client, "viewer")
    assert client.get("/api/config").status_code == 200   # 로컬(서버 자신·스크립트)은 기존대로


# ── Phase 3: 설정 개요(고급 설정 표·접속 정보) ────────────────────────────────

def test_config_overview_masks_and_reports_code_defaults(client, mem_config):
    r = client.get("/api/config/overview")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    for v in SECRET_VALUES:
        assert v not in body, v
    d = r.get_json()
    rows = {k["key"]: k for k in d["keys"]}
    assert set(rows) == set(mem_config)                       # 전 키
    assert rows["s3_secret_key"]["secret"] is True and rows["s3_secret_key"]["value"]["set"] is True
    assert "True" in rows["auth_enabled"]["defaults"]         # DEFAULT_CONFIG·코드에서 뽑은 값
    assert rows["auth_enabled"]["edit"] == "설정 › 계정·보안"
    assert rows["brand_new_signing_secret"]["edit"] == "config.json 직접"
    assert "ollama_base" in d["connection"] and "bind" in d["connection"]


def test_config_defaults_scan_finds_literal_defaults():
    found = ro._scan_config_defaults()
    assert "7" in found.get("image_retention_days", [])      # routes/image 등의 .get("image_retention_days", 7)
    assert all(isinstance(v, list) for v in found.values())


def test_config_overview_admin_only(client, mem_config):
    _as_role(client, "operator")
    assert client.get("/api/config/overview", headers=EXTERNAL).status_code == 403
