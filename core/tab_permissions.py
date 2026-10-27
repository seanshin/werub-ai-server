"""관리 콘솔 탭별 권한 — 웹 세션의 비관리 역할(operator·viewer)에만 적용.

역할표(`routes/training.py::ROLE_DEFAULTS`)는 오랫동안 **화면 숨김에만** 쓰였고 서버는 강제하지 않았다
(관리자급 API는 v2.125.56에서 역할 admin으로 좁혔다). 이 모듈은 나머지 — 탭 고유 API — 를 탭 권한으로 막는다.

설계(2026-09-29 UI 호출 지도 기준 · admin-menu-settings-cleanup):
- **공통 읽기는 막지 않는다.** 헤더·대시보드는 탭과 무관하게 10초~5분마다 `/api/status`·`/api/queue/status`·
  카드별 상태 GET을 부른다 — 이걸 막으면 허용된 탭에서도 화면이 깨진다. 규칙에 없는 경로는 통과(기존 동작).
- 여러 탭이 공유하는 경로는 **허용 탭 중 하나라도** 있으면 통과(예: 이미지 보기 = image 또는 queue).
- `ADMIN_ONLY`는 비관리 역할에 항상 거부 — 대시보드에 붙어 있지만 서버 제어인 것(Ollama 시작·정지, 모델 반납·예열).
- 로컬 요청·Bearer API 키·admin 역할은 이 모듈을 거치지 않는다(호출부가 거른다).
"""

ADMIN_ONLY = "__admin__"

# (메서드 집합 또는 None=전부, 경로 접두사, 필요한 탭: None=공통 | str | tuple(하나라도) | ADMIN_ONLY)
# **먼저 맞는 규칙이 이긴다** — 구체적인 경로를 위에 둔다.
_RULES = (
    ({"GET"}, "/api/queue/status", None),               # 헤더·대시보드 공통
    (None, "/api/queue/job/", ("queue", "image")),       # 이미지 생성 폴링
    (None, "/api/queue/pause", "queue"),
    (None, "/api/queue/resume", "queue"),
    (None, "/api/queue/", "queue"),
    (None, "/api/chat", "chat"),
    (None, "/api/generate", "generate"),
    (None, "/api/rag/", "knowledge"),
    (None, "/api/image/view/", ("image", "queue")),
    ({"GET"}, "/api/image/sana/status", None),           # 대시보드 카드
    (None, "/api/image/", "image"),
    (None, "/api/activity", "activity"),
    (None, "/api/logs", "logs"),
    (None, "/api/alerts/", "logs"),
    (None, "/api/training/", "training"),
    (None, "/api/mod/", "training"),
    (None, "/api/models/init", "models"),
    (None, "/api/models/unload", ADMIN_ONLY),
    (None, "/api/models/warm", ADMIN_ONLY),
    (None, "/api/service/", ADMIN_ONLY),
)


def required_tabs(method, path):
    """이 요청에 필요한 탭. None=공통(통과) · tuple=그중 하나 · ADMIN_ONLY."""
    m = (method or "GET").upper()
    for methods, prefix, tabs in _RULES:
        if (methods is None or m in methods) and path.startswith(prefix):
            if tabs is None or tabs == ADMIN_ONLY:
                return tabs
            return tabs if isinstance(tabs, tuple) else (tabs,)
    return None


def denied_reason(method, path, role, permissions):
    """거부 사유(허용이면 None). permissions = {탭: bool}(로그인 때 세션에 실린 값)."""
    if role == "admin":
        return None
    need = required_tabs(method, path)
    if need is None:
        return None
    if need == ADMIN_ONLY:
        return "admin_only"
    perms = permissions if isinstance(permissions, dict) else {}
    if any(perms.get(t) is True for t in need):
        return None
    return "tab:" + "|".join(need)
