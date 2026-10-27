"""메인 메뉴 그룹화 회귀 — admin-menu-settings-cleanup Phase 1.

렌더된 관리 화면에서: 내부 탭 12개가 각각 정확히 한 번 메뉴에 있고 패널과 짝이 맞는가,
새 창 링크 7개가 빠지지 않았는가(GPU 템플릿 분기), 새 창 링크가 내부 탭 클래스(.tab)를 쓰지 않는가.
"""
import re
from pathlib import Path

TABS = {"dashboard", "queue", "models", "chat", "generate", "knowledge", "image",
        "activity", "settings", "logs", "training", "users"}
EXT_LINKS = {"admin-console/medical/dashboard/", "admin-console/hr-law/chat/", "admin-console/game/dashboard/",
             "medical-training", "drug-pipeline", "model-manager", "orchestration-editor"}


def _render(app, is_imac=False):
    """관리 화면 템플릿을 직접 렌더 — `/`는 로그인 확인에서 302가 날 수 있고, 그때 인라인 폴백 HTML이
    돌아와 새 템플릿을 검사하지 못한다(처음 이 테스트가 그렇게 헛돌았다)."""
    from flask import render_template
    with app.test_request_context("/"):
        return render_template("admin.html", user_script="", server_title="t",
                               is_imac=is_imac, app_version="test")


def _nav(html):
    m = re.search(r'<nav class="tabs" id="mainNav">(.*?)</nav>', html, re.S)
    assert m, "mainNav 없음"
    return m.group(1)


def test_menu_tabs_match_panels(app):
    html = _render(app)
    nav = _nav(html)
    tabs = re.findall(r'data-tab="([a-z]+)"', nav)
    assert sorted(tabs) == sorted(TABS)            # 각 탭 정확히 한 번
    for t in TABS:
        assert f'id="panel-{t}"' in html, t        # 메뉴 항목마다 패널 존재
        assert f"switchTab('{t}')" in nav


def test_menu_external_links_kept_and_distinct(app):
    nav = _nav(_render(app, is_imac=False))
    hrefs = set(re.findall(r'<a [^>]*href="([^"]+)"', nav))
    assert hrefs == EXT_LINKS                                  # GPU: 7개 전부
    imac = set(re.findall(r'<a [^>]*href="([^"]+)"', _nav(_render(app, is_imac=True))))
    assert imac == {"model-manager", "orchestration-editor"}   # iMac: 공통 2개만
    for a in re.findall(r'<a [^>]*>', nav):
        assert 'class="tab ' not in a and 'class="tab"' not in a, a   # 새 창 링크는 .tab-ext
        assert 'target="_blank"' in a and 'rel="noopener"' in a


def test_template_source_has_all_external_links():
    """템플릿 원본 기준으로 7개 전부(렌더 환경의 is_imac 분기와 무관하게)."""
    src = Path(__file__).resolve().parents[2].joinpath("templates/admin.html").read_text()
    nav = re.search(r'<nav class="tabs" id="mainNav">(.*?)</nav>', src, re.S).group(1)
    assert set(re.findall(r'href="([^"]+)"', nav)) == EXT_LINKS


def test_switchtab_uses_exact_match_and_hash():
    js = Path(__file__).resolve().parents[2].joinpath("static/js/main.js").read_text()
    assert "querySelector(`.tab[onclick*=" not in js   # 부분 문자열 매칭 제거(주석 속 언급은 허용)
    assert "name === 'hrlaw'" not in js and "name === 'game'" not in js   # 죽은 분기 제거
    assert "hashchange" in js and "_tabFromHash" in js


# ── Phase 0c (D6): 역할 권한 — 메뉴 숨김은 실제 템플릿에서 ─────────────────────

def test_operator_menu_shows_only_allowed_tabs(app):
    """예전 숨김은 인라인 폴백 HTML에만 적용되고 render_template 결과에 덮여 무효였다."""
    from flask import render_template
    with app.test_request_context("/"):
        html = render_template("admin.html", user_script="", server_title="t", is_imac=False,
                               app_version="test", allowed_tabs=["dashboard", "training"])
    nav = _nav(html)
    assert sorted(re.findall(r'data-tab="([a-z]+)"', nav)) == ["dashboard", "training"]
    assert 'data-group="admin"' not in nav and 'data-group="ops"' not in nav and 'data-group="tools"' not in nav
    assert 'data-group="models"' in nav     # Training이 들어 있는 그룹은 남는다


def test_switchtab_refuses_tabs_missing_from_menu():
    js = Path(__file__).resolve().parents[2].joinpath("static/js/main.js").read_text()
    assert "function _tabAllowed" in js and "if (!_tabAllowed(name)) name = 'dashboard'" in js


