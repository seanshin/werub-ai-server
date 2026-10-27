"""전체 API 표면 품질 계약 — 2026-08-31 전수 감사에서 나온 축을 고정한다.

감사 결과(591 오퍼레이션 / 561 경로):
  - **미선언 태그 381건(64%)** — Swagger UI는 미선언 태그를 설명 없이 **알파벳순으로 뒤에**
    붙인다. 연동자가 표면을 찾지 못하는 주된 이유였다.
  - **`@require_auth`인데 `security` 누락 17건** — 문서를 보고 키 없이 붙였다가 401을 맞는다.
    문서가 코드보다 관대하면 연동자가 잘못된 전제로 간다.
  - `responses` 없음 144건 · 응답 schema 전무 174건.
  - 🔴 `/api/version`이 **98KB**(changelog 91건을 항상 포함) — 버전만 확인하려는 연동·헬스체크
    호출에 그 페이로드가 매번 나갔다.

이 파일이 고정하는 것:
  ① 모든 응답이 **자기 버전을 말한다**(`X-API-Version`/`X-API-Contract`) — 장애 신고 시
     "그 응답을 낸 버전"을 사후에 알 수 있어야 한다.
  ② 서비스 빌드 버전과 **API 계약 버전을 분리**한다(배포마다 계약이 바뀌는 것처럼 보이면 안 된다)
  ③ `/api/version` 기본 응답에 changelog가 **없다**(명시 요청 시에만)
  ④ `@require_auth` ↔ Swagger `security` **정합**
  ⑤ 태그 선언 커버리지
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import api_version as av


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _routes_sources() -> dict:
    out = {}
    for fn in sorted(os.listdir(os.path.join(ROOT, "routes"))):
        if fn.endswith(".py"):
            out[fn] = _read(f"routes/{fn}")
    return out


# ── ① 응답이 자기 버전을 말한다 ─────────────────────────────────────────────
def test_version_headers_shape():
    h = av.headers("2.125.29")
    assert h["X-API-Version"] == "2.125.29"
    assert h["X-API-Contract"] == av.API_CONTRACT


def test_build_version_and_contract_are_separate_axes():
    """🔑 배포마다 오르는 빌드 버전과 호환성 경계를 한 숫자로 섞으면
    연동자가 매 배포마다 호환성을 의심하게 된다."""
    h = av.headers("9.9.9")
    assert h["X-API-Version"] != h["X-API-Contract"]
    assert re.fullmatch(r"\d+", av.API_CONTRACT), "계약 버전은 단순 정수(경계 표시)"


def test_after_request_hook_is_registered():
    src = _read("app.py")
    assert "def _api_version_headers(response):" in src
    assert "_api_version_mod.headers(VERSION)" in src


def test_headers_are_cors_exposed():
    """CORS로 노출 선언이 없으면 브라우저 XHR에서 **조용히 안 보인다**."""
    src = _read("app.py")
    block = src[src.index("def _api_version_headers"):src.index("def _security_headers")]
    assert "Access-Control-Expose-Headers" in block
    for k in ("X-API-Version", "X-API-Contract", "X-API-Commit"):
        assert k in block


def test_version_hook_never_blocks_the_response():
    """헤더 부착 실패가 응답을 막으면 안 된다."""
    src = _read("app.py")
    block = src[src.index("def _api_version_headers"):src.index("def _security_headers")]
    assert "except Exception" in block and "return response" in block


def test_git_commit_does_not_shell_out():
    """🔴 요청 경로에서 subprocess를 띄우면 gthread 풀을 잡아먹는다(레포 규율).

    ⚠️ 문자열 검사는 **docstring의 설명 문구까지 잡는다**(첫 판에서 자기 문서에 걸려
    실패했다) — 실제 호출부만 보도록 코드 라인만 검사한다.
    """
    src = _read("core/api_version.py")
    code = "\n".join(ln for ln in src.split("\n")
                     if not ln.lstrip().startswith("#"))
    # docstring 블록 제거
    parts = code.split('"""')
    code = "".join(parts[::2])
    for bad in ("subprocess", "os.system", "os.popen"):
        assert bad not in code, f"{bad} 사용 금지"


