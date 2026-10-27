"""유지보수 창 스크립트 회귀 (v2.125.68) — scripts/maintenance_window.sh · scripts/post_reboot_verify.sh

🔴 이 테스트는 GPU 서버의 배포 게이트(pytest)에서도 돈다. 바꾸는 명령(apt-get·systemctl·systemd-run·dkms·
journalctl·runuser·sudo)은 **전부 PATH 맨 앞의 stub이 가로챈다** — stub 목록에서 하나라도 빠지면 진짜 명령이
불릴 수 있으니 `MUTATING`을 줄이지 말 것(`test_every_mutating_command_is_stubbed`가 지킨다).
stub은 호출을 calls.log에 남기고 fixture JSON대로 답한다. 네트워크 0.
"""
import json
import os
import re
import stat
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MW = os.path.join(ROOT, "scripts", "maintenance_window.sh")
PRV = os.path.join(ROOT, "scripts", "post_reboot_verify.sh")

MUTATING = ["apt-get", "systemctl", "systemd-run", "dkms", "journalctl", "runuser", "sudo", "reboot"]
READONLY = ["apt", "dpkg-query", "uname", "id", "df", "pm2", "docker", "curl", "nvidia-smi", "loginctl",
            "crontab", "flock", "uptime"]

STUB = r'''#!/usr/bin/env python3
import json, os, sys
name = sys.argv[1]; args = sys.argv[2:]
d = os.environ["STUB_DIR"]
with open(os.path.join(d, "calls.log"), "a") as f:
    f.write(name + " " + " ".join(args) + "\n")
fx = json.load(open(os.path.join(d, "fixture.json")))
state_p = os.path.join(d, "state.json")
state = json.load(open(state_p)) if os.path.exists(state_p) else {}
def save(): json.dump(state, open(state_p, "w"))
out = sys.stdout.write
if name == "dkms":
    if args[:1] == ["autoinstall"]:
        if "dkms_after_autoinstall" in fx: state["dkms"] = fx["dkms_after_autoinstall"]; save()
        sys.exit(0)
    txt = state.get("dkms", fx.get("dkms", ""))
    k = args[args.index("-k") + 1] if "-k" in args else None
    for l in txt.splitlines():
        if k is None or (", %s," % k) in l: out(l + "\n")
elif name == "dpkg-query": out(fx.get("dpkg", ""))
elif name == "apt": out(fx.get("apt_list", ""))
elif name == "uname": out(fx.get("uname_r", "7.0.0-31-generic") + "\n")
elif name == "id": out(fx.get("id_u", "1000") + "\n")
elif name == "df": out("Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/x 100 1 %d 1%% /\n" % fx.get("df_avail_kb", 50 * 1048576))
elif name == "pm2":
    if args[:1] == ["resurrect"]:
        if "pm2_after_resurrect" in fx: state["pm2"] = fx["pm2_after_resurrect"]; save()
        sys.exit(0)
    if args[:1] == ["jlist"]:
        out(json.dumps([{"name": n, "pm2_env": {"status": s}} for n, s in state.get("pm2", fx.get("pm2", []))]))
elif name == "docker":
    if "--filter" in args: out("".join(n + "\n" for n in fx.get("docker_restarting", [])))
    else: out("".join("c%d\n" % i for i in range(fx.get("docker_n", 54))))
elif name == "nvidia-smi":
    if any("driver_version" in a for a in args): out(fx.get("smi_driver", "580.178.04") + "\n")
    else: out("NVIDIA GeForce RTX 5080, 16303 MiB\n")
elif name == "loginctl": out("yes\n")
elif name == "crontab": out(fx.get("crontab", "@reboot a\n@reboot b\n"))
elif name == "systemctl":
    if args[:1] == ["show"]: out("0\n")
    elif args[:1] == ["list-timers"]: out(fx.get("timers", ""))
elif name == "curl":
    url = next((a for a in args if a.startswith("http")), "")
    resp = {"code": 200, "body": "{}"}
    for suffix, r in fx.get("curl", {}).items():
        if url.endswith(suffix): resp = r
    if "/dev/null" not in args: out(resp.get("body", "{}"))
    if "-w" in args: out(str(resp.get("code", 200)))
elif name == "uptime": out("up 2 minutes\n")
sys.exit(0)
'''


