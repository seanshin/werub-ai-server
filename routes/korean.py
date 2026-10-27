"""Korean Language Processing Blueprint — 한국어 처리 통합 API

전체 한국어 처리 파이프라인을 독립 API로 제공:

  POST /api/korean/pipeline          — 전체 파이프라인 (필터→분석→교정→가독성)
  POST /api/korean/analyze           — AI 패턴 탐지 (22 메트릭)
  POST /api/korean/readability       — 가독성 점수 (5등급)
  POST /api/korean/spellcheck        — 맞춤법 검사 (Ollama LLM)
  POST /api/korean/filter            — 비한국어 제거 (force_korean)
  POST /api/korean/check             — 한국어 비율 검증
  GET  /api/korean/rules             — S1/S2 패턴 규칙 목록
  GET  /api/korean/metrics           — 개별 메트릭 목록 + 설명
  POST /api/korean/metrics/detail    — 개별 메트릭 상세 분석
"""
import hashlib
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Blueprint, request, jsonify
from core.cache import TTLCache
from routes.cache_ctx import RequestAwareTTLCache

from core.korean import force_korean, korean_quality_check
from core.korean_humanize import (
    compute_korean_quality,
    compute_readability,
    compute_cohesion,
    compute_text_stats,
    detect_honorific_level,
    detect_repetition,
    detect_pov,
    analyze_sensory,
    compute_diff,
    analyze_character_voice,
    analyze_expressiveness,
    get_quality_summary,
    get_s1_prompt_rules,
    # v1.6 개별 메트릭
    comma_inclusion_rate,
    comma_usage_rate,
    ending_comma_rate,
    comma_segment_length,
    conclusion_pivot_count,
    safe_balance_count,
    hanja_nominalizer_density,
    lexical_diversity,
    # v2.0 개별 메트릭
    lexical_density,
    ending_diversity,
    normalisation_score,
    da_streak_rate,
    inanimate_subject_rate,
    by_passive_count,
    double_passive_count,
    pronoun_density,
    deul_overuse_rate,
    relative_clause_nesting,
    have_make_literal_count,
    double_particle_count,
    progressive_aspect_rate,
    interference_index,
    VERSION,
)
from core.korean_spellcheck import spellcheck, spellcheck_long, get_spellcheck_summary

logger = logging.getLogger("ollama-admin")

korean_bp = Blueprint("korean", __name__)

# 한국어 분석 캐시 — 동일 텍스트·장르 반복 요청 방지 (TTL 10분)
_korean_cache = RequestAwareTTLCache(maxsize=500, ttl=600)


def _ck(text, *extras):
    """캐시 키 생성 — md5(text + extras)"""
    raw = text + "".join(str(e) for e in extras)
    return hashlib.md5(raw.encode("utf-8", errors="replace")).hexdigest()

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core", "data")

# ---------------------------------------------------------------------------
# 메트릭 레지스트리
# ---------------------------------------------------------------------------

_METRIC_REGISTRY = {
    # v1.6
    "comma_inclusion_rate": {"group": "comma", "desc": "쉼표 포함 문장 비율 (0~1)", "baseline": "인간 26% / AI 61%"},
    "comma_usage_rate": {"group": "comma", "desc": "문장당 평균 쉼표 수", "baseline": "인간 1.13 / AI 2.56"},
    "ending_comma_rate": {"group": "comma", "desc": "연결어미 뒤 쉼표 비율 (★최강 신호 4.84배)", "baseline": "인간 4% / AI 20%"},
    "comma_segment_length": {"group": "comma", "desc": "쉼표 구간 평균 어절수", "baseline": "인간 4.35 / AI 8.56"},
    "conclusion_pivot_count": {"group": "lexicon", "desc": "결산 피벗 어휘 (결론적으로/따라서 등)", "threshold": "3회+ → S1"},
    "safe_balance_count": {"group": "lexicon", "desc": "안전 균형 어휘 (양쪽 모두/신중하게 등)", "threshold": "4회+ → S2"},
    "hanja_nominalizer_density": {"group": "vocabulary", "desc": "한자어 명사화 -성/-적/-화 밀도", "threshold": "12회/문서 → S2"},
    "lexical_diversity": {"group": "vocabulary", "desc": "TTR (고유 토큰 / 전체 토큰)", "baseline": "인간 ~0.65 / AI ~0.55"},
    # v2.0 simplification
    "lexical_density": {"group": "simplification", "desc": "내용어 비율 (접미사·종결 기반)"},
    "ending_diversity": {"group": "simplification", "desc": "종결어미 다양성 (고유/전체)"},
    # v2.0 normalisation
    "normalisation_score": {"group": "normalisation", "desc": "평서형 (~한다/~된다/~이다) 집중도"},
    "da_streak_rate": {"group": "normalisation", "desc": "'-다' 4연속 streak 횟수"},
    # v2.0 interference T1~T8
    "inanimate_subject_rate": {"group": "interference", "desc": "T1: 무생물 주어 + 만능 동사 비율"},
    "by_passive_count": {"group": "interference", "desc": "T2a: ~에 의해 + 피동 동사 수"},
    "double_passive_count": {"group": "interference", "desc": "T2b: 이중 피동 (되어진다 등) 수"},
    "pronoun_density": {"group": "interference", "desc": "T3: 영어 대명사 직역 밀도"},
    "deul_overuse_rate": {"group": "interference", "desc": "T4: 무정물 '-들' 과용 비율"},
    "relative_clause_nesting": {"group": "interference", "desc": "T5: 관형절 깊이 ≥3 문장 수"},
    "have_make_literal_count": {"group": "interference", "desc": "T6: light verb 직역 수"},
    "double_particle_count": {"group": "interference", "desc": "T7: 이중 조사 결합 수"},
    "progressive_aspect_rate": {"group": "interference", "desc": "T8b: '~고 있다' 문장당 비율"},
}

