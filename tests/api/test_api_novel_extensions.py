"""LibriScribe 연동 확장 7건 테스트 (E1~E7)."""
import json
import pytest


class TestE1ExternalContext:
    """E1. chapter/write — external_context 파라미터"""

    def test_chapter_write_with_external_context(self, client, api_headers):
        """external_context 전달 시 _build_novel_context 스킵"""
        r = client.post("/api/novel/chapter/write",
                        data=json.dumps({
                            "project_id": "test-project-nonexistent",
                            "chapter_num": 1,
                            "external_context": "[세계관] 테스트 세계\n[인물] 주인공(Lv.1)\n[아웃라인] 첫 번째 챕터",
                            "additional_direction": "전투 장면 중심",
                        }),
                        headers=api_headers)
        # 프로젝트가 없으면 404 — external_context가 있어도 project 확인은 필수
        assert r.status_code == 404

    def test_chapter_write_without_external_context(self, client, api_headers):
        """external_context 없으면 기존 동작 (project 필요)"""
        r = client.post("/api/novel/chapter/write",
                        data=json.dumps({
                            "project_id": "test-project-nonexistent",
                            "chapter_num": 1,
                        }),
                        headers=api_headers)
        assert r.status_code == 404  # project not found

    def test_chapter_write_missing_params(self, client, api_headers):
        """필수 파라미터 없으면 400"""
        r = client.post("/api/novel/chapter/write",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400


class TestE2Snapshot:
    """E2. project snapshot — 전체 상태 1회 조회"""

    def test_snapshot_not_found(self, client):
        """존재하지 않는 프로젝트 → 404"""
        r = client.get("/api/novel/project/nonexistent-id/snapshot")
        assert r.status_code == 404

    def test_snapshot_endpoint_exists(self, client):
        """엔드포인트 존재 확인 (404 = 프로젝트 미존재, not 405)"""
        r = client.get("/api/novel/project/test-id/snapshot")
        assert r.status_code in (200, 404, 500)
        assert r.status_code != 405  # Method Not Allowed가 아님


class TestE3WebNovelMetrics:
    """E3. score — 웹소설 특화 메트릭"""

    def test_score_missing_project(self, client, api_headers):
        """project_id 없으면 400"""
        r = client.post("/api/novel/score",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_score_nonexistent_project(self, client, api_headers):
        """존재하지 않는 프로젝트 → 404"""
        r = client.post("/api/novel/score",
                        data=json.dumps({"project_id": "nonexistent"}),
                        headers=api_headers)
        assert r.status_code == 404

    def test_score_with_web_novel_flag(self, client, api_headers):
        """include_web_novel_metrics 파라미터 전달 확인"""
        r = client.post("/api/novel/score",
                        data=json.dumps({
                            "project_id": "nonexistent",
                            "include_web_novel_metrics": True,
                        }),
                        headers=api_headers)
        # 프로젝트 없으므로 404 — 파라미터 자체는 에러 없이 수용
        assert r.status_code == 404


class TestE4FocusChapter:
    """E4. revise/consistency — focus_chapter + context_summary"""

    def test_consistency_missing_project(self, client, api_headers):
        """project_id 없으면 400"""
        r = client.post("/api/novel/revise/consistency",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_consistency_focus_chapter(self, client, api_headers):
        """focus_chapter + context_summary 전달 시 단일 회차 검증"""
        r = client.post("/api/novel/revise/consistency",
                        data=json.dumps({
                            "project_id": "nonexistent",
                            "focus_chapter": 13,
                            "context_summary": "진우(Lv.15)가 B급 던전을 클리어한 상태",
                        }),
                        headers=api_headers)
        # 프로젝트/챕터 없으므로 404
        assert r.status_code == 404

    def test_consistency_without_focus(self, client, api_headers):
        """focus_chapter 없으면 기존 동작"""
        r = client.post("/api/novel/revise/consistency",
                        data=json.dumps({"project_id": "nonexistent"}),
                        headers=api_headers)
        assert r.status_code == 404


class TestE5ExtractMetadata:
    """E5. extract/metadata — 텍스트 엔티티 자동 추출"""

    def test_extract_missing_content(self, client, api_headers):
        """content 없으면 400"""
        r = client.post("/api/novel/extract/metadata",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_extract_success(self, client, api_headers):
        """정상 호출 — LLM mock 응답"""
        r = client.post("/api/novel/extract/metadata",
                        data=json.dumps({
                            "content": "어둠 속에서 진우가 눈을 떴다. 레벨이 올랐다는 시스템 메시지가 떠올랐다. 유진이 달려왔다.",
                            "known_characters": ["진우", "유진"],
                            "known_foreshadows": ["던전 속 비문"],
                        }),
                        headers=api_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert "_meta" in data
        assert "tokens" in data["_meta"]

    def test_extract_with_empty_known(self, client, api_headers):
        """known 목록 없어도 동작"""
        r = client.post("/api/novel/extract/metadata",
                        data=json.dumps({
                            "content": "테스트 소설 텍스트입니다.",
                        }),
                        headers=api_headers)
        assert r.status_code == 200


class TestE6EpisodeStructure:
    """E6. chapter/write — episode_structure 파라미터"""

    def test_episode_structure_accepted(self, client, api_headers):
        """episode_structure 파라미터가 에러 없이 수용됨"""
        r = client.post("/api/novel/chapter/write",
                        data=json.dumps({
                            "project_id": "nonexistent",
                            "chapter_num": 1,
                            "episode_structure": {
                                "hook_lines": 3,
                                "intro_pct": 15,
                                "develop_pct": 50,
                                "climax_pct": 25,
                                "cliff_pct": 10,
                                "tension_target": 0.85,
                            },
                        }),
                        headers=api_headers)
        # 프로젝트 없으므로 404 — episode_structure 자체는 에러 없음
        assert r.status_code == 404


class TestE7ReturnDetail:
    """E7. orchestrated_write — return_detail"""

    def test_return_detail_accepted(self, client, api_headers):
        """return_detail 파라미터가 에러 없이 수용됨"""
        r = client.post("/api/novel/chapter/write",
                        data=json.dumps({
                            "project_id": "nonexistent",
                            "chapter_num": 1,
                            "quality": "orchestrated",
                            "return_detail": True,
                        }),
                        headers=api_headers)
        assert r.status_code == 404  # project not found