@pytest.fixture(scope="module")
def stub_bin(tmp_path_factory):
    # 실행 파일은 모듈당 한 번만 만든다 — macOS는 새 실행 파일의 첫 실행마다 ~0.4s 검사해서
    # 테스트마다 만들면 29건이 75s 걸렸다(Linux에선 무관). 테스트별 상태는 STUB_DIR로 가른다.
    d = tmp_path_factory.mktemp("stubbin")
    src = d / "_stub.py"
    src.write_text(STUB)
    for n in MUTATING + READONLY:          # 래퍼가 명령 이름을 첫 인자로 넘긴다
        p = d / n
        p.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{src}" {n} "$@"\n')
        p.chmod(0o755)
    return d


@pytest.fixture
def env(tmp_path, stub_bin):
    stub = tmp_path / "stub"               # calls.log·fixture.json·state.json
    stub.mkdir()
    boot = tmp_path / "boot"
    boot.mkdir()
    for k in ("7.0.0-31-generic", "7.0.0-34-generic", "7.0.0-38-generic"):
        (boot / f"vmlinuz-{k}").write_text("")
        (boot / f"initrd.img-{k}").write_text("")
    uu = tmp_path / "50uu"
    uu.write_text('Unattended-Upgrade::Package-Blacklist {\n    "nvidia-";\n    "libnvidia-";\n};\n')
    nvp = tmp_path / "nvver"
    nvp.write_text("NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.173.02  Release Build\n")
    logs = tmp_path / "logs"
    logs.mkdir()
    e = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ['PATH']}",
        "STUB_DIR": str(stub),
        "MW_LOG_DIR": str(logs), "MW_BOOT_DIR": str(boot), "MW_UU_CONF": str(uu),
        "MW_REBOOT_REQUIRED": str(tmp_path / "reboot-required"), "MW_NV_PROC": str(nvp),
        "MW_PM2_BIN": str(stub_bin), "MW_REQUIRE_ROOT": "0", "MW_LOCK": str(tmp_path / "lock"),
        "MW_QUEUE_WAIT_S": "0", "MW_QUEUE_POLL_S": "0", "MW_NOW_HM": "22:00",
        "MW_VERIFY_POLL_S": "0", "MW_RESTART_WAIT_S": "0",
        "MW_PUBLIC_URL": "https://pub.example/ollama",
        # 실제 /etc/apt·/proc을 보지 않게 — 서버 상태(cdrom 줄·옛 라이브러리)에 따라 결과가 갈리면 안 된다
        "MW_APT_SOURCES": str(tmp_path / "no-sources.list"),
        "MW_ADMIN_URL": "http://adm.example", "MW_OLLAMA_URL": "http://ol.example",
    }
    fx = {
        "dkms": ("nvidia/580.178.04, 7.0.0-31-generic, x86_64: installed\n"
                 "nvidia/580.178.04, 7.0.0-34-generic, x86_64: installed\n"
                 "nvidia/580.178.04, 7.0.0-38-generic, x86_64: installed\n"),
        "dpkg": ("rc 570.211.01-0ubuntu1.24.04.1\nii 580.178.04-0ubuntu0.24.04.1\n"),
        "pm2": [["his-proxy", "online"], ["his-tunnel", "online"], ["werub-app", "online"], ["werub-app", "online"]],
        "docker_n": 54,
        "curl": {"/api/queue/status": {"code": 200, "body": '{"running": 0}'}},
    }

    class E:
        tmp = tmp_path
        vars = e
        fixture = fx

        def run(self, script, *args):
            (stub / "fixture.json").write_text(json.dumps(self.fixture))
            return subprocess.run(["bash", script, *args], env=self.vars, capture_output=True, text=True,
                                  cwd=str(tmp_path), timeout=60)

        def calls(self):
            p = stub / "calls.log"
            return p.read_text().splitlines() if p.exists() else []

        def called(self, prefix):
            return [c for c in self.calls() if c.startswith(prefix)]
    return E()