# ── Phase 2: Settings 6개 절 + 설정 이관 ─────────────────────────────────────

SECTIONS = ["security", "engine", "alerts", "storage", "integration", "system"]


def _sections(html):
    out = {}
    for m in re.finditer(r'<section class="settings-section" data-section="([a-z]+)">(.*?)</section>', html, re.S):
        out[m.group(1)] = m.group(2)
    return out


def _panel(html, name):
    a = html.index(f'id="panel-{name}"')
    b = html.find('id="panel-', a + 10)
    return html[a:b if b > 0 else len(html)]


def test_settings_has_six_sections_with_matching_nav(app):
    html = _render(app)
    secs = _sections(html)
    assert list(secs) == SECTIONS
    nav = re.findall(r'class="settings-nav-item" data-section="([a-z]+)"', html)
    assert nav == SECTIONS


def test_settings_cards_live_in_the_right_section(app):
    secs = _sections(_render(app))
    where = {
        "security": ['id="pwCurrent"', 'id="cfgAuthEnabled"', 'id="keysTable"'],
        "engine": ['id="rsSelect"', 'id="rsToggle"', 'id="cfgDefaultModel"', 'id="initCountry"', 'id="perfSettings"'],
        "alerts": ['id="cfgSlack"', 'id="settingsAlertSummary"'],
        "storage": ['id="imgRetentionDays"', 'id="settingsS3Status"'],
        "_hardcoded_gone": [],
        "integration": ['id="settingsConnection"'],
        "system": ['id="versionCard"', 'id="cfgOverviewTable"'],
    }
    where.pop("_hardcoded_gone")
    assert "localhost:11434" not in secs["integration"]   # 접속 정보 하드코딩 제거(Phase 3)
    for sec, needles in where.items():
        for n in needles:
            assert n in secs[sec], (sec, n)


def test_moved_controls_left_only_status_and_link(app):
    html = _render(app)
    dash, models, settings = _panel(html, "dashboard"), _panel(html, "models"), _panel(html, "settings")
    # 대시보드 상주 카드: 상태는 남고 조작부는 Settings로
    assert 'id="residencyCard"' in dash and 'id="rsPolicy"' in dash
    assert 'id="rsToggle"' not in dash and "openSettings('engine')" in dash
    # Models: 기본 모델·초기화 폼은 없고 이관 안내 링크만
    assert 'id="cfgDefaultModel"' not in models and 'id="initCountry"' not in models
    assert "openSettings('engine')" in models
    # 헤더와 중복이던 로그아웃 카드 제거
    assert "/logout" not in settings
    # 버전 배지 → 시스템 정보 절
    assert "openSettings('system')" in html


def test_settings_js_routes_sections():
    js = Path(__file__).resolve().parents[2].joinpath("static/js/main.js").read_text()
    for needle in ("function showSettingsSection", "function openSettings", "_settingsSectionFromHash",
                   "'#settings/' + sec"):
        assert needle in js, needle
    # openSettings로 지정한 절이 현재 hash보다 우선(라이브에서 #settings/engine → 배지가 system으로 못 가던 결함)
    assert "showSettingsSection(_pendingSettingsSection || _settingsSectionFromHash()" in js
    assert "loadInitConfig(); }" not in js.split("function switchTab")[1].split("function closeTabGroups")[0]


class _Nesting:
    """브라우저 파서처럼 `</div>`를 **가장 가까운 열린 div까지** 닫아(사이의 section도 함께) 부모를 기록한다.

    🔴 v2.125.59가 '접속 정보' 카드를 고치며 `</div>` 하나를 남겼다 — 절 안의 여분 `</div>`는 무시되지 않고
    바깥 `settings-body`를 닫는다. 그 결과 '시스템 정보' 절이 소메뉴 옆 셋째 칸으로 밀려났고, 패널 끝의
    `</div>`들이 하나씩 밀려 **`.container`까지 닫혀** 뒤의 패널 6개(knowledge~logs)가 폭 제한·여백 없이 렌더됐다.
    화면만 틀어지고 오류는 0이라(클래스 #1과 같은 무성) 브라우저 확인에서도 지나갔다.
    """
    def __init__(self, html):
        from html.parser import HTMLParser
        stack, self.parent = [], {}
        outer = self

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                if tag not in ("div", "section"):
                    return
                d = dict(attrs)
                key = d.get("id") or (d.get("data-section") and "section:" + d["data-section"]) or d.get("class", "")
                if key.startswith(("panel-", "section:")):
                    outer.parent[key] = stack[-1][1] if stack else None
                stack.append((tag, d.get("class", "") if not d.get("id") else d["id"]))

            def handle_endtag(self, tag):
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k][0] == tag:
                        del stack[k:]
                        return
        P().feed(html)