def test_git_commit_is_cached_and_fail_open(monkeypatch):
    monkeypatch.setattr(av, "_resolved", False, raising=False)
    monkeypatch.setattr(av, "_commit", None, raising=False)
    monkeypatch.setattr(av, "_BASE_DIR", "/nonexistent-path-for-test")
    assert av.git_commit() is None          # .git 없어도 예외 없음
    h = av.headers("1.2.3")
    assert "X-API-Commit" not in h          # 없으면 아예 안 싣는다(빈 값 금지)


# ── ③ /api/version 페이로드 ────────────────────────────────────────────────
def test_version_endpoint_omits_changelog_by_default():
    """🔴 98KB를 기본으로 돌려주던 것 — 버전 확인 호출에 매번 나갔다."""
    src = _read("routes/ollama.py")
    block = src[src.index('def api_version():'):src.index("def get_config():")]
    assert 'request.args.get("changelog"' in block, "명시 요청 시에만 실어야 한다"
    idx_cl = block.index('body["changelog"] = _CHANGELOG')
    idx_gate = block.index('request.args.get("changelog"')
    assert idx_gate < idx_cl, "게이트 뒤에서만 changelog를 붙인다"


def test_version_endpoint_exposes_contract_and_discovery():
    src = _read("routes/ollama.py")
    block = src[src.index('def api_version():'):src.index("def get_config():")]
    for key in ('"api_contract"', '"endpoints"', '"docs"', '"openapi"',
                '"medical_catalog"'):
        assert key in block, key


def test_admin_ui_still_requests_changelog():
    """기본을 요약으로 바꾸면서 changelog를 렌더하는 화면을 안 고치면 **빈 화면**이 된다."""
    # app.py는 v2.125.66에서 뺐다 — 거기 있던 건 관리 화면 인라인 사본(삭제됨)이었다. 서빙 파일은 main.js뿐.
    assert "/api/version?changelog=true" in _read("static/js/main.js")


def test_endpoint_count_is_cached():
    """url_map 순회를 요청마다 돌면 낭비다(기동 후 불변)."""
    src = _read("routes/ollama.py")
    assert "_API_ENDPOINT_COUNT" in src and "if _API_ENDPOINT_COUNT is None" in src


# ── ④ 인증 문서 정합 ────────────────────────────────────────────────────────
_ROUTE_DEF = re.compile(
    r'@\w+\.route\(\s*"([^"]+)"[^)]*\)((?:\s*@\w[\w\.]*(?:\([^)]*\))?)*)\s*\ndef \w+\([^)]*\):\s*\n(\s*""".*?""")',
    re.S)


def _auth_doc_mismatches() -> list:
    bad = []
    for fn, src in _routes_sources().items():
        for m in _ROUTE_DEF.finditer(src):
            path, decos, doc = m.group(1), m.group(2), m.group(3)
            if "require_auth" not in decos:
                continue
            if "---" not in doc:          # Swagger 미문서화는 별도 축
                continue
            if "security:" not in doc:
                bad.append((fn, path))
    return bad


def test_require_auth_routes_declare_security():
    """🔴 문서가 코드보다 관대하면 연동자는 키 없이 붙였다가 401을 맞는다.

    2026-08-31 감사에서 17건(coding.py 16 + medical.py 1)이 여기 걸렸다.
    """
    bad = _auth_doc_mismatches()
    assert not bad, "security 누락: " + ", ".join(f"{f}:{p}" for f, p in bad[:20])


# ── ⑤ 태그 선언 ────────────────────────────────────────────────────────────
def _declared_tags() -> set:
    src = _read("app.py")
    block = src[src.index('"tags": ['):src.index("swagger = Swagger(app")]
    return set(re.findall(r'\{"name": "([^"]+)"', block))


def _used_tags() -> set:
    used = set()
    for src in list(_routes_sources().values()) + [_read("app.py")]:
        for m in re.finditer(r"^\s*tags: \[([^\]]+)\]\s*$", src, re.M):
            for t in m.group(1).split(","):
                used.add(t.strip())
    return used