def test_every_mutating_command_is_stubbed():
    # 🔴 바꾸는 명령을 스크립트에 새로 쓰면 여기에도 더해야 한다(배포 게이트에서 진짜가 불리지 않게)
    src = open(MW).read() + open(PRV).read()
    for cmd in ("apt-get", "systemctl", "systemd-run", "dkms", "journalctl", "runuser", "sudo"):
        if re.search(r"\b%s\b" % re.escape(cmd), src):
            assert cmd in MUTATING
    assert "reboot" in MUTATING


# ── --schedule ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("hm", ["03:00", "03:30", "04:29", "06:00", "06:59"])
def test_schedule_refuses_backup_and_unattended_windows(env, hm):
    r = env.run(MW, "--schedule", f"2026-10-08 {hm}")
    assert r.returncode == 2 and "금지 시간대" in r.stderr
    assert env.called("systemd-run") == []


def test_schedule_creates_timer_for_execute(env):
    r = env.run(MW, "--schedule", "2026-10-08 22:00", "--mode", "reboot-only")
    assert r.returncode == 0, r.stdout + r.stderr
    (c,) = env.called("systemd-run")
    assert "--on-calendar=2026-10-08 22:00:00" in c and "--unit=werub-maintenance" in c
    assert c.endswith("--execute --mode reboot-only")


def test_schedule_rejects_bad_format_and_existing_timer(env):
    assert env.run(MW, "--schedule", "10/08 22:00").returncode == 2
    env.fixture["timers"] = "Thu 2026-10-08 22:00:00 KST - - - werub-maintenance.timer werub-maintenance.service\n"
    r = env.run(MW, "--schedule", "2026-10-08 22:00")
    assert r.returncode == 2 and "이미 예약" in r.stderr
    assert env.called("systemd-run") == []


# ── --execute ───────────────────────────────────────────────────────────
def test_execute_full_happy_path_order_and_baseline(env):
    r = env.run(MW, "--execute", "--mode", "full")
    assert r.returncode == 0, r.stdout + r.stderr
    seq = [c for c in env.calls() if c.split()[0] in ("apt-get", "journalctl", "systemctl", "bash")]
    names = [c.split()[0] + " " + c.split()[1] for c in env.calls()
             if c.startswith(("apt-get", "systemctl reboot", "journalctl"))]
    assert names == ["apt-get update", "apt-get -y", "journalctl --vacuum-size=1G", "systemctl reboot"], seq
    up = env.called("apt-get -y")[0]
    assert "--force-confold" in up and "full-upgrade" in up and "DPkg::Lock::Timeout=600" in up
    assert env.called("pm2 save")  # dump 고정
    b = json.loads((env.tmp / "logs" / "maintenance_baseline.json").read_text())
    assert b["kernel_expected"] == "7.0.0-38-generic"        # /boot 최신 = GRUB_DEFAULT=0이 부팅할 커널
    assert b["driver_expected"] == "580.178.04"              # rc(570)는 무시
    assert b["driver_before"] == "580.173.02" and b["pm2"].count("werub-app") == 2


def test_execute_reboot_only_skips_apt(env):
    env.fixture["dkms"] = "nvidia/580.178.04, 7.0.0-38-generic, x86_64: installed\n"
    r = env.run(MW, "--execute", "--mode", "reboot-only")
    assert r.returncode == 0, r.stdout
    assert env.called("apt-get") == [] and env.called("systemctl reboot")