_METRIC_FUNCTIONS = {
    "comma_inclusion_rate": comma_inclusion_rate,
    "comma_usage_rate": comma_usage_rate,
    "ending_comma_rate": ending_comma_rate,
    "comma_segment_length": comma_segment_length,
    "conclusion_pivot_count": conclusion_pivot_count,
    "safe_balance_count": safe_balance_count,
    "hanja_nominalizer_density": hanja_nominalizer_density,
    "lexical_diversity": lexical_diversity,
    "lexical_density": lexical_density,
    "ending_diversity": ending_diversity,
    "normalisation_score": normalisation_score,
    "da_streak_rate": da_streak_rate,
    "inanimate_subject_rate": inanimate_subject_rate,
    "by_passive_count": by_passive_count,
    "double_passive_count": double_passive_count,
    "pronoun_density": pronoun_density,
    "deul_overuse_rate": deul_overuse_rate,
    "relative_clause_nesting": relative_clause_nesting,
    "have_make_literal_count": have_make_literal_count,
    "double_particle_count": double_particle_count,
    "progressive_aspect_rate": progressive_aspect_rate,
}


# ---------------------------------------------------------------------------
# API: 전체 파이프라인 (오케스트레이션)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/pipeline", methods=["POST"])
def korean_pipeline():
    """한국어 전체 처리 파이프라인 — 필터→분석→교정→가독성 일괄 실행
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "처리할 텍스트"}
            genre: {type: string, default: essay}
            steps: {type: array, items: {type: string}, default: ["filter", "analyze", "readability"], description: "실행할 단계 (filter/analyze/spellcheck/readability)"}
    responses:
      200:
        description: 파이프라인 전체 결과
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    genre = data.get("genre", "essay")
    steps = data.get("steps") or ["filter", "analyze", "readability"]
    start = time.time()

    result = {
        "original": text,
        "pipeline_steps": steps,
        "genre": genre,
    }
    processed = text

    # Phase 1: 비한국어 필터 (순차 — 이후 단계가 이 결과에 의존)
    if "filter" in steps:
        filtered = force_korean(text)
        passed, ratio, detail = korean_quality_check(filtered)
        result["filter"] = {
            "text": filtered.strip(),
            "korean_ratio": round(ratio, 4),
            "passed": passed,
            "detail": detail,
        }
        processed = filtered.strip()

    # Phase 2: analyze + spellcheck 병렬 실행 (둘 다 filtered text 사용, 독립적)
    _do_analyze = "analyze" in steps
    _do_spell = "spellcheck" in steps

    if _do_analyze and _do_spell:
        _spell_fn = spellcheck_long if len(processed) > 500 else spellcheck
        with ThreadPoolExecutor(max_workers=2) as _ex:
            _af = _ex.submit(compute_korean_quality, processed, genre)
            _sf = _ex.submit(_spell_fn, processed)
        quality = _af.result()
        spell = _sf.result()
    elif _do_analyze:
        quality = compute_korean_quality(processed, genre=genre)
        spell = None
    elif _do_spell:
        quality = None
        _spell_fn = spellcheck_long if len(processed) > 500 else spellcheck
        spell = _spell_fn(processed)
    else:
        quality = None
        spell = None

    if quality is not None:
        result["analyze"] = {
            "risk_band": quality["risk_band"],
            "risk_score": quality["risk_score"],
            "metrics": quality["metrics"],
            "z_scores": quality["z_scores"],
            "v2_metrics": quality["v2_metrics"],
            "interference_index": quality["v2_interference_index"],
            "evidence": quality["evidence"],
        }

    if spell is not None:
        result["spellcheck"] = {
            "success": spell["success"],
            "errors": spell.get("errors", spell.get("total_errors", 0)),
            "checked": spell["checked"],
            "corrections": spell.get("corrections", []),
            "duration_ms": spell["duration_ms"],
        }
        if spell["success"] and spell.get("errors", spell.get("total_errors", 0)) > 0:
            processed = spell["checked"]

    # Phase 3: readability + cohesion 병렬 실행 (최종 processed 기준)
    _do_read = "readability" in steps
    _do_coh = "cohesion" in steps

    if _do_read and _do_coh:
        with ThreadPoolExecutor(max_workers=2) as _ex:
            _rf = _ex.submit(compute_readability, processed)
            _cf = _ex.submit(compute_cohesion, processed)
        result["readability"] = _rf.result()
        result["cohesion"] = _cf.result()
    elif _do_read:
        result["readability"] = compute_readability(processed)
    elif _do_coh:
        result["cohesion"] = compute_cohesion(processed)

    result["processed"] = processed
    result["duration_ms"] = int((time.time() - start) * 1000)

    return jsonify(result)


# ---------------------------------------------------------------------------
# API: AI 패턴 분석 (22 메트릭)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/analyze", methods=["POST"])
def korean_analyze():
    """AI 패턴 탐지 — 22 메트릭 + z-score + risk_band
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
            genre: {type: string, default: essay, enum: [essay, column, report, blog, medical, chat]}
    responses:
      200:
        description: 전체 분석 결과
        schema:
          type: object
          properties:
            version: {type: string}
            genre: {type: string}
            char_count: {type: integer}
            metrics: {type: object, description: "22개 원지표"}
            z_scores: {type: object, description: "베이스라인 대비 z-score"}
            risk_band: {type: string, description: "low | medium | high"}
            risk_score: {type: integer}
            v2_metrics: {type: object}
            v2_interference_index: {type: number}
            evidence: {type: object, description: "{conclusion_pivots, safe_balances} 근거 구간"}
            warning: {type: string, description: "베이스라인 폴백 시에만"}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    genre = data.get("genre", "essay")
    key = _ck(text, genre)
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = compute_korean_quality(text, genre=genre)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 가독성 점수
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/readability", methods=["POST"])
def korean_readability():
    """한국어 가독성 분석 — 5등급 (초등~학술)
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 가독성 점수 + 등급 + 개선 제안
        schema:
          type: object
          properties:
            score: {type: number}
            grade: {type: string, description: "초등~학술 5등급"}
            metrics: {type: object}
            recommendations: {type: array}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "readability")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = compute_readability(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 맞춤법 검사
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/spellcheck", methods=["POST"])
def korean_spellcheck():
    """맞춤법 검사 — Ollama LLM 자체 교정

    Ollama LLM으로 직접 교정한다 — **외부 맞춤법 API를 쓰지 않는다**(외부 의존 0).
    교정문(`checked`)과 함께 변경 항목(`corrections`)을 낸다.
    AI 문체 패턴 점검이 목적이면 `POST /api/korean/analyze`, 전체 파이프라인은 `POST /api/korean/pipeline`.
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 교정 결과 + 교정 목록
        schema:
          type: object
          properties:
            success: {type: boolean}
            original: {type: string}
            checked: {type: string, description: "교정문"}
            corrections: {type: array, description: "변경 항목(500자 이하 경로)"}
            errors: {type: integer}
            duration_ms: {type: integer}
            error_detail: {type: string, description: "실패 시에만"}
            chunks: {type: array, description: "500자 초과 시 청크 분할 결과"}
            chunks_count: {type: integer}
            total_errors: {type: integer}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "spellcheck")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = spellcheck_long(text) if len(text) > 500 else spellcheck(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 비한국어 필터
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/filter", methods=["POST"])
def korean_filter():
    """비한국어 텍스트 제거 (중국어/일본어/영어 액션 등)
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 필터링된 텍스트 + 한국어 비율
        schema:
          type: object
          properties:
            original: {}
            filtered: {}
            korean_ratio: {type: number}
            passed: {}
            detail: {}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    filtered = force_korean(text)
    passed, ratio, detail = korean_quality_check(filtered)
    return jsonify({
        "original": text,
        "filtered": filtered.strip(),
        "korean_ratio": round(ratio, 4),
        "passed": passed,
        "detail": detail,
    })


# ---------------------------------------------------------------------------
# API: 한국어 비율 검증
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/check", methods=["POST"])
def korean_check():
    """한국어 비율 검증 — 최소 30% 한국어 필요
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 검증 결과
        schema:
          type: object
          properties:
            passed: {}
            korean_ratio: {type: number}
            detail: {}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    passed, ratio, detail = korean_quality_check(text)
    return jsonify({"passed": passed, "korean_ratio": round(ratio, 4), "detail": detail})


# ---------------------------------------------------------------------------
# API: S1/S2 규칙 목록
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/rules", methods=["GET"])
def korean_rules():
    """S1/S2 패턴 규칙 + 장르 설정 + Do-NOT 목록
    ---
    tags: [Korean Language]
    responses:
      200:
        description: 규칙 JSON
    """
    rules_path = os.path.join(_DATA_DIR, "humanize_rules.json")
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({"error": "rules file not found"}), 404


@korean_bp.route("/api/korean/rules/prompt", methods=["GET"])
def korean_rules_prompt():
    """S1 규칙 프롬프트 텍스트 (system prompt 삽입용)
    ---
    tags: [Korean Language]
    responses:
      200:
        description: 프롬프트 텍스트
        schema:
          type: object
          properties:
            prompt: {}
    """
    return jsonify({"prompt": get_s1_prompt_rules()})


# ---------------------------------------------------------------------------
# API: 메트릭 레지스트리
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/metrics", methods=["GET"])
def korean_metrics_list():
    """22개 메트릭 목록 + 설명 + 그룹
    ---
    tags: [Korean Language]
    responses:
      200:
        description: 메트릭 레지스트리
        schema:
          type: object
          properties:
            version: {}
            total_metrics: {type: integer}
            groups: {type: array}
            metrics: {}
    """
    return jsonify({
        "version": VERSION,
        "total_metrics": len(_METRIC_REGISTRY),
        "groups": sorted(set(v["group"] for v in _METRIC_REGISTRY.values())),
        "metrics": _METRIC_REGISTRY,
    })


@korean_bp.route("/api/korean/metrics/detail", methods=["POST"])
def korean_metrics_detail():
    """개별 메트릭 상세 분석 — 선택한 메트릭만 계산
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
            metrics: {type: array, items: {type: string}, description: "계산할 메트릭 이름 목록 (빈 배열=전체)"}
    responses:
      200:
        description: 선택 메트릭 결과
        schema:
          type: object
          properties:
            version: {}
            char_count: {type: integer}
            metrics: {}
            interference_index: {}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    requested = data.get("metrics") or list(_METRIC_FUNCTIONS.keys())
    results = {}
    for name in requested:
        fn = _METRIC_FUNCTIONS.get(name)
        if fn:
            results[name] = fn(text)

    return jsonify({
        "version": VERSION,
        "char_count": len(text),
        "metrics": results,
        "interference_index": interference_index(text) if "interference_index" in (data.get("metrics") or []) or not data.get("metrics") else None,
    })


# ---------------------------------------------------------------------------
# API: 시스템 상태
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/status", methods=["GET"])
def korean_status():
    """한국어 처리 시스템 상태 — 모듈 버전, KSS 상태, 메트릭 수
    ---
    tags: [Korean Language]
    responses:
      200:
        description: 시스템 상태
        schema:
          type: object
          properties:
            version: {}
            modules: {type: object}
            kss_sentence_splitter: {}
            s1_patterns: {type: integer}
            s2_patterns: {type: integer}
            total_metrics: {type: integer}
            integration_points: {type: integer}
            source: {type: string}
    """
    kss_available = False
    try:
        from core.korean_humanize import _kss_splitter
        kss_available = _kss_splitter is not None
    except ImportError:
        pass

    return jsonify({
        "version": VERSION,
        "modules": {
            "korean_filter": {"status": "active", "source": "core/korean.py"},
            "korean_humanize": {"status": "active", "source": "core/korean_humanize.py", "metrics": 22},
            "korean_spellcheck": {"status": "active", "source": "core/korean_spellcheck.py", "backend": "ollama"},
            "korean_readability": {"status": "active", "source": "core/korean_humanize.py"},
        },
        "kss_sentence_splitter": kss_available,
        "s1_patterns": 18,
        "s2_patterns": 12,
        "total_metrics": len(_METRIC_REGISTRY),
        "integration_points": 19,
        "source": "im-not-ai v2.0 (MIT) + KSS (BSD-3) + NovelWriter (MIT)",
    })


# ---------------------------------------------------------------------------
# API: 텍스트 통계 분석
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/stats", methods=["POST"])
def korean_stats():
    """텍스트 종합 통계 — 어휘 빈도, 반복어, 문장 유형, 대화 비율, 어휘 풍부도
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 텍스트 통계
        schema:
          type: object
          properties:
            char_count: {type: integer}
            word_count: {type: integer}
            sentence_count: {type: integer}
            paragraph_count: {type: integer}
            avg_sentence_length: {type: number}
            vocabulary_richness: {type: number}
            word_frequency: {type: object}
            repeated_words: {type: array}
            sentence_types: {type: object}
            declarative: {type: integer}
            interrogative: {type: integer}
            exclamatory: {type: integer}
            imperative: {type: integer}
            dialogue_ratio: {type: number}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "stats")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = compute_text_stats(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 문체 변환 (Ollama LLM)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/style-transfer", methods=["POST"])
def korean_style_transfer():
    """문체 변환 — 존댓말↔반말, 문어체↔구어체, 소설체↔대화체 등
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text, target_style]
          properties:
            text: {type: string, description: "변환할 텍스트"}
            target_style: {type: string, enum: [formal, informal, literary, colloquial, narration, dialogue], description: "목표 문체"}
            preserve_meaning: {type: boolean, default: true}
    responses:
      200:
        description: 변환 결과
        schema:
          type: object
          properties:
            original: {}
            converted: {}
            target_style: {}
            tokens: {}
            duration_ms: {type: integer}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    target = data.get("target_style", "").strip()
    if not text or not target:
        return jsonify({"error": "text and target_style required"}), 400

    style_prompts = {
        "formal": "존댓말(합쇼체)로 변환하세요. '~습니다', '~합니다' 종결.",
        "informal": "반말(해체)로 변환하세요. '~야', '~해', '~이야' 종결.",
        "literary": "문어체/소설체로 변환하세요. 서사적 묘사, 은유, 여운 있는 문장.",
        "colloquial": "구어체/대화체로 변환하세요. 자연스러운 말투, 줄임말 허용.",
        "narration": "3인칭 나레이션으로 변환하세요. 객관적 서술, 장면 묘사.",
        "dialogue": "대화문으로 변환하세요. 인물 간 대사 형식으로.",
    }

    style_inst = style_prompts.get(target)
    if not style_inst:
        return jsonify({"error": f"unknown style: {target}", "available": list(style_prompts.keys())}), 400

    system = f"""당신은 한국어 문체 변환 전문가입니다.
{style_inst}

규칙:
1. 의미와 내용은 100% 보존하세요
2. 문체/어투/종결어미만 변환하세요
3. 고유명사, 수치, 인용은 그대로 유지하세요
4. 변환된 텍스트만 출력하세요 (설명 없이)"""

    import requests as _req
    start = time.time()
    try:
        from core.config import OLLAMA_BASE
        base = OLLAMA_BASE or "http://localhost:11434"
        # v2.124.98 task 라우터 경유 — 모델명은 config(`korean.transform`)가 정한다
        from core import model_router
        res = model_router.generate(
            "korean.transform", f"다음 텍스트를 변환하세요:\n\n{text}",
            system=system,
            options={"temperature": 0.3, "num_predict": int(len(text) * 1.5)},
            timeout=30, adapter=model_router.OllamaAdapter(base=base))
        if not res.ok:
            raise RuntimeError(res.error or "generate failed")
        converted = (res.text or "").strip()
        tokens = res.tokens
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500

    return jsonify({
        "original": text,
        "converted": converted,
        "target_style": target,
        "tokens": tokens,
        "duration_ms": int((time.time() - start) * 1000),
    })


# ---------------------------------------------------------------------------
# API: 대화문 분석 (소설용)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/dialogue-analysis", methods=["POST"])
def korean_dialogue_analysis():
    """소설 대화문 분석 — 대사/묘사/내면 비율, 화자별 대사 수, 대사체 특성
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "소설 텍스트"}
    responses:
      200:
        description: 대화문 분석 결과
        schema:
          type: object
          properties:
            composition: {type: object}
            speakers: {}
            dialogue_stats: {type: object}
            recommendations: {}
    """
    import re as _re
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    lines = text.split("\n")
    dialogue_lines = []
    narration_lines = []
    inner_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # 대화문: 따옴표로 시작하거나 포함
        if _re.search(r'^["\u201c]|["\u201c].*["\u201d]', stripped):
            dialogue_lines.append(stripped)
        # 내면 독백: 작은따옴표 또는 이탤릭 표현
        elif _re.search(r"^['\u2018]|['\u2018].*['\u2019]", stripped):
            inner_lines.append(stripped)
        else:
            narration_lines.append(stripped)

    total = max(len(dialogue_lines) + len(narration_lines) + len(inner_lines), 1)

    # 대사에서 화자 추출 시도 (패턴: "OOO이/가 말했다", "OOO은/는")
    speakers = {}
    for d in dialogue_lines:
        # "말했다" 앞의 주어 추출
        m = _re.search(r'([가-힣]{2,5})(?:이|가|은|는)\s*(?:말했|물었|대답했|외쳤|속삭|소리쳤|중얼)', d)
        if m:
            name = m.group(1)
            speakers[name] = speakers.get(name, 0) + 1

    # 대화문 내 평균 길이
    avg_dialogue_len = 0
    if dialogue_lines:
        dial_lengths = [len(d) for d in dialogue_lines]
        avg_dialogue_len = round(sum(dial_lengths) / len(dial_lengths), 1)

    return jsonify({
        "composition": {
            "dialogue_ratio": round(len(dialogue_lines) / total, 3),
            "narration_ratio": round(len(narration_lines) / total, 3),
            "inner_voice_ratio": round(len(inner_lines) / total, 3),
            "dialogue_lines": len(dialogue_lines),
            "narration_lines": len(narration_lines),
            "inner_voice_lines": len(inner_lines),
        },
        "speakers": speakers if speakers else None,
        "dialogue_stats": {
            "avg_length": avg_dialogue_len,
            "total_dialogue_chars": sum(len(d) for d in dialogue_lines),
        },
        "recommendations": _dialogue_recommendations(
            len(dialogue_lines) / total if total > 0 else 0,
            len(narration_lines) / total if total > 0 else 0,
        ),
    })


# ---------------------------------------------------------------------------
# API: 텍스트 응집성 분석
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/cohesion", methods=["POST"])
def korean_cohesion():
    """텍스트 응집성 분석 — 문장 간 연결성, 접속사 밀도, 주제 일관성
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 응집성 점수 + 약점 진단
        schema:
          type: object
          properties:
            score: {type: number}
            grade: {type: string}
            metrics: {type: object, description: "ASO·접속사·주제 일관성"}
            weak_points: {type: array}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "cohesion")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = compute_cohesion(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 글쓰기 피드백 자동 생성 (Ollama LLM)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/feedback", methods=["POST"])
def korean_feedback():
    """AI 글쓰기 피드백 자동 생성 — 5항목 평가 + 구체적 개선 제안
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "피드백할 텍스트"}
            genre: {type: string, default: essay, description: "장르 (essay/novel/report/blog)"}
            focus: {type: string, default: all, description: "집중 영역 (all/content/structure/expression/grammar/style)"}
    responses:
      200:
        description: 피드백 결과
        schema:
          type: object
          properties:
            feedback: {}
            quantitative: {type: object}
            genre: {}
            focus: {}
            tokens: {}
            duration_ms: {type: integer}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    genre = data.get("genre", "essay")
    focus = data.get("focus", "all")

    # 정량 분석 먼저 수행
    quality = compute_korean_quality(text, genre=genre)
    readability = compute_readability(text)
    cohesion = compute_cohesion(text)
    stats = compute_text_stats(text)

    # LLM 피드백 생성
    quantitative_summary = f"""[정량 분석 결과]
- AI 패턴 위험도: {quality["risk_band"]} (score {quality["risk_score"]})
- 결산 피벗 어휘: {quality["metrics"]["conclusion_pivot_count"]}건
- 연결어미+쉼표: {quality["metrics"]["ending_comma_rate"]:.2f}
- 가독성: {readability["score"]}점 ({readability["grade"]})
- 응집성: {cohesion["score"]}점 ({cohesion["grade"]})
- 대화 비율: {stats.get("dialogue_ratio", 0):.0%}
- 어휘 다양성(TTR): {stats.get("vocabulary_richness", {}).get("ttr", 0)}"""

    focus_inst = {
        "all": "내용, 구조, 표현, 문법, 문체를 종합 평가",
        "content": "내용의 깊이, 논리성, 설득력에 집중",
        "structure": "구조, 전개, 단락 구성에 집중",
        "expression": "표현력, 묘사, 어휘 선택에 집중",
        "grammar": "맞춤법, 문법, 띄어쓰기에 집중",
        "style": "문체, 톤, 리듬, 일관성에 집중",
    }

    system = f"""당신은 한국어 글쓰기 전문 피드백 교사입니다.

다음 5항목으로 피드백하세요:
1. 정확성 — 내용의 정확성, 논리적 오류
2. 효과성 — 전달력, 독자 설득력/몰입도
3. 우선순위 — 가장 먼저 고쳐야 할 부분
4. 이해도 — 대상 독자 수준에 맞는지
5. 격려 — 잘한 점 인정 + 동기부여

장르: {genre}
집중: {focus_inst.get(focus, focus_inst["all"])}

규칙:
- 구체적 예시를 들어 설명하세요 (원문 인용 + 개선안)
- "좋습니다/나쁩니다" 대신 구체적 근거를 제시하세요
- 최대 5개 핵심 피드백으로 제한
- JSON 출력: {{"feedbacks": [{{"category": "...", "issue": "...", "example": "원문 인용", "suggestion": "개선안", "priority": "high/medium/low"}}], "strengths": ["잘한 점"], "overall_score": 0-100}}"""

    import requests as _req
    start = time.time()
    try:
        from core.config import OLLAMA_BASE
        base = OLLAMA_BASE or "http://localhost:11434"
        from core import model_router
        res = model_router.generate(
            "korean.transform", f"{quantitative_summary}\n\n[원문]\n{text[:3000]}",
            system=system, options={"temperature": 0.3, "num_predict": 1500},
            timeout=30, adapter=model_router.OllamaAdapter(base=base))
        if not res.ok:
            raise RuntimeError(res.error or "generate failed")
        llm_response = (res.text or "").strip()
        tokens = res.tokens
    except Exception as e:
        llm_response = None
        tokens = 0
        logger.warning(f"Feedback LLM failed: {e}")

    # JSON 파싱 시도
    feedback_parsed = None
    if llm_response:
        import re as _re
        # ```json 블록 제거
        cleaned = _re.sub(r"^```\w*\n?", "", llm_response)
        cleaned = _re.sub(r"\n?```$", "", cleaned).strip()
        try:
            import json as _json
            feedback_parsed = _json.loads(cleaned)
        except Exception:
            # { } 추출
            m = _re.search(r"\{[\s\S]*\}", cleaned)
            if m:
                try:
                    feedback_parsed = _json.loads(m.group())
                except Exception:
                    pass

    return jsonify({
        "feedback": feedback_parsed or {"raw": llm_response},
        "quantitative": {
            "quality": get_quality_summary(quality),
            "readability": {"score": readability["score"], "grade": readability["grade"]},
            "cohesion": {"score": cohesion["score"], "grade": cohesion["grade"]},
            "stats": {
                "sentences": stats.get("sentence_count", 0),
                "words": stats.get("word_count", 0),
                "dialogue_ratio": stats.get("dialogue_ratio", 0),
                "ttr": stats.get("vocabulary_richness", {}).get("ttr", 0),
            },
        },
        "genre": genre,
        "focus": focus,
        "tokens": tokens,
        "duration_ms": int((time.time() - start) * 1000),
    })


# ---------------------------------------------------------------------------
# API: 경어법 수준 판별
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/honorific", methods=["POST"])
def korean_honorific():
    """경어법 수준 판별 — 하십시오/해요/해체/해라 분류 + 혼용 탐지
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 경어체 분포 + 일관성 + 혼용 경고
        schema:
          type: object
          properties:
            dominant: {type: string, description: "우세 경어 등급"}
            distribution: {type: object}
            mixed_levels: {type: boolean, description: "true면 등급 혼용"}
            consistency: {type: number}
            detail: {type: object}
            warning: {type: string}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "honorific")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = detect_honorific_level(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 반복/중복 탐지
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/repetition", methods=["POST"])
def korean_repetition():
    """반복·중복 패턴 탐지 — 동일 문장, 반복 어구, 종결어미 단조로움
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 중복 문장 + 반복 어구 + 어미 단조로움 + 점수
        schema:
          type: object
          properties:
            score: {type: number}
            duplicate_sentences: {type: array}
            repeated_phrases: {type: array}
            ending_monotony: {type: object, description: "어미 단조로움"}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "repetition")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = detect_repetition(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 시점(POV) 분석
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/pov", methods=["POST"])
def korean_pov():
    """소설 시점 판별 — 1인칭/3인칭/혼합 + 지표
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 시점 판정 + 근거 지표
        schema:
          type: object
          properties:
            pov: {type: string, description: "1인칭 | 3인칭 | 혼합"}
            confidence: {type: number}
            indicators: {type: object}
            warning: {type: string}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "pov")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = detect_pov(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 감각 묘사 분석
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/sensory", methods=["POST"])
def korean_sensory():
    """감각 묘사 분석 — 5감 빈도 + Show Don't Tell 점수
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 감각별 빈도 + SDT 점수 + 개선 제안
        schema:
          type: object
          properties:
            senses: {type: object, description: "5감별 집계"}
            dominant: {type: string}
            missing: {type: array}
            total_sensory: {type: integer}
            tell_count: {type: integer}
            show_dont_tell_score: {type: number}
            per_sentence: {type: array}
            recommendations: {type: array}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    key = _ck(text, "sensory")
    cached = _korean_cache.get(key)
    if cached is not None:
        return jsonify(cached)
    result = analyze_sensory(text)
    _korean_cache.set(key, result)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 종합 진단 (소설 전용)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/novel-diagnosis", methods=["POST"])
def korean_novel_diagnosis():
    """소설 텍스트 종합 진단 — 모든 분석을 한번에 실행
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "소설 텍스트"}
            genre: {type: string, default: literary}
    responses:
      200:
        description: 종합 진단 결과
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    genre = data.get("genre", "literary")
    start = time.time()

    result = {
        "quality": get_quality_summary(compute_korean_quality(text, genre=genre)),
        "readability": compute_readability(text),
        "cohesion": compute_cohesion(text),
        "repetition": detect_repetition(text),
        "honorific": detect_honorific_level(text),
        "pov": detect_pov(text),
        "sensory": analyze_sensory(text),
        "dialogue": None,
        "stats": compute_text_stats(text),
    }

    # 대화문 분석 (인라인)
    import re as _re
    lines = text.split("\n")
    dial = sum(1 for l in lines if l.strip() and _re.search(r'^["\u201c]|["\u201c].*["\u201d]', l.strip()))
    narr = sum(1 for l in lines if l.strip()) - dial
    total_lines = max(dial + narr, 1)
    result["dialogue"] = {
        "dialogue_ratio": round(dial / total_lines, 3),
        "narration_ratio": round(narr / total_lines, 3),
    }

    # 종합 점수 (가중 평균)
    scores = {
        "quality": max(0, 100 - result["quality"].get("risk_score", 0) * 15),
        "readability": result["readability"].get("score", 50),
        "cohesion": result["cohesion"].get("score", 50),
        "repetition": result["repetition"].get("score", 50),
        "sensory": result["sensory"].get("show_dont_tell_score", 50),
    }
    weights = {"quality": 0.25, "readability": 0.15, "cohesion": 0.2, "repetition": 0.2, "sensory": 0.2}
    overall = round(sum(scores[k] * weights[k] for k in scores), 1)

    result["overall"] = {
        "score": overall,
        "grade": "A" if overall >= 80 else "B" if overall >= 65 else "C" if overall >= 50 else "D",
        "component_scores": scores,
    }
    result["duration_ms"] = int((time.time() - start) * 1000)

    return jsonify(result)


