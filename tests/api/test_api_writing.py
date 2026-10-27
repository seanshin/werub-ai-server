"""Writing AI API 테스트."""
import json
import pytest


class TestWritingAPI:
    """글쓰기 AI 엔드포인트"""

    def test_tones(self, client):
        """GET /api/writing/tones — 톤/구조/길이 프리셋"""
        r = client.get("/api/writing/tones")
        assert r.status_code == 200
        data = r.get_json()
        assert "tones" in data
        assert "structures" in data
        assert "lengths" in data

    def test_models(self, client):
        """GET /api/writing/models — 글쓰기 모델 목록"""
        r = client.get("/api/writing/models")
        assert r.status_code == 200
        data = r.get_json()
        assert "models" in data
        assert "default" in data

    def test_languages(self, client):
        """GET /api/writing/languages — 번역 언어 목록"""
        r = client.get("/api/writing/languages")
        assert r.status_code == 200
        data = r.get_json()
        assert "languages" in data
        assert len(data["languages"]) == 7

    def test_draft_missing_prompt(self, client, api_headers):
        """POST /api/writing/draft — prompt 없으면 400"""
        r = client.post("/api/writing/draft",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_draft_success(self, client, api_headers, monkeypatch):
        """POST /api/writing/draft — 정상 호출

        ⚠️ 생성을 고정한다. 종전에는 실제 LLM 가용성에 기대어 있었고, 생성이 비면
        **빈 초안을 200으로** 받아 그대로 통과했다 — 즉 무성 실패 위에서 초록이었다
        (2026-08-14, 반환 경로 가드를 넣자 502로 드러남).
        """
        from routes import writing as W
        monkeypatch.setattr(W, "_generate", lambda *a, **k: (
            "AI 기술 트렌드는 빠르게 변하고 있습니다. 올해는 특히 소형 모델과 "
            "온디바이스 추론이 주목받고 있습니다.", 120, 900))
        r = client.post("/api/writing/draft",
                        data=json.dumps({"prompt": "AI 기술 트렌드에 대해 작성해주세요"}),
                        headers=api_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("draft"), "빈 초안은 성공이 아니다"
        assert "tokens" in data

    def test_draft_collapsed_output_is_502(self, client, api_headers, monkeypatch):
        """생성이 붕괴하면 200이 아니라 502 — 무성 실패 금지.

        실측(2026-08-14): 첫 생성이 비한국어면 `force_korean`이 통째로 지워
        `'[]\\n[]'`(5자)만 남는데도 200으로 나갔다. `'회사'`(2자)도 마찬가지였다.
        """
        from routes import writing as W
        monkeypatch.setattr(W, "_generate", lambda *a, **k: ("[]\n[]", 273, 900))
        r = client.post("/api/writing/draft",
                        data=json.dumps({"prompt": "사내 건강검진 안내문을 작성해줘"}),
                        headers=api_headers)
        assert r.status_code == 502
        assert "error" in r.get_json()

    def test_polish_missing_text(self, client, api_headers):
        """POST /api/writing/polish — text 없으면 400"""
        r = client.post("/api/writing/polish",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_seo_missing_text(self, client, api_headers):
        """POST /api/writing/seo — text 없으면 400"""
        r = client.post("/api/writing/seo",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_seo_response_schema(self, client, api_headers):
        """POST /api/writing/seo — 응답 스키마 보장"""
        r = client.post("/api/writing/seo",
                        data=json.dumps({"text": "테스트 본문입니다. 이것은 SEO 분석을 위한 텍스트입니다."}),
                        headers=api_headers)
        # v2.125.72: 모델이 JSON을 못 만들거나 생성 자체가 실패하면 502다. 종전엔 `"raw" in data`를 허용해
        #   **실패를 200으로 통과시키는 계약**을 이 테스트가 고정하고 있었다(테스트 환경은 생성이 실패해 늘 raw였다).
        if r.status_code == 502:
            assert r.get_json()["code"] in ("GENERATION_FAILED", "MODEL_OUTPUT_UNPARSED")
            return
        assert r.status_code == 200
        data = r.get_json()
        assert "raw" not in data
        assert "meta_description" in data
        assert "keywords" in data
        assert "readability" in data
        assert "suggestions" in data
        assert "tokens" in data

    def test_evaluate_missing_text(self, client, api_headers):
        """POST /api/writing/evaluate — text 없으면 400"""
        r = client.post("/api/writing/evaluate",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_translate_missing_params(self, client, api_headers):
        """POST /api/writing/translate — 필수 파라미터 검증"""
        # text 없음
        r = client.post("/api/writing/translate",
                        data=json.dumps({"target": "en"}),
                        headers=api_headers)
        assert r.status_code == 400

        # target 없음
        r = client.post("/api/writing/translate",
                        data=json.dumps({"text": "안녕하세요"}),
                        headers=api_headers)
        assert r.status_code == 400

        # 잘못된 target
        r = client.post("/api/writing/translate",
                        data=json.dumps({"text": "안녕하세요", "target": "xx"}),
                        headers=api_headers)
        assert r.status_code == 400

    def test_translate_success(self, client, api_headers):
        """POST /api/writing/translate — 정상 번역"""
        r = client.post("/api/writing/translate",
                        data=json.dumps({"text": "안녕하세요", "target": "en"}),
                        headers=api_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert "translation" in data
        assert data["target_lang"] == "en"

    def test_translate_contract_missing_params(self, client, api_headers):
        """POST /api/translate — text/targets 필수 검증 + 미지원 언어 400"""
        # text 없음
        r = client.post("/api/translate",
                        data=json.dumps({"targets": ["en"]}), headers=api_headers)
        assert r.status_code == 400
        # targets 없음
        r = client.post("/api/translate",
                        data=json.dumps({"text": "안녕하세요"}), headers=api_headers)
        assert r.status_code == 400
        # 미지원 target
        r = client.post("/api/translate",
                        data=json.dumps({"text": "안녕하세요", "targets": ["xx"]}), headers=api_headers)
        assert r.status_code == 400

    def test_translate_contract_shape(self, client, api_headers):
        """POST /api/translate — 계약 응답 shape {translation:{lang}, ms}"""
        r = client.post("/api/translate",
                        data=json.dumps({"text": "환자가 고혈압이 있습니다", "source": "ko",
                                         "targets": ["en"], "domain": "medical", "style": "literal"}),
                        headers=api_headers)
        assert r.status_code == 200
        data = r.get_json()
        assert isinstance(data.get("translation"), dict)
        assert "en" in data["translation"]
        assert isinstance(data.get("ms"), int)
        assert data["domain"] == "medical" and data["style"] == "literal"

    def test_translate_contract_multi_target(self, client, api_headers):
        """POST /api/translate — 다중 targets + 단일 'target' 호환"""
        r = client.post("/api/translate",
                        data=json.dumps({"text": "안녕하세요", "targets": ["en", "ja"]}),
                        headers=api_headers)
        assert r.status_code == 200
        tr = r.get_json()["translation"]
        assert set(tr.keys()) == {"en", "ja"}
        # 단일 target 문자열 호환
        r2 = client.post("/api/translate",
                         data=json.dumps({"text": "안녕하세요", "target": "en"}),
                         headers=api_headers)
        assert r2.status_code == 200
        assert "en" in r2.get_json()["translation"]

    def test_clean_translation_strips_commentary(self, app):
        """_clean_translation — 메타 코멘트 제거하되 병기 괄호/한국어 보존"""
        from routes.writing import _clean_translation
        assert _clean_translation("Hello.\n\n(Note: discrepancy here)") == "Hello."
        assert _clean_translation("Tuesday  \n\nCorrected version: ...") == "Tuesday"
        assert _clean_translation('"Bonjour"') == "Bonjour"
        assert _clean_translation("It is next Tuesday. (Note: ambiguous)") == "It is next Tuesday."
        # 병기 괄호·한국어 '참고/주의' 보존(오절단 금지)
        assert _clean_translation("고혈압(hypertension)") == "고혈압(hypertension)"
        assert _clean_translation("주의사항을 참고하세요") == "주의사항을 참고하세요"

    def test_script_ok_language_fidelity(self, app):
        """_script_ok — 대상 언어 표기 충실도(가나없는 순중국어를 ja로 거부)"""
        from routes.writing import _script_ok
        assert _script_ok("哪里不舒服？", "ja") is False        # 순중국어 → ja 거부
        assert _script_ok("どこが痛いですか", "ja") is True       # 가나 포함 일본어
        assert _script_ok("今どこが痛いですか", "ja") is True     # 가나+한자 혼합
        assert _script_ok("哪里不舒服？", "zh") is True           # 중국어 → zh 정상
        assert _script_ok("どこ", "zh") is False                  # 가나 → zh 거부
        assert _script_ok("안녕하세요", "ko") is True
        assert _script_ok("Hello there", "en") is True
        assert _script_ok("哪里", "en") is False                  # CJK → en 거부
        # ko에 중국어 혼입(한글 시작 후 중국어로 degenerate) → 거부
        assert _script_ok("이 약을 하루 세 번 服用此药，每天三次", "ko") is False
        assert _script_ok("이 약을 하루 세 번 드세요", "ko") is True

    def test_craft_missing_prompt(self, client, api_headers):
        """POST /api/writing/craft — prompt 없으면 400"""
        r = client.post("/api/writing/craft",
                        data=json.dumps({}),
                        headers=api_headers)
        assert r.status_code == 400