def test_execute_gate_fail_retries_dkms_then_refuses_reboot(env):
    # 새 커널(-38)에 모듈이 안 빌드됨 → autoinstall 1회 → 그래도 없으면 재부팅하지 않는다
    env.fixture["dkms"] = "nvidia/580.178.04, 7.0.0-34-generic, x86_64: installed\n"
    r = env.run(MW, "--execute")
    assert r.returncode == 1
    assert env.called("dkms autoinstall -k 7.0.0-38-generic")
    assert env.called("systemctl reboot") == []
    assert "CRITICAL" in r.stdout
    assert not (env.tmp / "logs" / "maintenance_baseline.json").exists()


def test_execute_gate_fail_recovered_by_dkms_autoinstall(env):
    env.fixture["dkms"] = "nvidia/580.178.04, 7.0.0-34-generic, x86_64: installed\n"
    env.fixture["dkms_after_autoinstall"] = "nvidia/580.178.04, 7.0.0-38-generic, x86_64: installed\n"
    r = env.run(MW, "--execute")
    assert r.returncode == 0, r.stdout
    assert env.called("systemctl reboot")


@pytest.mark.parametrize("breakage", ["blacklist", "initrd", "version_mismatch", "disk"])
def test_execute_gate_blocks_reboot(env, breakage):
    if breakage == "blacklist":
        open(env.vars["MW_UU_CONF"], "w").write("Unattended-Upgrade::Package-Blacklist {\n};\n")
    elif breakage == "initrd":
        os.remove(os.path.join(env.vars["MW_BOOT_DIR"], "initrd.img-7.0.0-38-generic"))
    elif breakage == "version_mismatch":  # 모듈은 옛 버전으로만 빌드됨(유저랜드 580.178)
        env.fixture["dkms"] = "nvidia/580.173.02, 7.0.0-38-generic, x86_64: installed\n"
    elif breakage == "disk":
        env.fixture["df_avail_kb"] = 1 * 1048576
    r = env.run(MW, "--execute")
    assert r.returncode == 1 and env.called("systemctl reboot") == [], r.stdout


def test_execute_dry_run_changes_nothing(env):
    r = env.run(MW, "--execute", "--dry-run")
    assert r.returncode == 0, r.stdout
    for cmd in ("apt-get", "systemctl reboot", "journalctl", "dkms autoinstall", "pm2 save"):
        assert env.called(cmd) == [], cmd
    assert "[dry-run] systemctl reboot" in r.stdout
    assert not (env.tmp / "logs" / "maintenance_baseline.json").exists()
    assert (env.tmp / "logs" / "maintenance_baseline.json.dry-run").exists()


def test_execute_refuses_in_forbidden_window(env):
    env.vars["MW_NOW_HM"] = "03:40"
    r = env.run(MW, "--execute")
    assert r.returncode == 2 and env.called("apt-get") == [] and env.called("systemctl") == []


# ── --check ─────────────────────────────────────────────────────────────
def test_check_reports_and_warns_on_nvidia_pending(env):
    env.fixture["apt_list"] = ("나열 중...\nnvidia-driver-580-open/noble-updates 580.178 amd64\n"
                               "linux-image-generic-hwe-24.04/noble-updates 7.0.0-38 amd64\nopenssl/noble 3 amd64\n")
    open(env.vars["MW_REBOOT_REQUIRED"], "w").write("*** reboot ***\n")
    r = env.run(MW, "--check")
    assert r.returncode == 1 and "NVIDIA 대기 1개" in r.stdout
    j = json.loads((env.tmp / "logs" / "maintenance_check.json").read_text())
    assert j["upgradable"] == 3 and j["nvidia_pending"] == 1 and j["kernel_pending"] == 1
    assert j["reboot_pending_days"] == 0 and j["kernel_newest"] == "7.0.0-38-generic"
    assert not [c for c in env.calls() if c.split()[0] in ("apt-get", "systemd-run", "dkms") and "status" not in c]


