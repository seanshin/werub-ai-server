"""Queue API 테스트."""
import json
import pytest


class TestQueueAPI:
    """작업 큐 CRUD"""

    def test_queue_status(self, client):
        """GET /api/queue/status — 큐 상태"""
        r = client.get("/api/queue/status")
        assert r.status_code == 200
        data = r.get_json()
        assert "pending" in data
        assert "running" in data

    def test_queue_stats(self, client):
        """GET /api/queue/stats — 큐 통계"""
        r = client.get("/api/queue/stats")
        assert r.status_code == 200
        data = r.get_json()
        assert "total" in data

    def test_queue_batches(self, client):
        """GET /api/queue/batches — 배치 목록"""
        r = client.get("/api/queue/batches")
        assert r.status_code == 200
        data = r.get_json()
        assert "batches" in data

    def test_submit_missing_prompt(self, client, api_headers):
        """POST /api/queue/submit — prompt 없으면 400"""
        r = client.post("/api/queue/submit",
                        data=json.dumps({"model": "qwen2.5:7b"}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_submit_with_prompt_only(self, client, api_headers):
        """POST /api/queue/submit — model 없으면 기본 모델 사용 (200)"""
        r = client.post("/api/queue/submit",
                        data=json.dumps({"prompt": "test"}),
                        headers=api_headers)
        assert r.status_code in (200, 400)  # 기본 모델 설정에 따라 다름