def test_all_used_tags_are_declared():
    """미선언 태그는 /docs 뒤편에 설명 없이 붙는다 — 연동자가 못 찾는다.

    2026-08-31 감사 시점: 사용 87 / 선언 33 ⇒ 미선언 54개(381 오퍼레이션).
    """
    missing = sorted(_used_tags() - _declared_tags())
    assert not missing, f"미선언 태그 {len(missing)}개: {missing[:25]}"


def test_declared_tags_all_have_descriptions():
    src = _read("app.py")
    block = src[src.index('"tags": ['):src.index("swagger = Swagger(app")]
    # 태그 선언은 한 줄/여러 줄 두 형식이 섞여 있다 — 개행을 허용해 대조한다
    # (첫 판에서 한 줄 정규식만 써서 여러 줄 의료 태그를 '설명 없음'으로 오판했다).
    names = re.findall(r'\{"name": "([^"]+)"', block)
    with_desc = re.findall(r'\{"name": "([^"]+)",\s*"description": "[^"]+"\}', block,
                           re.S)
    assert set(names) == set(with_desc), \
        f"설명 없는 태그: {sorted(set(names) - set(with_desc))}"


def test_medical_catalog_tag_leads_the_list():
    src = _read("app.py")
    block = src[src.index('"tags": ['):src.index("swagger = Swagger(app")]
    assert re.findall(r'\{"name": "([^"]+)"', block)[0] == "Medical - Catalog"


# ── responses 보강(감사에서 가장 큰 결손 덩어리였던 coding.py) ──────────────
def test_coding_routes_document_auth_and_validation_failures():
    """200만 적어 두면 연동자가 401/400 분기를 안 짠다.

    문서화한 상태 코드는 **코드에서 확인한 것만**이다 —
    `core.security.require_auth`가 401 `{"error":"unauthorized"}`,
    핸들러가 400 `{"error":"... is required"}`를 낸다.
    """
    src = _read("routes/coding.py")
    n_routes = src.count("@coding_bp.route")
    assert src.count("      401:") >= 13, "401 문서화 부족"
    assert src.count("      400:") >= 13, "400 문서화 부족"
    assert n_routes == src.count("@require_auth"), "coding 라우트는 전부 인증 대상"


# ── responses / description 커버리지 (2026-08-31 감사 후속) ─────────────────
def test_high_traffic_paths_have_prose_descriptions():
    """실호출 상위 경로 = 실제 연동 표면. 한 줄 요약만 있으면 연동자가 코드를 읽어야 한다.

    대상은 `api_activity` 실측(2026-08-31)에서 뽑았다 — 추측한 목록이 아니다.
    """
    targets = [
        ("routes/writing.py", "/api/translate"),
        ("routes/writing.py", "/api/writing/draft"),
        ("routes/counseling.py", "/api/counseling/chat"),
        ("routes/korean.py", "/api/korean/spellcheck"),
        ("routes/ollama.py", "/api/generate"),
        ("routes/medical_advanced.py", "/api/medical/dur/check"),
        ("routes/medical_law.py", "/api/medical-law/ask"),
    ]
    missing = []
    for rel, path in targets:
        src = _read(rel)
        i = src.find(f'"{path}"')
        assert i >= 0, f"{path} 경로 없음"
        seg = src[i:i + 3000]
        j = seg.find('"""')
        body = seg[j + 3:seg.find("---", j)]
        if not [ln for ln in body.split("\n")[1:] if ln.strip()]:
            missing.append(path)
    assert not missing, f"설명 없는 고빈도 경로: {missing}"


def test_counseling_chat_documents_the_crisis_flag():
    """🔴 위기 플래그를 문서에 안 적으면 연동 화면이 그냥 텍스트로 렌더한다."""
    src = _read("routes/counseling.py")
    i = src.index('"/api/counseling/chat"')
    seg = src[i:i + 2500]
    assert "is_crisis" in seg and "1393" in seg


def test_generate_documents_raw_passthrough_contract():
    """X6에서 확정한 계약(raw 통과)이 스펙에도 있어야 한다 —
    문서에 없으면 다음 사람이 '한국어 필터가 빠졌다'고 오인한다."""
    src = _read("routes/ollama.py")
    i = src.index('"/api/generate"')
    seg = src[i:i + 2500]
    assert "raw" in seg and "test_generate_raw_passthrough" in seg