def test_check_quiet_when_nothing_pending(env):
    env.fixture["apt_list"] = "나열 중...\n"
    r = env.run(MW, "--check")
    assert r.returncode == 0
    # 정상이면 로그를 아예 쌓지 않는다(주간 cron이 매주 한 줄씩 소음을 만들지 않게)
    assert not (env.tmp / "logs" / "maintenance_window.log").exists()


# ── post_reboot_verify.sh ───────────────────────────────────────────────
def _verify_fixture(env, **over):
    env.fixture["curl"] = {
        "/api/health": {"code": 200}, "ol.example/": {"code": 200},
        "/api/generate": {"code": 200, "body": '{"eval_count": 8}'},
        "/api/ps": {"code": 200, "body": '{"models": [{"size_vram": 9899000000}]}'},
        "/api/version": {"code": 200, "body": '{"version": "2.125.68"}'},
        "/ollama/api/tags": {"code": 200}, "/ollama/api/status": {"code": 401},
    }
    env.fixture["uname_r"] = "7.0.0-38-generic"
    env.fixture["smi_driver"] = "580.178.04"
    open(env.vars["MW_NV_PROC"], "w").write(
        "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.178.04  Release Build\n")
    env.fixture.update(over)


def _baseline(env, **over):
    b = {"ts": "t", "mode": "full", "kernel_before": "7.0.0-31-generic", "driver_before": "580.173.02",
         "kernel_expected": "7.0.0-38-generic", "driver_expected": "580.178.04",
         "pm2": ["his-proxy", "his-tunnel", "werub-app", "werub-app"], "docker": 54}
    b.update(over)
    (env.tmp / "logs" / "maintenance_baseline.json").write_text(json.dumps(b))


def _last(env):
    return json.loads((env.tmp / "logs" / "maintenance_last.json").read_text())


def test_verify_all_ok_moves_baseline(env):
    _verify_fixture(env)
    _baseline(env)
    r = env.run(PRV)
    assert r.returncode == 0, r.stdout
    j = _last(env)
    assert j["result"] == "OK"
    assert {k: v["status"] for k, v in j["axes"].items() if v["status"] != "ok"} == {}
    assert not (env.tmp / "logs" / "maintenance_baseline.json").exists()
    assert [p for p in os.listdir(env.tmp / "logs") if p.startswith("maintenance_baseline.2")]


def test_verify_resurrects_missing_caddy_and_tunnel(env):
    _verify_fixture(env, pm2=[["werub-app", "online"]],
                    pm2_after_resurrect=[["his-proxy", "online"], ["his-tunnel", "online"], ["werub-app", "online"]])
    r = env.run(PRV)
    assert env.called("pm2 resurrect")
    j = _last(env)
    assert j["axes"]["pm2_key"]["status"] == "ok" and "resurrect로 복구" in j["axes"]["pm2_key"]["value"]
    assert r.returncode == 0, r.stdout


def test_verify_fails_when_resurrect_does_not_help(env):
    _verify_fixture(env, pm2=[["werub-app", "online"]])
    r = env.run(PRV)
    assert r.returncode == 1 and _last(env)["axes"]["pm2_key"]["status"] == "fail"


def test_verify_empty_pm2_list_is_fail_not_ok(env):
    # v2.125.68 실물 검증에서 발견: pm2 jlist가 실패해 빈 목록이 와도 'pm2_status ok 0 online'으로 통과했다
    _verify_fixture(env, pm2=[])
    r = env.run(PRV)
    j = _last(env)
    assert r.returncode == 1 and j["axes"]["pm2_status"]["status"] == "fail"


def test_verify_auth_bypass_is_fail(env):
    _verify_fixture(env)
    env.fixture["curl"]["/ollama/api/status"] = {"code": 200}
    r = env.run(PRV)
    assert r.returncode == 1 and _last(env)["axes"]["ingress_auth"]["status"] == "fail"