def test_panels_and_settings_sections_nest_correctly(app):
    nest = _Nesting(_render(app))
    for t in TABS:
        assert nest.parent.get("panel-" + t) == "container", t
    for sec in ("security", "engine", "alerts", "storage", "integration", "system"):
        assert nest.parent.get("section:" + sec) == "settings-body", sec


def _fn(js, name):
    """main.js에서 함수 본문 하나를 잘라 낸다(다음 최상위 function/const 선언까지)."""
    i = js.index(name)
    m = re.search(r"\n(?:async )?function |\nconst |\nlet |\nwindow\.", js[i + len(name):])
    return js[i:i + len(name) + (m.start() if m else len(js))]


def test_settings_unsaved_guard_wired():
    """미저장 변경 경고 — 떠나는 두 길(탭 전환·절 전환)과 창 닫기가 모두 묻고, 기준값을 로더와 저장 성공이 기록한다.

    ⚠️ 기준값이 기록되지 않은 칸은 **조용히 추적 밖**이다 — 그래서 로더·저장 지점마다 검사한다.
    """
    js = Path(__file__).resolve().parents[2].joinpath("static/js/main.js").read_text()
    assert "settingsDirtyCards(curSec)" in _fn(js, "function switchTab(")
    ss = _fn(js, "function showSettingsSection(")
    assert "settingsDirtyCards(leaving)" in ss and "if (leaving === sec) return;" in ss
    assert "window.addEventListener('beforeunload'" in js
    # 취소 시 주소 복원(뒤로 가기로 hash만 바뀌고 화면은 그대로 남는 어긋남 방지)
    assert js.count("history.replaceState(null, '', '#settings/' + ") >= 2
    for loader, needle in (("async function loadSettings(", "markSettingsClean(['cfgAuthEnabled'"),
                           ("async function saveSettings(", "markSettingsClean(['cfgAuthEnabled'"),
                           ("async function loadDefaultModelConfig(", "markSettingsClean(['cfgDefaultModel'])"),
                           ("async function loadInitConfig(", "markSettingsClean(_INIT_FIELDS)"),
                           ("async function saveInitConfig(", "markSettingsClean(_INIT_FIELDS)"),
                           ("async function loadPerfSettings(", "markSettingsClean("),
                           ("async function applyPerfSettings(", "markSettingsClean("),
                           ("async function loadImageStorage(", "markSettingsClean(_IMG_FIELDS)"),
                           ("async function saveImageSettings(", "markSettingsClean(_IMG_FIELDS)")):
        assert needle in _fn(js, loader), loader
    # 비밀번호 칸은 추적하지 않는다(자동완성이 가짜 경고를 만든다)
    assert "pwCurrent" not in _fn(js, "const _settingsBaseline")


def test_image_storage_zero_means_unlimited_in_ui():
    """0(무제한)을 `||`로 받으면 7일·500개·2GB로 보이고, 그 화면에서 다시 저장하면 한도가 되살아나 이미지가 지워진다."""
    js = Path(__file__).resolve().parents[2].joinpath("static/js/main.js").read_text()
    body = _fn(js, "async function loadImageStorage(")
    for k in ("image_retention_days", "image_max_count", "image_max_size_mb"):
        assert f"cfg.{k} ??" in body and f"cfg.{k} ||" not in body, k


# ── v2.125.66: 템플릿 렌더 실패는 로그 + 500 (낡은 인라인 사본으로 조용히 떨어지지 않는다) ─────────

def _login(client):
    import time
    with client.session_transaction() as s:
        s["web_logged_in"] = True
        s["web_user"] = "admin"
        s["web_last_active"] = time.time()


def test_index_serves_template_not_inline(app, client):
    _login(client)
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'id="settingsNav"' in html            # 템플릿에만 있는 요소(Phase 2)
    import app as A
    assert not hasattr(A, "HTML_TEMPLATE")        # 낡은 사본은 삭제됐다


def test_index_render_failure_is_500_and_logged(app, client, monkeypatch, caplog):
    """예전엔 `except Exception: pass` → 로그 없이 3,744줄 낡은 인라인 화면(고친 결함이 되살아난 화면)."""
    import flask
    def boom(*a, **k):
        raise RuntimeError("template broken")
    monkeypatch.setattr(flask, "render_template", boom)
    _login(client)
    with caplog.at_level("ERROR"):
        r = client.get("/")
    assert r.status_code == 500
    assert "관리 화면을 표시할 수 없습니다" in r.get_data(as_text=True)
    assert any("[admin-ui]" in rec.getMessage() for rec in caplog.records)
