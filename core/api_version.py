"""API 버전 식별 — 응답이 "어느 서버가 답했는가"를 스스로 말하게 한다.

왜(2026-08-31 전체 API 감사): 연동 시스템이 장애를 신고할 때 **어느 버전이 그 응답을
냈는지** 알 방법이 없었다. `/api/version`을 따로 부르면 그건 *지금* 버전이지 문제의 응답을
낸 버전이 아니다(그 사이 배포가 있었을 수 있다). 그래서 **모든 응답에 헤더로 실는다**.

두 종류를 구분한다:
- `X-API-Version` = **서비스 빌드 버전**(app.VERSION). 배포마다 올라간다. 버그 신고·재현용.
- `X-API-Contract` = **API 계약 버전**. 요청/응답 스키마의 호환성 경계이며,
  하위 호환이 깨질 때만 올린다. 연동 코드가 분기해야 하는 축은 이쪽이다.

빌드 버전이 올랐다고 계약이 바뀐 것은 아니다 — 둘을 한 숫자로 섞으면 연동자가 매 배포마다
호환성을 의심하게 된다.
"""
from __future__ import annotations

import os
import threading

# API 계약 버전 — **하위 호환이 깨질 때만** 올린다(경로 삭제·필수 필드 추가·응답 형태 변경).
# 엔드포인트 추가나 선택 필드 추가는 계약을 깨지 않으므로 올리지 않는다.
API_CONTRACT = "1"

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_lock = threading.Lock()
_commit: str | None = None
_resolved = False


def git_commit(short: bool = True) -> str | None:
    """HEAD 커밋 해시. **subprocess를 쓰지 않는다** — 요청 경로에서 프로세스를 띄우면
    gthread 풀을 잡아먹는다(이 레포의 블로킹 금지 규율). `.git`을 직접 읽고 결과를 캐시한다.
    `.git`이 없는 배포본에서는 None(장애가 아니다)."""
    global _commit, _resolved
    with _lock:
        if _resolved:
            return _commit[:7] if (_commit and short) else _commit
        _resolved = True
        try:
            head = os.path.join(_BASE_DIR, ".git", "HEAD")
            with open(head, encoding="utf-8") as f:
                ref = f.read().strip()
            if ref.startswith("ref:"):
                path = os.path.join(_BASE_DIR, ".git", ref.split(" ", 1)[1].strip())
                with open(path, encoding="utf-8") as f:
                    _commit = f.read().strip()
            else:
                _commit = ref
        except Exception:  # noqa: BLE001
            _commit = None
    return _commit[:7] if (_commit and short) else _commit


def headers(service_version: str) -> dict:
    """모든 응답에 실을 버전 헤더."""
    h = {"X-API-Version": service_version, "X-API-Contract": API_CONTRACT}
    c = git_commit()
    if c:
        h["X-API-Commit"] = c
    return h
