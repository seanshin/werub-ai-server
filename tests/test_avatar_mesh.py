"""core.avatar_mesh 순수/검증 단위테스트 (GPU·TotalSegmentator 불요).

결과 파싱 + submit 입력 검증(SSRF·로컬차단). 세그 파이프라인 자체는 GPU에서 E2E.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import avatar_mesh as am  # noqa: E402


def test_parse_result_last_json_line():
    out = "TotalSegmentator log...\nprogress 50%\n" + '{"ok": true, "faces": 7260, "organs": ["lung_upper_lobe_left"]}'
    r = am._parse_result(out)
    assert r["ok"] is True and r["faces"] == 7260


def test_parse_result_none_on_garbage():
    assert am._parse_result("no json here\njust logs") is None
    assert am._parse_result("") is None


def test_submit_requires_pixels_url():
    assert am.submit("")["error"]
    assert am.submit(None)["error"]


def test_submit_rejects_non_public_url():
    # http(비https)·사설 IP 리터럴 거부(요청핸들러 정적 SSRF)
    assert am.submit("http://example.com/ct.nii.gz").get("error")
    assert am.submit("https://10.0.0.5/ct.nii.gz").get("error")
    assert am.submit("https://127.0.0.1/ct.nii.gz").get("error")


def test_submit_rejects_local_path_by_default():
    # 로컬 경로는 allow_local_input=false(기본) 시 거부
    r = am.submit("/etc/hostname")
    assert r.get("error")


def test_defaults_fast_mode_and_paths():
    assert am._DEFAULTS["allow_full"] is False     # OOM 회피 기본
    assert am._DEFAULTS["mode"] if "mode" in am._DEFAULTS else True
    assert "ts_poc_venv" in am._DEFAULTS["ts_python"]


def test_supported_rois_includes_heart_chambers():
    # P2: 심혈관 세분 ROI 추가(라이선스 게이트), 기존 무라이선스 ROI 불변
    assert "heart_chambers" in am.SUPPORTED_ROIS
    for r in ("lung", "heart", "kidney", "liver"):
        assert r in am.SUPPORTED_ROIS


def test_script_heart_chambers_task_mapping():
    # 격리 venv 스크립트의 ROI_TASKS — heartchambers_highres(라이선스) 매핑·라벨
    import importlib.util
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "avatar_organ_mesh.py")
    spec = importlib.util.spec_from_file_location("avatar_organ_mesh_t", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)     # 모듈 로드(totalsegmentator import는 main() 내부라 무관)
    assert mod.ROI_TASKS["heart_chambers"]["task"] == "heartchambers_highres"
    assert "heart_myocardium" in mod.ROI_TASKS["heart_chambers"]["labels"]
    assert mod.ROI_LABELS["heart"] == ["heart"]   # 무라이선스 whole-heart 경로 불변
