"""Pytest fixtures for WeRU.B AI Server API tests."""
import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Flask 앱은 한 번만 로드 (import 비용 절감), 테스트 클라이언트는 매번 새로 생성
_app_instance = None
_mock_patcher = None


def _get_app():
    """Flask 앱 싱글턴 (mock된 requests.Session 포함)"""
    global _app_instance, _mock_patcher
    if _app_instance is None:
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": "테스트 응답입니다.",
            "eval_count": 10,
            "done": True,
        }
        mock_response.iter_lines.return_value = []
        mock_response.status_code = 200
        mock_session.post.return_value = mock_response
        mock_session.get.return_value = mock_response

        os.environ["TESTING"] = "1"
        _mock_patcher = patch("requests.Session", return_value=mock_session)
        _mock_patcher.start()

        from app import app as flask_app
        flask_app.config["TESTING"] = True
        flask_app.config["SECRET_KEY"] = "test-secret"
        _app_instance = flask_app
    return _app_instance


@pytest.fixture
def app():
    """Flask 앱 인스턴스 — 매 테스트마다 상태 초기화"""
    flask_app = _get_app()
    yield flask_app


@pytest.fixture
def client(app):
    """Flask 테스트 클라이언트 — 매 테스트마다 새로 생성"""
    with app.test_client() as c:
        # 웹 로그인 세션 설정 (require_web_login bypass)
        with c.session_transaction() as sess:
            sess["web_logged_in"] = True
            sess["web_user"] = "test_admin"
            sess["web_role"] = "admin"
            sess["web_permissions"] = json.dumps({
                "medical": True, "training": True, "keys": True,
                "queue": True, "logs": True, "image": True,
                "settings": True, "ollama": True, "rag": True,
                "users": True,
            })
        yield c


@pytest.fixture
def api_headers():
    """API 호출용 기본 헤더"""
    return {"Content-Type": "application/json"}