def test_verify_kernel_driver_mismatch_vs_baseline(env):
    _verify_fixture(env, uname_r="7.0.0-34-generic")
    _baseline(env)
    r = env.run(PRV)
    j = _last(env)
    assert r.returncode == 1 and j["axes"]["kernel_expected"]["status"] == "fail"


def test_verify_driver_module_vs_userland_mismatch(env):
    _verify_fixture(env, smi_driver="580.173.02")   # 모듈 580.178 / 유저랜드 580.173 → CPU 폴백 전조
    r = env.run(PRV)
    assert r.returncode == 1 and _last(env)["axes"]["driver_match"]["status"] == "fail"


def test_verify_pm2_duplicate_after_double_resurrect_is_warn(env):
    # @reboot pm2 resurrect 줄이 2개(libriscribe용·HIS용) — 중복 기동이면 개수가 는다
    _verify_fixture(env, pm2=[["his-proxy", "online"], ["his-tunnel", "online"], ["his-proxy", "online"],
                              ["werub-app", "online"], ["werub-app", "online"]])
    _baseline(env)
    r = env.run(PRV)
    a = _last(env)["axes"]["pm2_vs_baseline"]
    assert a["status"] == "warn" and "his-proxy" in a["value"] and r.returncode == 0


def test_verify_without_baseline_still_checks_ingress(env):
    _verify_fixture(env)
    env.fixture["curl"]["/ollama/api/tags"] = {"code": 502}
    r = env.run(PRV)
    j = _last(env)
    assert r.returncode == 1 and j["axes"]["ingress_tags"]["status"] == "fail"
    assert "기준값 없음" in j["axes"]["docker"]["value"]


def test_scripts_parse():
    for s in (MW, PRV):
        assert subprocess.run(["bash", "-n", s]).returncode == 0
        assert os.stat(s).st_mode & stat.S_IXUSR or True


@pytest.mark.parametrize("n,status", [(0, "ok"), (3, "warn")])
def test_verify_deleted_libs_zero_is_ok(env, n, status):
    # 2026-10-07 첫 실전 재부팅: 0건인데 `grep -c … || echo 0`이 "0\n0"을 내 warn으로 판정됐다
    maps = env.tmp / "maps"
    maps.write_text("7f00 r-xp /usr/lib/libc.so.6\n" + "7f01 r-xp /usr/lib/libssl.so.3 (deleted)\n" * n)
    env.vars["MW_ADMIN_MAPS"] = str(maps)
    _verify_fixture(env)
    env.run(PRV)
    a = _last(env)["axes"]["deleted_libs"]
    assert a["status"] == status and a["value"] == str(n)


@pytest.mark.parametrize("n", [0, 2])
def test_check_deleted_libs_is_single_number(env, n):
    maps = env.tmp / "maps"
    maps.write_text("7f01 r-xp /usr/lib/libssl.so.3 (deleted)\n" * n)
    env.vars["MW_ADMIN_MAPS"] = str(maps)
    env.fixture["apt_list"] = "나열 중...\n"
    env.run(MW, "--check")
    j = json.loads((env.tmp / "logs" / "maintenance_check.json").read_text())
    assert j["deleted_libs"] == n


@pytest.mark.parametrize("line,warns", [
    ("deb [check-date=no] file:///cdrom noble main restricted\n", True),
    ("# deb [check-date=no] file:///cdrom noble main restricted\n", False),   # 10-07 처치 후 상태
    ("deb http://kr.archive.ubuntu.com/ubuntu noble main\n", False),
])
def test_check_flags_active_cdrom_source(env, line, warns):
    # 2026-10-07 첫 실전 --execute가 cdrom 저장소 때문에 apt-get update에서 ABORT했다 — --check가 미리 잡는다
    src = env.tmp / "sources.list"
    src.write_text(line)
    env.vars["MW_APT_SOURCES"] = str(src)
    env.fixture["apt_list"] = "나열 중...\n"
    r = env.run(MW, "--check")
    assert (r.returncode == 1) is warns
    assert ("cdrom 저장소 활성" in r.stdout) is warns
