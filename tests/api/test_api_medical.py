"""Medical AI API 테스트."""
import json
import pytest


class TestMedicalAPI:
    """의료 AI 엔드포인트"""

    def test_triage_departments(self, client):
        """GET /api/medical/triage/departments — 진료과 목록"""
        r = client.get("/api/medical/triage/departments")
        assert r.status_code == 200
        data = r.get_json()
        assert "departments" in data

    def test_triage_common_symptoms(self, client):
        """GET /api/medical/triage/common-symptoms — 자주 묻는 증상"""
        r = client.get("/api/medical/triage/common-symptoms")
        assert r.status_code == 200
        data = r.get_json()
        assert "symptoms" in data

    def test_summarize_missing_text(self, client, api_headers):
        """POST /api/medical/summarize — text 없으면 400 or 401 (테넌트 인증)"""
        r = client.post("/api/medical/summarize",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code in (400, 401)  # 테넌트 인증 필요 시 401

    def test_analyze_missing_symptoms(self, client, api_headers):
        """POST /api/medical/analyze — symptoms 없으면 400 or 401"""
        r = client.post("/api/medical/analyze",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code in (400, 401)

    def test_models_list(self, client):
        """GET /api/medical/models — 의료 모델 레지스트리"""
        r = client.get("/api/medical/models")
        assert r.status_code == 200
        data = r.get_json()
        assert "models" in data

    def test_models_routing(self, client):
        """GET /api/medical/models/routing — 도메인→모델 라우팅"""
        r = client.get("/api/medical/models/routing")
        assert r.status_code == 200