# ---------------------------------------------------------------------------
# API: 텍스트 비교 (Diff)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/diff", methods=["POST"])
def korean_diff():
    """텍스트 비교 — 수정 전후 변경 추적 (문장 단위 diff)
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [original, revised]
          properties:
            original: {type: string, description: "원본 텍스트"}
            revised: {type: string, description: "수정본 텍스트"}
    responses:
      200:
        description: 변경률 + 변경 목록 + 가드 경고
        schema:
          type: object
          properties:
            change_rate: {type: integer}
            added_count: {type: integer}
            removed_count: {type: integer}
            modified_count: {}
            unchanged_count: {}
            changes: {type: array}
            warning: {}
            summary: {}
    """
    data = request.get_json() or {}
    original = data.get("original", "").strip()
    revised = data.get("revised", "").strip()
    if not original and not revised:
        return jsonify({"error": "original or revised required"}), 400
    return jsonify(compute_diff(original, revised))


# ---------------------------------------------------------------------------
# API: 캐릭터 말투 분석
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/character-voice", methods=["POST"])
def korean_character_voice():
    """캐릭터별 말투 분석 — 대사 추출 + 종결어미 + 스타일 + 일관성
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "소설 텍스트"}
            characters: {type: array, items: {type: string}, description: "분석할 캐릭터 이름 목록"}
    responses:
      200:
        description: 캐릭터별 대사 특성 + 일관성 이슈
        schema:
          type: object
          properties:
            characters: {type: object}
            total_speakers: {type: integer}
            consistency_issues: {type: array}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    characters = data.get("characters")
    return jsonify(analyze_character_voice(text, characters))


# ---------------------------------------------------------------------------
# API: 생동감 분석 (의성어/의태어 + 감각)
# ---------------------------------------------------------------------------

@korean_bp.route("/api/korean/expressiveness", methods=["POST"])
def korean_expressiveness():
    """소설 생동감 분석 — 의성어/의태어 + 감각 묘사 통합
    ---
    tags: [Korean Language]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string}
    responses:
      200:
        description: 의성어/의태어 빈도 + 감각 요약 + 생동감 점수
        schema:
          type: object
          properties:
            onomatopoeia: {type: object}
            mimetic: {type: object}
            sensory_summary: {type: object}
            vividness_score: {type: integer}
            per_sentence: {type: number}
            recommendations: {}
    """
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    return jsonify(analyze_expressiveness(text))


def _dialogue_recommendations(dial_ratio: float, narr_ratio: float) -> list[str]:
    """대화문 비율 기반 개선 제안."""
    recs = []
    if dial_ratio < 0.2:
        recs.append("대화가 부족합니다. 인물 간 대화를 추가하면 생동감이 높아집니다.")
    elif dial_ratio > 0.7:
        recs.append("대화 비중이 높습니다. 묘사와 내면 독백을 추가하면 깊이가 생깁니다.")
    if narr_ratio > 0.8:
        recs.append("서술 비중이 높습니다. Show Don't Tell — 행동과 대화로 보여주세요.")
    return recs
