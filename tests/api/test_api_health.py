"""API 헬스체크 + 기본 엔드포인트 테스트."""
import json
import pytest


class TestHealthEndpoints:
    """서버 상태 확인 API"""

    def test_version(self, client):
        """GET /api/version — 버전 + 체인지로그"""
        r = client.get("/api/version")
        assert r.status_code == 200
        data = r.get_json()
        assert "version" in data
        assert data["version"].startswith("2.")

    def test_config_get(self, client):
        """GET /api/config — 설정 조회"""
        r = client.get("/api/config")
        assert r.status_code == 200

    def test_openapi_spec(self, client):
        """GET /apispec.json — Swagger 스펙"""
        r = client.get("/apispec.json")
        assert r.status_code == 200
        data = r.get_json()
        assert "paths" in data or "swagger" in data


class TestAdminPages:
    """관리 페이지 렌더링 확인"""

    def test_main_admin(self, client):
        """GET / — 메인 어드민 (로그인 필요 시 302)"""
        r = client.get("/", follow_redirects=True)
        assert r.status_code == 200

    def test_login_page(self, client):
        """GET /login — 로그인 페이지"""
        r = client.get("/login")
        assert r.status_code == 200

    def test_model_manager(self, client):
        """GET /model-manager — 로그인 필요 시 302"""
        r = client.get("/model-manager", follow_redirects=True)
        assert r.status_code == 200

    def test_orchestration_editor(self, client):
        """GET /orchestration-editor — 로그인 필요 시 302"""
        r = client.get("/orchestration-editor", follow_redirects=True)
        assert r.status_code == 200

    def test_drug_pipeline(self, client):
        """GET /drug-pipeline — 약물 파이프라인"""
        r = client.get("/drug-pipeline")
        assert r.status_code == 200
