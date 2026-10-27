"""Medical AI API Blueprint — 병원용 멀티테넌트 진료 지원"""
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, date

import requests as http_requests
from core.quality import (force_json, force_json_array, validate_soap_fields,
                         generate_with_validation, generate_with_validation_ex, validate_json_response,
                         validate_json_array_response, validate_icd_codes,
                         check_drug_interactions)
from flask import Blueprint, request, jsonify, Response
from core.security import require_auth
from core.korean_humanize import compute_korean_quality, get_quality_summary

logger = logging.getLogger("ollama-admin")

medical_bp = Blueprint("medical", __name__)


def _escape_like(val: str) -> str:
    """LIKE 패턴 특수문자 이스케이프"""
    return val.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# 의료 모델 라우팅 — 복잡도별 모델 분기 (v2.41.0 → v2.47.0 확장)
# 기본값 (active config가 없을 때 fallback)
_DEFAULT_HEAVY = "qwen2.5:14b"
_DEFAULT_LIGHT = "qwen2.5:7b"

# 활성 모델 설정 파일 경로 (init_medical에서 설정)
_ACTIVE_MODEL_CONFIG = None
_model_config_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Medical Model Registry (v2.47.0)
# ---------------------------------------------------------------------------
MEDICAL_MODELS = {
    # --- 파인튜닝 모델 (한국어 의료) ---
    "werubmedv2:latest": {
        "id": "werubmedv2", "name": "WeRUB Medical v2",
        "size_gb": 4.7, "lang": "ko", "base": "qwen2.5:7b",
        "domains": ["soap", "diagnosis", "prescription"],
        "strength": "한국어 SOAP, 진단, 처방 (파인튜닝 232건)",
        "tier": "primary",
    },
    "werub-medical:7b": {
        "id": "werub-medical-v1", "name": "WeRUB Medical v1",
        "size_gb": 4.7, "lang": "ko", "base": "qwen2.5:7b",
        "domains": ["soap", "diagnosis"],
        "strength": "한국어 SOAP, 감별진단 (파인튜닝 53건, 메인)",
        "tier": "primary",
    },
    # --- Google MedGemma ---
    "medgemma:4b": {
        "id": "medgemma-4b", "name": "MedGemma 4B",
        "size_gb": 3.3, "lang": "en", "base": "gemma3",
        "domains": ["vision", "radiology", "pathology", "dermatology"],
        "strength": "의료 영상 분석 (X-ray, 피부, 안과, 병리), Vision+Text",
        "tier": "specialist",
    },
    "medgemma:27b": {
        "id": "medgemma-27b", "name": "MedGemma 27B",
        "size_gb": 17.0, "lang": "en", "base": "gemma3",
        "domains": ["clinical_reasoning", "diagnosis", "research"],
        "strength": "Google 공식 의료 27B, 임상 추론 (Text only, VRAM 주의)",
        "tier": "heavy",
    },
    "MedAIBase/MedGemma1.5:4b": {
        "id": "medgemma15-4b", "name": "MedGemma 1.5 4B",
        "size_gb": 7.8, "lang": "en", "base": "gemma3",
        "domains": ["clinical_qa", "clinical_reasoning", "medical_records"],
        "strength": "MedGemma 1.5 (2026-01): 의료 추론·의료기록(EHR/FHIR) 해석 개선. 자체벤치 89.7 vs 4b 80.0. F16 Text only(비전은 medgemma:4b 유지)",
        "tier": "primary",
    },
    # --- 임상 특화 ---
    "medllama2:latest": {
        "id": "medllama2", "name": "MedLlama2 7B",
        "size_gb": 3.8, "lang": "en", "base": "llama2",
        "domains": ["clinical_analysis", "reference"],
        "strength": "임상 분석, 참고문헌 기반 응답 (영어)",
        "tier": "specialist",
    },
    "meditron:latest": {
        "id": "meditron", "name": "Meditron 7B",
        "size_gb": 3.8, "lang": "en", "base": "llama2",
        "domains": ["differential_diagnosis", "pharmacology"],
        "strength": "감별진단, 약물 정보, 의료 가이드라인 (영어)",
        "tier": "specialist",
    },
    # --- 신규 (v2.47.0) ---
    "thewindmom/llama3-med42-8b": {
        "id": "med42-8b", "name": "Med42 8B",
        "size_gb": 4.9, "lang": "en", "base": "llama3",
        "domains": ["clinical_qa", "usmle", "diagnosis", "treatment"],
        "strength": "임상 QA 최강급 (70B가 GPT-4 능가), USMLE 추론",
        "tier": "primary",
    },
    "koesn/llama3-openbiollm-8b": {
        "id": "openbiollm-8b", "name": "OpenBioLLM 8B",
        "size_gb": 4.9, "lang": "en", "base": "llama3",
        "domains": ["research", "pubmed", "biomedical"],
        "strength": "PubMedQA, BioASQ, 바이오의학 연구 문헌",
        "tier": "specialist",
    },
    # biomistral, llava-med 삭제 (v2.69.0) — medgemma:4b/27b가 대체
    "gpt-oss:20b": {
        "id": "gpt-oss", "name": "GPT-OSS 20B",
        "size_gb": 13.0, "lang": "multi", "base": "gpt-oss",
        "domains": ["clinical_reasoning", "differential_diagnosis", "health_qa"],
        "strength": "OpenAI o3-mini급 추론, HealthBench 최상위, 의료 교차검증",
        "tier": "primary",
    },
    "monotykamary/medichat-llama3": {
        "id": "medichat", "name": "MediChat-Llama3 8B",
        "size_gb": 4.9, "lang": "en", "base": "llama3",
        "domains": ["patient_education", "health_qa"],
        "strength": "환자 교육, 건강 상담, 쉬운 의학 설명",
        "tier": "specialist",
    },
}

# 도메인별 최적 모델 라우팅 (v2.47.0)
# 각 도메인에 대해 우선순위 순서로 모델 배열
DOMAIN_MODEL_ROUTING = {
    # 한국어 진료
    "soap":                 ["werub-medical:7b", "werubmedv2:latest", "qwen2.5:14b"],
    "diagnosis":            ["werub-medical:7b", "thewindmom/llama3-med42-8b", "qwen2.5:14b"],
    "prescription":         ["werubmedv2:latest", "werub-medical:7b", "meditron:latest"],
    # 영어 임상
    "clinical_qa":          ["thewindmom/llama3-med42-8b", "MedAIBase/MedGemma1.5:4b", "medgemma:27b"],
    "clinical_analysis":    ["thewindmom/llama3-med42-8b", "medllama2:latest", "meditron:latest"],
    "clinical_reasoning":   ["thewindmom/llama3-med42-8b", "MedAIBase/MedGemma1.5:4b", "medgemma:27b"],
    "differential_diagnosis": ["thewindmom/llama3-med42-8b", "meditron:latest", "medllama2:latest"],
    # 🔴 2026-08-04 1순위 교체(meditron → qwen2.5:14b) — meditron이 이 과제를 **수행하지 못한다**.
    #   `/api/medical/predict/adverse-drug` 첫 측정에서 **4/4 결정적 재현**:
    #     프롬프트 "약물 이상반응 위험을 JSON으로만 답하세요(와파린+아미오다론+아스피린, 78세 여성)"
    #     → meditron: **"이상반응의 위험도를 계산하는 과정을 알아보세요."**(27자, 분석 0)
    #     → JSON 파싱 실패 → `{"response": <입력 에코>}` 폴백이 그대로 나갔다.
    #   같은 입력에 qwen2.5:14b는 1,234자로 individual_risks·duplications·common_adr를 채운다.
    # ⚠️ 약물 상호작용의 **정본은 식약처 DUR**(`/api/medical/dur/check`·29ms·LLM 0)이다.
    #    LLM은 그 위에 설명을 붙이는 역할이며, 상호작용 판정 자체를 LLM에 맡기지 말 것.
    "pharmacology":         ["qwen2.5:14b", "thewindmom/llama3-med42-8b", "meditron:latest"],
    "treatment":            ["thewindmom/llama3-med42-8b", "meditron:latest", "medllama2:latest"],
    "usmle":                ["thewindmom/llama3-med42-8b", "medgemma:27b"],
    # 연구/문헌 (v2.69.0: biomistral 삭제, gpt-oss 추가)
    "research":             ["koesn/llama3-openbiollm-8b", "gpt-oss:20b", "medllama2:latest"],
    "pubmed":               ["koesn/llama3-openbiollm-8b", "medllama2:latest", "gpt-oss:20b"],
    "biomedical":           ["koesn/llama3-openbiollm-8b", "gpt-oss:20b"],
    "literature":           ["koesn/llama3-openbiollm-8b", "medllama2:latest"],
    # 의료 영상 (v2.69.0: llava-med 삭제, medgemma만 사용)
    "vision":               ["medgemma:4b", "medgemma:27b"],
    "radiology":            ["medgemma:4b", "medgemma:27b"],
    "pathology":            ["medgemma:4b", "medgemma:27b"],
    "dermatology":          ["medgemma:4b"],
    # 환자 커뮤니케이션
    "patient_education":    ["monotykamary/medichat-llama3", "qwen2.5:14b"],
    "health_qa":            ["monotykamary/medichat-llama3", "thewindmom/llama3-med42-8b"],
    # 범용 (한국어 fallback)
    "general_ko":           ["qwen2.5:14b", "werub-medical:7b"],
    "general_en":           ["thewindmom/llama3-med42-8b", "qwen2.5:14b"],
}


def _resolve_domain_model(domain, user_model=None):
    """도메인에 최적화된 모델 선택. 사용자 지정 모델 우선.
    설치된 모델 중에서만 선택 (Ollama에 있는지 확인하지 않고 레지스트리 기준)."""
    if user_model:
        return user_model
    candidates = DOMAIN_MODEL_ROUTING.get(domain, [])
    if candidates:
        return candidates[0]  # 첫 번째 = 최우선
    return get_heavy_model()


# 모델 설치 여부 캐시 (5분 TTL)
_installed_models_cache = {}
_installed_models_ts = 0

def _check_model_installed(model_name):
    """Ollama에 설치되어 있는지 확인 (5분 캐시)"""
    global _installed_models_cache, _installed_models_ts
    import time as _time
    now = _time.time()
    if now - _installed_models_ts > 300:
        try:
            r = http_requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=3)
            _installed_models_cache = {m["name"] for m in r.json().get("models", [])}
            _installed_models_ts = now
        except Exception:
            return True  # 확인 불가 시 설치 가정
    return model_name in _installed_models_cache or any(model_name.split(":")[0] in n for n in _installed_models_cache)


def _resolve_model_safe(domain, user_model=None):
    """도메인 모델 선택 + 존재 확인 + 폴백"""
    if user_model and user_model != "auto":
        if _check_model_installed(user_model):
            return user_model
        logger.warning("Model %s not installed, falling back", user_model)

    candidates = DOMAIN_MODEL_ROUTING.get(domain, [])
    for model in candidates:
        if _check_model_installed(model):
            return model

    return get_heavy_model()


def _is_korean_input(text):
    """한국어 비율 30% 이상이면 True"""
    if not text:
        return True
    ko_count = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    return ko_count / max(len(text), 1) > 0.3


def _load_active_model_config():
    """medical_model_active.json에서 활성 모델 설정 로드"""
    if _ACTIVE_MODEL_CONFIG and os.path.exists(_ACTIVE_MODEL_CONFIG):
        try:
            with open(_ACTIVE_MODEL_CONFIG) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Failed to load active model config: %s", e)
    return {}


def _save_active_model_config(cfg):
    """활성 모델 설정 저장"""
    if not _ACTIVE_MODEL_CONFIG:
        return
    with _model_config_lock:
        with open(_ACTIVE_MODEL_CONFIG, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_heavy_model():
    """현재 활성 HEAVY 모델 반환"""
    cfg = _load_active_model_config()
    return cfg.get("heavy_model", _DEFAULT_HEAVY)


def get_light_model():
    """현재 활성 LIGHT 모델 반환"""
    cfg = _load_active_model_config()
    return cfg.get("light_model", _DEFAULT_LIGHT)


def _get_model_defaults():
    """현재 활성 모델 기반 라우팅 테이블 — 도메인 라우팅 통합"""
    heavy = get_heavy_model()
    light = get_light_model()
    return {
        # 한국어 진료 (기존)
        "summarize": heavy,
        "analyze": heavy,
        "draft": heavy,
        "explain": light,
        "reference": light,
        "translate": light,
        # 영어 임상 (도메인 라우팅 연동)
        "clinical_analyze": _resolve_domain_model("clinical_analysis"),
        "clinical_drug": _resolve_domain_model("pharmacology"),
        "clinical_qa": _resolve_domain_model("clinical_qa"),
        # 예측/추론
        "predict_readmission": _resolve_domain_model("clinical_reasoning"),
        "predict_complication": _resolve_domain_model("clinical_reasoning"),
        "predict_adverse_drug": _resolve_domain_model("pharmacology"),
        "reasoning_chain": _resolve_domain_model("diagnosis"),
        # 연구/문헌
        "research": _resolve_domain_model("research"),
        "literature": _resolve_domain_model("literature"),
        # 환자 커뮤니케이션
        "patient_education": _resolve_domain_model("patient_education"),
        "health_qa": _resolve_domain_model("health_qa"),
        # 의료 영상
        "vision": _resolve_domain_model("vision"),
        "radiology": _resolve_domain_model("radiology"),
    }


# 자체 파인튜닝 모델 식별 접두(규제 가드 대상 — 모델 카드 docs/model-cards/)
_CUSTOM_MODEL_PREFIXES = ("werub",)


def is_custom_model(model):
    """자체 파인튜닝(제조사 직접 개발) 모델 여부."""
    return bool(model) and model.lower().startswith(_CUSTOM_MODEL_PREFIXES)


def _pin_public(model):
    """규제 라우팅 가드(opt-in): 자동 라우팅이 자체 파인튜닝 모델을 반환하지 않도록
    공개 모델로 대체. 식약처 LLM 가이드라인 대응 — 허가 대상 기능 경로에 자체 개발
    모델 미관여를 설정 드리프트와 무관하게 구조적으로 보장하는 스위치.
    config `medical_pin_public_models`(기본 false)=기존 동작 100% 불변,
    `medical_public_substitute`(기본 qwen2.5:14b). 호출자가 model을 명시하면 존중
    (명시 지정=추적 가능한 의도적 선택이라 가드 대상 아님 — _get_medical_model 참조)."""
    try:
        cfg = _load_config() if _load_config else {}
    except Exception:  # noqa: BLE001 — 가드 실패는 기존 라우팅으로 강등
        cfg = {}
    if not cfg.get("medical_pin_public_models", False):
        return model
    if is_custom_model(model):
        return cfg.get("medical_public_substitute", "qwen2.5:14b")
    return model


def _get_medical_model(action, user_model=None, input_text=""):
    """사용자 지정 모델 우선, 없으면 도메인 라우팅 기본값.
    model='auto' → 도메인 자동 라우팅 강제.
    한국어 입력 시 한국어 모델 유지, 영어 입력 시 영어 특화 모델 사용."""
    if user_model and user_model != "auto":
        return user_model
    defaults = _get_model_defaults()
    model = defaults.get(action, get_heavy_model())

    # 한국어 입력 + 영어 전용 모델 → 한국어 모델로 폴백
    if input_text and _is_korean_input(input_text):
        ko_actions = {"analyze", "explain", "reference", "clinical_qa", "health_qa", "patient_education"}
        if action in ko_actions and model in _EN_ONLY_MODELS:
            return _pin_public(get_heavy_model())

    return _pin_public(model)

# 영어 전용 모델 집합
_EN_ONLY_MODELS = {
    "thewindmom/llama3-med42-8b",
    "koesn/llama3-openbiollm-8b",
    "gpt-oss:20b",
    "monotykamary/medichat-llama3",
    "medllama2:latest",
    "meditron:latest",
}

# app.py에서 init_medical()로 주입
_load_config = None
_ollama_session = None
_llm_generate_korean = None
_OLLAMA_BASE = None
_BASE_DIR = None

_medical_db_local = threading.local()


# 의료 지식 RAG store는 client.rag(vector_db + bge-m3 + FTS5)로 단일화(v2.88.2).
# 구 이중 store(chromadb_data `_get_chroma` + qwen3-embedding `_get_embedding`)는 제거 —
# 색인은 chromadb_data, 검색은 vector_db를 봐서 hybrid가 항상 0건이던 분리 해소.
# chromadb_data/medical_knowledge(157건)는 동결 보존(코드 미참조), 원본은 medical.db.
MEDICAL_KB_COLLECTION = "medical_knowledge"


def init_medical(*, load_config, ollama_session, llm_generate_korean,
                 ollama_base, base_dir):
    global _load_config, _ollama_session, _llm_generate_korean
    global _OLLAMA_BASE, _BASE_DIR, _ACTIVE_MODEL_CONFIG
    _load_config = load_config
    _ollama_session = ollama_session
    _llm_generate_korean = llm_generate_korean
    _OLLAMA_BASE = ollama_base
    _BASE_DIR = base_dir
    _ACTIVE_MODEL_CONFIG = os.path.join(base_dir, "medical_model_active.json")
    # 로드된 활성 모델 로그
    cfg = _load_active_model_config()
    if cfg:
        logger.info("Medical active model: heavy=%s, light=%s",
                     cfg.get("heavy_model", _DEFAULT_HEAVY),
                     cfg.get("light_model", _DEFAULT_LIGHT))


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_medical_db():
    conn = getattr(_medical_db_local, "conn", None)
    if conn is None:
        db_path = os.path.join(_BASE_DIR, "medical.db")
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _init_medical_schema(conn)
        _medical_db_local.conn = conn
    try:
        conn.execute("SELECT 1")
    except sqlite3.DatabaseError:
        try:
            conn.close()
        except Exception as e:
            logger.debug("medical DB close failed: %s", e)
        _medical_db_local.conn = None
        return _get_medical_db()
    return conn


def _init_medical_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS medical_tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            api_key TEXT NOT NULL UNIQUE,
            plan TEXT NOT NULL DEFAULT 'basic',
            specialty TEXT,
            max_queries_day INTEGER DEFAULT 100,
            max_queries_month INTEGER DEFAULT 2000,
            max_rag_docs INTEGER DEFAULT 100,
            contact_name TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            settings TEXT DEFAULT '{}',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS medical_consultations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            action TEXT NOT NULL,
            input_text TEXT NOT NULL,
            output_text TEXT,
            model TEXT,
            tokens INTEGER,
            duration_ms INTEGER,
            metadata TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (tenant_id) REFERENCES medical_tenants(id)
        );
        CREATE INDEX IF NOT EXISTS idx_mc_tenant ON medical_consultations(tenant_id, created_at DESC);
        CREATE TABLE IF NOT EXISTS medical_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            date TEXT NOT NULL,
            query_count INTEGER DEFAULT 0,
            token_count INTEGER DEFAULT 0,
            UNIQUE(tenant_id, date),
            FOREIGN KEY (tenant_id) REFERENCES medical_tenants(id)
        );
        CREATE TABLE IF NOT EXISTS medical_knowledge (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT,
            version TEXT,
            indexed_at TEXT DEFAULT (datetime('now')),
            metadata TEXT DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_mk_category ON medical_knowledge(category);
        CREATE INDEX IF NOT EXISTS idx_mk_tenant ON medical_knowledge(tenant_id);
        CREATE TABLE IF NOT EXISTS medical_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id TEXT NOT NULL,
            action TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            ip_address TEXT,
            request_summary TEXT,
            response_status INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mal_tenant ON medical_audit_log(tenant_id, created_at DESC);
    """)
    # 테넌트 키 해싱(보안 P2 M3) — 평문 저장·평문 비교를 해시로 전환하기 위한 컬럼.
    # 기존 행은 첫 인증 시 지연 마이그레이션되므로 HIS 측 작업은 없다.
    for col, typedef in (("api_key_hash", "TEXT"), ("api_key_prefix", "TEXT")):
        try:
            conn.execute(f"ALTER TABLE medical_tenants ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass            # 이미 존재
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mt_key_hash "
                     "ON medical_tenants(api_key_hash)")
        conn.commit()
    except sqlite3.OperationalError as e:
        logger.debug("tenant key hash index skip: %s", e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DISCLAIMER = "\n\n[면책] 이 정보는 AI가 생성한 참고 자료이며, 의료 행위에 해당하지 않습니다. 진단 및 치료 결정은 반드시 담당 의사의 전문적 판단에 따라야 합니다."
DISCLAIMER_EN = "\n\n[Disclaimer] This is AI-generated clinical reference. Final decisions must be made by the attending physician."

# 다국어 면책 문구 (v2.38.0)
DISCLAIMERS = {
    "ko": "[면책] 이 정보는 AI가 생성한 참고 자료이며, 의료 행위에 해당하지 않습니다. 진단 및 치료 결정은 반드시 담당 의사의 전문적 판단에 따라야 합니다.",
    "en": "[Disclaimer] This is AI-generated clinical reference only, not a medical act. All diagnosis and treatment decisions must be made by the attending physician.",
    "zh": "[免责声明] 本信息由AI生成，仅供参考，不构成医疗行为。诊断和治疗决策必须由主治医师做出。",
    "vi": "[Miễn trừ trách nhiệm] Thông tin này do AI tạo ra, chỉ mang tính tham khảo. Mọi quyết định chẩn đoán và điều trị phải do bác sĩ điều trị đưa ra.",
    "ja": "[免責事項] この情報はAIが生成した参考資料であり、医療行為には該当しません。診断及び治療の決定は必ず担当医の専門的判断に従ってください。",
}


def get_disclaimer(lang="ko"):
    """다국어 면책 문구 반환"""
    return DISCLAIMERS.get(lang, DISCLAIMERS["ko"])


def hash_medical_key(key: str) -> str:
    """테넌트 API 키 → sha256 hex. 본 서버 API 키·HR Law 테넌트 키와 동일 방식."""
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()


def _migrate_tenant_key(conn, tenant_id, key):
    """평문 키를 해시로 전환하고 평문을 제거한다(첫 인증 시 1회).

    `api_key`는 레거시 `NOT NULL UNIQUE` 제약이 걸려 있어 NULL·빈 문자열을 넣을 수 없다
    (빈 문자열은 테넌트가 둘 이상이면 UNIQUE 충돌). 그래서 **테넌트별 고유 플레이스홀더**를
    넣는다 — 실제 키는 `med-` 접두라 이 값과 절대 일치하지 않는다.
    """
    try:
        conn.execute(
            "UPDATE medical_tenants SET api_key_hash=?, api_key_prefix=?, api_key=? WHERE id=?",
            (hash_medical_key(key), key[:12], f"hashed:{tenant_id}", tenant_id))
        conn.commit()
        logger.info("medical tenant key 해시 전환 완료: %s", tenant_id)
    except Exception as e:  # noqa: BLE001 — 전환 실패가 인증을 막지 않는다(다음 요청에 재시도)
        logger.warning("medical tenant key 해시 전환 실패(%s): %s", tenant_id, e)


def _resolve_medical_tenant(req):
    key = req.headers.get("X-Medical-Key") or req.args.get("medical_key", "")
    if not key:
        return None
    conn = _get_medical_db()
    # 1) 해시 조회(정상 경로)
    t = conn.execute("SELECT * FROM medical_tenants WHERE api_key_hash=? AND is_active=1",
                     (hash_medical_key(key),)).fetchone()
    if t:
        return dict(t)
    # 2) 레거시 평문 조회 → 지연 마이그레이션(호출자 작업 0·무중단)
    # 🔴 v2.125.18: `api_key` 컬럼에는 마이그레이션이 심은 **플레이스홀더 `hashed:{id}`**가 들어 있다
    #    (`:523`·`:909`·`:1014`). 그 리터럴은 `id`만 알면 유도 가능하고 `/api/medical/tenants`가 id를
    #    임의 키로 노출하므로, 이 분기가 `X-Medical-Key: hashed:{id}`를 **정상 키로 통과시켰다**
    #    (2026-08-25 실측 3/3 테넌트 우회 · PHI 경로 · **키 로테이션으로 안 닫힌다**).
    #    hr_law.py:696이 이미 옳게 한 그대로 — 빈/플레이스홀더 값을 제외한다(같은 클래스 전 경로 쓸기).
    if not key or key.startswith("hashed:"):
        return None
    t = conn.execute("SELECT * FROM medical_tenants "
                     "WHERE api_key=? AND api_key<>'' AND api_key NOT LIKE 'hashed:%' AND is_active=1",
                     (key,)).fetchone()
    if not t:
        return None
    t = dict(t)
    _migrate_tenant_key(conn, t["id"], key)
    return t


def _check_tenant_limit(tenant_id):
    conn = _get_medical_db()
    today = date.today().isoformat()
    row = conn.execute("SELECT query_count FROM medical_usage WHERE tenant_id=? AND date=?",
                       (tenant_id, today)).fetchone()
    tenant = conn.execute("SELECT max_queries_day FROM medical_tenants WHERE id=?", (tenant_id,)).fetchone()
    current = row["query_count"] if row else 0
    limit = tenant["max_queries_day"] if tenant else 100
    return current < limit, current, limit


def _increment_usage(tenant_id, tokens=0):
    conn = _get_medical_db()
    today = date.today().isoformat()
    conn.execute("""
        INSERT INTO medical_usage (tenant_id, date, query_count, token_count) VALUES (?, ?, 1, ?)
        ON CONFLICT(tenant_id, date) DO UPDATE SET query_count = query_count + 1, token_count = token_count + ?
    """, (tenant_id, today, tokens, tokens))
    conn.commit()


def _phi_store_enabled():
    """PHI(환자 진료데이터) 저장 여부 — config medical_phi_store, 기본 False(비저장).

    False(기본)일 때 진료 input/output 전문(medical_consultations)을 저장하지 않고,
    감사 로그(medical_audit_log)의 요청 요약(환자정보 포함 가능)도 미보존한다.
    의료법·개인정보보호법 대응(HIS PHI 비저장 정책). 런타임 토글(재시작 불필요).
    """
    try:
        from core.config import load_config
        return bool(load_config().get("medical_phi_store", False))
    except Exception:
        return False


def _audit_log(tenant_id, action, endpoint, req, status=200):
    conn = _get_medical_db()
    # PHI 비저장 모드: 감사 행(테넌트·액션·엔드포인트·상태)은 유지하되
    # 요청 본문 요약(환자정보 가능)은 미보존
    if _phi_store_enabled():
        summary = (req.json or {}).get("text", "")[:100] if req.json else ""
    else:
        summary = ""
    conn.execute(
        "INSERT INTO medical_audit_log (tenant_id, action, endpoint, ip_address, request_summary, response_status) VALUES (?,?,?,?,?,?)",
        (tenant_id, action, endpoint, req.remote_addr, summary, status))
    conn.commit()


def _save_consultation(tenant_id, action, input_text, output_text, model, tokens, duration_ms, metadata=None):
    # PHI 비저장 모드(기본): 진료 input/output 전문을 DB에 남기지 않음
    if not _phi_store_enabled():
        return None
    conn = _get_medical_db()
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO medical_consultations (id, tenant_id, action, input_text, output_text, model, tokens, duration_ms, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
        (cid, tenant_id, action, input_text, output_text, model, tokens, duration_ms,
         json.dumps(metadata) if metadata else "{}"))
    conn.commit()
    return cid


def _kb_tenant_ok(meta, tenant_id) -> bool:
    """지식문서가 요청 테넌트에 노출 가능한지 — 공유('shared')/레거시(None)/자기 테넌트만 True.
    색인(knowledge/index)은 메타 tenant_id=테넌트id(비공개) 또는 'shared'(공유) 저장.
    레거시(메타 없음=None)는 공유로 간주(기존 공유 지식 보존)."""
    tid = (meta or {}).get("tenant_id")
    return tid in (None, "", "shared", tenant_id)


def _tenant_kb_filter(results, tenant_id):
    """결과 후처리 격리 — 타 테넌트 비공개 문서 제거(벡터/키워드/레거시 공통 보장).
    restrictive where 대신 후처리로 격리해 레거시(메타 없는) 공유문서 오제외를 방지."""
    return [r for r in (results or []) if _kb_tenant_ok(r.get("metadata"), tenant_id)]


# 근거 관련성 게이트 — hybrid_search의 score는 **가중 RRF(순위 융합)**라 유사도 정보가
# 없다(1순위면 항상 ~1.6). 그래서 무의미 입력도 항상 "근거"를 받아 왔고, 호출자가 임계를
# 올려도 걸러지지 않았다(트윈 실측 2026-07: 무작위 토큰 relevance 1.5 = 임상 키워드
# 범위 1.4~1.7과 겹침). 실제 코사인은 깨끗이 갈린다 — 임상 0.57~0.69 vs 무의미
# 0.35~0.39(GPU 실측) → 그 사이(0.45)를 기본 게이트로 둔다.
# 키워드(FTS) 매칭이 있는 항목은 용어가 실제로 문서에 존재하므로 게이트에서 제외한다
# (벡터 상위에 못 들어 vector_score=0인 정확 용어 매칭을 잃지 않기 위함).
_REFERENCE_MIN_SIM_DEFAULT = 0.45


def _drop_weak_matches(results, min_sim=None):
    """벡터 유사도만 낮은(키워드 매칭도 없는) 약한 근거 제거 — 무관 입력 오인용 방지."""
    if min_sim is None:
        try:
            from core.config import load_config
            min_sim = float((load_config() or {}).get(
                "medical_reference_min_similarity", _REFERENCE_MIN_SIM_DEFAULT))
        except Exception:  # noqa: BLE001
            min_sim = _REFERENCE_MIN_SIM_DEFAULT
    if min_sim <= 0:
        return results or []
    kept = []
    for r in results or []:
        if (r.get("keyword_score") or 0) > 0:      # 용어 실매칭 = 유지
            kept.append(r)
        elif (r.get("vector_score") or 0) >= min_sim:
            kept.append(r)
    return kept


import hashlib as _hashlib
from core.cache import TTLCache as _TTLCache
from routes.cache_ctx import RequestAwareTTLCache
from core.llm_resilience import (get_adaptive_timeout, dedup_call,
                                  _make_dedup_key, ollama_breaker)

_medical_cache = RequestAwareTTLCache(maxsize=200, ttl=3600)  # 200 항목, 1시간 TTL


def _json_acceptable(text: str) -> bool:
    """JSON 계약 경로의 캐시 수용 판정 — 파싱되는 객체일 때만 True.

    🔴 **깨진 출력을 캐시하면 일과성 결함이 TTL 동안 고정 오답이 된다.**
    2026-08-10 실측: 재기동 직후 콜드 캐시 상태의 첫 생성이 키 없는 중괄호를 낸
    JSON(`…], {  "drug":`)을 만들었고, 그것이 캐시에 박혀 `/api/medical/predict/
    adverse-drug`가 **3/3 결정적으로** 구조화 필드 없이 응답했다(같은 프롬프트에서
    나이만 바꾸면 정상). 14b는 콜드/웜 캐시 상태에 따라 긴 생성이 갈린다(v2.124.20).
    ⇒ 재시도가 통하도록 **깨진 것만 캐시에서 뺀다**. 응답 자체는 그대로 돌려준다
      (호출자의 `force_json` 복구 여지를 뺏지 않는다).

    📌 `/ask` 실패 캐시(v2.124.85)와 **같은 클래스**가 이 경로에 남아 있었다.

    ⚠️ `force_json`은 **실패해도 dict를 돌려준다**(`{fallback_key: 원문}`). 그래서
    `isinstance(..., dict)`로는 성공/실패를 못 가른다 — 첫 구현이 정확히 그렇게 틀렸고
    회귀 테스트가 잡았다. **센티넬 키**로 폴백 여부를 판별한다.
    """
    if not text:
        return False
    sentinel = "__unparsed_sentinel__"
    try:
        from core.quality import force_json
        parsed = force_json(text, sentinel)
    except Exception:  # noqa: BLE001 — 판정 불가면 종전대로 캐시(완화 방향 아님)
        return True
    if not parsed:
        return False
    return not (isinstance(parsed, dict) and set(parsed.keys()) == {sentinel})


_GEN_FAILED = object()  # _do_generate 실패 표식(문자열과 충돌 불가)


class MedicalGenerationError(RuntimeError):
    """의료 생성 실패 — **문자열이 아니라 예외로** 올린다(연동 검토 회신·2026-09-27).

    종전엔 `("Error: …", 0, ms)`를 반환했고 호출 35곳 중 medical 핸들러는
    `startswith("Error:")`를 보지 않아 실패가 **200+본문 "Error: …"**로 나갔다
    (HIS는 성공 201로 넘김 · 상담 이력에 오류 문구 저장 · 사용량 과금 · 비교/벤치마크는
    `status: ok`로 기록). 반복 클래스 #1. 호출자마다 문자열 검사를 박는 대신 예외 하나로
    35곳을 구조적으로 덮고, 응답 변환은 `app_errorhandler` 한 곳에 둔다.

    - circuit open → 503(재시도 가능) · 그 외(모델 부재·타임아웃·HTTP 오류) → 502
    - v2.125.68: **대기열 거절(Fair Queue busy) → 429 + Retry-After**. 종전엔 502로 나가 HIS가 고장과
      '잠시 후 다시'를 못 갈랐다(2026-09-30 14:38 complication 502×2 — `active=1/3`인데 사용자당 한도 거절).
    - 부분 실패를 허용하는 호출자(비교·벤치마크·배치·앙상블 3번째 모델)는 원래
      `except Exception`으로 받고 있었다 — 이제야 그 분기가 실제로 탄다.
    """

    def __init__(self, reason, *, status=502, duration_ms=0, retry_after=None):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.duration_ms = duration_ms
        self.retry_after = retry_after   # status=429(busy)일 때만 채운다


@medical_bp.app_errorhandler(MedicalGenerationError)
def _medical_generation_failed(e):
    logger.warning("[medical] 생성 실패 → %s: %s", e.status, e.reason)
    if e.status == 429:
        # /api/chat 계열의 기존 429 형식과 같게(core.llm_resilience.busy_payload)
        from core.llm_resilience import busy_payload
        body, headers = busy_payload(e)
        return jsonify(body), 429, headers
    return jsonify({"error": "medical generation failed", "code": "generation_failed",
                    "detail": str(e.reason)[:300]}), e.status


def medical_generation_error_event(e):
    """SSE 경로용 — 헤더가 이미 나간 스트림은 errorhandler가 닿지 않는다."""
    if e.status == 429:
        return {"event": "error", "code": "server_busy", "status": 429,
                "retry_after": e.retry_after, "detail": str(e.reason)[:300]}
    return {"event": "error", "code": "generation_failed", "status": e.status,
            "detail": str(e.reason)[:300]}


def _medical_generate(model, prompt, system, options=None, timeout=None, action="",
                      accept=None):
    """LLM 생성 — 의료용 (캐시 + 적응형 타임아웃 + 중복제거 + 서킷브레이커)

    accept: 응답을 **캐시에 담아도 되는지** 판정하는 콜러블(기본 None=종전 동작).
            JSON 계약 경로는 `_json_acceptable`을 넘겨 깨진 출력의 고정을 막는다.
    """
    opts = options or {}

    # P1-1: 적응형 타임아웃
    if timeout is None:
        timeout = get_adaptive_timeout(model, action)

    # 캐시 조회 (model + system + prompt 해시)
    cache_key = _hashlib.md5(f"{model}:{system[:100]}:{prompt}".encode()).hexdigest()
    cached = _medical_cache.get(cache_key)
    if cached:
        return cached[0], cached[1], 0  # response, tokens, 0ms (캐시 히트)

    # P1-4: 서킷 브레이커 — Ollama 다운 시 즉시 에러
    if not ollama_breaker.can_execute():
        raise MedicalGenerationError("Ollama temporarily unavailable (circuit open)", status=503)

    # P1-3: 요청 중복 제거 — 동일 요청 병합
    dedup_key = _make_dedup_key(model, prompt, system)

    def _do_generate():
        start = time.time()
        try:
            # v2.125.13 D1 이관 — 페이로드 동일(회귀 tests/test_router_migration_hr_medical.py)
            from core import model_router
            res = model_router.generate(
                "medical.generate", prompt, model=model, system=system, options=opts, timeout=timeout,
                adapter=model_router.OllamaAdapter(base=_OLLAMA_BASE, session=_ollama_session))
            if not res.ok:
                # v2.125.15 무성 실패 일괄: HTTP 오류도 전송 예외와 같은 분기(브레이커 failure·Error 반환)
                # — 종전엔 ""가 **성공으로**(브레이커 success·캐시 미저장) 흘렀다
                # v2.125.68: busy면 LLMBusyError로(종류 보존) — 아래 except가 브레이커에서 뺀다
                from core.llm_resilience import result_error
                raise result_error(res, "HTTP %s" % res.status)
            elapsed_ms = int((time.time() - start) * 1000)
            response = res.text
            tokens = res.tokens

            ollama_breaker.record_success()

            # 성공 시 캐시 저장 — ⚠️ "Error로 시작하지 않음"은 성공의 증거가 아니다.
            # 구조 계약이 있는 경로는 accept가 그 계약까지 확인한다(깨진 출력 고정 방지).
            if response and not response.startswith("Error"):
                if accept is None or accept(response):
                    _medical_cache.set(cache_key, (response, tokens))
                else:
                    logger.warning("[medical] 캐시 보류 — 구조 계약 미충족 action=%s model=%s",
                                   action, model)

            return response, tokens, elapsed_ms
        except Exception as e:
            elapsed_ms = int((time.time() - start) * 1000)
            # 🔴 v2.125.68: 대기열 거절은 백엔드 고장이 아니다 — 공용 브레이커(5개 도메인)에 세면
            #    한 클라이언트의 동시 거절 3건이 전 도메인을 30초 503으로 만든다.
            from core.llm_resilience import record_failure_unless_busy, is_busy
            record_failure_unless_busy(e, ollama_breaker)
            # 예외를 dedup 안에서 올리면 대기자는 None을 받는다 — 표식 튜플로 넘기고 밖에서 올린다
            # (4번째 = busy면 retry_after — 대기자도 같은 429를 받는다)
            return (_GEN_FAILED, str(e), elapsed_ms,
                    getattr(e, "retry_after", None) if is_busy(e) else None)

    result = dedup_call(dedup_key, _do_generate)
    if not result:
        raise MedicalGenerationError("dedup wait failed")
    if result[0] is _GEN_FAILED:
        if result[3] is not None:
            raise MedicalGenerationError(result[1], status=429, duration_ms=result[2],
                                         retry_after=result[3])
        raise MedicalGenerationError(result[1], duration_ms=result[2])
    return result


# ---------------------------------------------------------------------------
# System Prompts
# ---------------------------------------------------------------------------

SUMMARIZE_SYSTEM = """당신은 {specialty} 전문의를 보조하는 의료 AI입니다. 반드시 한국어로 답변하세요.

진료 기록을 {format} 형식으로 구조화하세요.

[SOAP 형식 규칙]
- Subjective: 환자 호소, 병력, 주관적 증상
- Objective: 활력징후, 검사 결과, 신체검사 소견
- Assessment: 진단/감별진단, ICD-10 코드 포함
- Plan: 검사 계획, 처방, 추적 관찰

[추가 출력]
- icd_codes: 관련 ICD-10 코드 배열
- key_findings: 핵심 소견 목록
- urgency: low/medium/high/critical

반드시 JSON 형식으로만 출력하세요. 다른 텍스트를 포함하지 마세요.
{{"summary": {{"subjective":"...","objective":"...","assessment":"...","plan":"..."}}, "icd_codes":["..."], "key_findings":["..."], "urgency":"..."}}"""

ANALYZE_SYSTEM = """당신은 임상 전문의를 보조하는 감별진단 AI입니다. 반드시 한국어로 답변하세요.

주어진 임상 데이터를 분석하여 감별진단 상위 3개를 확률순으로 제시하세요.

반드시 아래 JSON 배열 형식으로만 출력하세요. JSON 외 다른 텍스트를 절대 포함하지 마세요.

[
  {{
    "diagnosis": "영문 진단명",
    "diagnosis_ko": "한국어 진단명",
    "icd10": "ICD-10",
    "probability": "high 또는 medium 또는 low",
    "supporting": ["근거1", "근거2"],
    "against": ["반대 근거"],
    "recommended_tests": ["추천 검사"],
    "urgency": "low 또는 medium 또는 high 또는 critical"
  }}
]"""

EXPLAIN_SYSTEM = """당신은 환자에게 의학 정보를 쉽게 설명하는 AI입니다. 반드시 한국어로 답변하세요.

[규칙]
1. 전문 의학 용어를 일반인이 이해할 수 있는 쉬운 말로 바꾸세요
2. 비유나 예시를 활용하세요
3. 핵심 사항은 목록으로 정리하세요
4. 주의사항과 다음 단계를 안내하세요
5. 불안감을 최소화하면서도 정확한 정보를 전달하세요

JSON으로 출력:
{{"explanation":"...", "key_points":["..."], "precautions":["..."], "follow_up":"..."}}"""

DRAFT_SYSTEM = """당신은 의무기록 작성을 보조하는 AI입니다. 반드시 한국어로 작성하세요.

주어진 진료 데이터를 {type} 형식의 의무기록 초안으로 작성하세요.
의학적으로 정확하고 간결한 표현을 사용하세요.
약어는 표준 의학 약어만 사용하세요."""

# P2-4: 전문과별 특화 프롬프트 보강
SPECIALTY_HINTS = {
    "내과": "\n[내과 접근] 감별 시 Common→Uncommon 순서. 대사/내분비/감염/자가면역 감별.",
    "외과": "\n[외과 접근] 수술 적응증 판단 프레임워크. 보존치료 vs 수술 기준 명시.",
    "소아과": "\n[소아과 접근] 연령별 정상 범위 참조. 소아 특이 질환 우선 고려.",
    "응급의학과": "\n[응급 접근] ABCDE 접근법. KTAS 분류. Red flag 증상 우선 배제.",
    "정신건강의학과": "\n[정신과 접근] DSM-5 기준. 자살 위험 평가 포함. PHQ-9/GAD-7 참조.",
    "산부인과": "\n[산부인과 접근] 임신 가능성 항상 고려. 월경력/산과력 확인.",
    "정형외과": "\n[정형외과 접근] Ottawa 규칙. 영상 적응증. 보존치료 우선.",
}

# P2-4: Few-shot 예시 (SOAP)
SOAP_FEW_SHOT = """
[예시 입출력]
입력: "45세 남성, 3일 전부터 가슴 답답함, 운동 시 악화, 흡연 20년"
출력: {"summary": {"subjective": "3일 전부터 흉부 압박감, 운동 시 악화. 흡연력 20갑년.", "objective": "V/S 확인 필요. ECG, Troponin 검사 권장.", "assessment": "불안정 협심증 의심 (I20.0). 감별: GERD, 근골격계 통증", "plan": "ECG 즉시 시행, Troponin I/T, 흉부 X-ray. 양성 시 심장내과 의뢰."}, "icd_codes": ["I20.0"], "key_findings": ["흉부 압박감", "운동 악화", "흡연 20갑년"], "urgency": "high"}
"""


CLINICAL_ANALYZE_SYSTEM = """You are an expert physician with extensive clinical experience. Analyze the clinical presentation and provide a thorough differential diagnosis.

For each diagnosis:
1. State the diagnosis with ICD-10 code
2. Explain supporting evidence from the presentation
3. Note any evidence against this diagnosis
4. Recommend specific workup/tests
5. Assess urgency level

Provide evidence-based reasoning. Cite relevant clinical guidelines when applicable."""

CLINICAL_DRUG_SYSTEM = """You are a clinical pharmacology expert. Provide comprehensive drug information including:
1. Mechanism of action
2. Standard dosing and routes
3. Contraindications
4. Drug interactions (especially clinically significant ones)
5. Monitoring parameters
6. Special populations (renal/hepatic impairment, pregnancy, elderly)

Cite relevant guidelines and evidence."""


# ---------------------------------------------------------------------------
# Tenant Management (Admin)
# ---------------------------------------------------------------------------

@medical_bp.route("/api/medical/tenant/register", methods=["POST"])
@require_auth
def medical_tenant_register():
    """Register a hospital/clinic
    ---
    tags: [Medical - Admin]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [name]
          properties:
            name: {type: string, description: "병원명"}
            plan: {type: string, enum: [basic, standard, enterprise], default: basic}
            specialty: {type: string, description: "전문과목"}
            contact_name: {type: string}
            contact_email: {type: string}
            contact_phone: {type: string}
    responses:
      200:
        description: 등록 성공
        schema:
          type: object
          properties:
            tenant_id: {}
            name: {}
            api_key: {}
            plan: {}
    """
    data = request.json or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400

    plan = data.get("plan", "basic")
    if plan not in ("basic", "standard", "enterprise"):
        return jsonify({"error": "plan must be basic/standard/enterprise"}), 400

    limits = {"basic": (100, 2000, 100), "standard": (500, 10000, 500), "enterprise": (999999, 999999, 999999)}
    max_day, max_month, max_docs = limits[plan]

    tid = str(uuid.uuid4())[:11]
    api_key = f"med-{secrets.token_hex(16)}"

    conn = _get_medical_db()
    conn.execute(
        """INSERT INTO medical_tenants (id, name, api_key, api_key_hash, api_key_prefix, plan, specialty,
           max_queries_day, max_queries_month, max_rag_docs,
           contact_name, contact_email, contact_phone) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        # api_key 컬럼은 레거시 NOT NULL UNIQUE라 플레이스홀더를 넣는다(평문 미저장).
        (tid, name, f"hashed:{tid}", hash_medical_key(api_key), api_key[:12],
         plan, data.get("specialty"), max_day, max_month, max_docs,
         data.get("contact_name"), data.get("contact_email"), data.get("contact_phone")))
    conn.commit()

    return jsonify({"tenant_id": tid, "name": name, "api_key": api_key, "plan": plan})


@medical_bp.route("/api/medical/tenants")
@require_auth
def medical_tenants_list():
    """List all registered hospitals
    ---
    tags: [Medical - Admin]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            tenants: {}
            count: {type: integer}
    """
    conn = _get_medical_db()
    rows = conn.execute(
        "SELECT id, name, plan, specialty, is_active, created_at, api_key, api_key_prefix "
        "FROM medical_tenants ORDER BY created_at DESC"
    ).fetchall()
    tenants = []
    for r in rows:
        d = dict(r)
        # 해시 전환된 행은 저장된 접두를, 아직 전환 전인 레거시 행은 평문 접두를 쓴다.
        prefix = d.get("api_key_prefix") or (d.get("api_key") or "")[:12]
        d["api_key_prefix"] = prefix + "..."
        d.pop("api_key", None)
        tenants.append(d)
    return jsonify({"tenants": tenants, "count": len(tenants)})


@medical_bp.route("/api/medical/tenant/<tenant_id>", methods=["PUT"])
@require_auth
def medical_tenant_update(tenant_id):
    """Update hospital info
    ---
    tags: [Medical - Admin]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "tenant not found"}}
    """
    data = request.json or {}
    conn = _get_medical_db()
    t = conn.execute("SELECT id FROM medical_tenants WHERE id=?", (tenant_id,)).fetchone()
    if not t:
        return jsonify({"error": "tenant not found"}), 404

    fields = []
    params = []
    for k in ("name", "plan", "specialty", "contact_name", "contact_email", "contact_phone",
              "max_queries_day", "max_queries_month", "max_rag_docs", "is_active"):
        if k in data:
            fields.append(f"{k}=?")
            params.append(data[k])

    if fields:
        fields.append("updated_at=datetime('now')")
        params.append(tenant_id)
        conn.execute(f"UPDATE medical_tenants SET {','.join(fields)} WHERE id=?", params)
        conn.commit()

    return jsonify({"ok": True})


@medical_bp.route("/api/medical/tenant/<tenant_id>", methods=["DELETE"])
@require_auth
def medical_tenant_delete(tenant_id):
    """Delete hospital and all data
    ---
    tags: [Medical - Admin]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
        schema:
          type: object
          properties:
            ok: {type: boolean}
            deleted: {}
            kb_docs: {type: integer}
    """
    conn = _get_medical_db()
    # 테넌트 지식문서 id 수집 → SQL 삭제 후 벡터/FTS 동반 정리(고아 벡터 방지)
    kb_ids = [r[0] for r in conn.execute(
        "SELECT id FROM medical_knowledge WHERE tenant_id=?", (tenant_id,)).fetchall()]
    conn.execute("DELETE FROM medical_consultations WHERE tenant_id=?", (tenant_id,))
    conn.execute("DELETE FROM medical_usage WHERE tenant_id=?", (tenant_id,))
    conn.execute("DELETE FROM medical_knowledge WHERE tenant_id=?", (tenant_id,))
    conn.execute("DELETE FROM medical_audit_log WHERE tenant_id=?", (tenant_id,))
    conn.execute("DELETE FROM medical_tenants WHERE id=?", (tenant_id,))
    conn.commit()
    if kb_ids:
        try:
            from client.rag import delete_document, fts_delete
            for _did in kb_ids:
                delete_document(_did, collection_name=MEDICAL_KB_COLLECTION)
                fts_delete(_did, collection=MEDICAL_KB_COLLECTION)
        except Exception as e:
            logger.warning(f"RAG tenant purge failed (SQLite는 삭제됨): {e}")
    return jsonify({"ok": True, "deleted": tenant_id, "kb_docs": len(kb_ids)})


@medical_bp.route("/api/medical/tenant/<tenant_id>/regenerate-key", methods=["POST"])
@require_auth
def medical_tenant_regenerate_key(tenant_id):
    """Regenerate API key
    ---
    tags: [Medical - Admin]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "tenant not found"}}
    """
    conn = _get_medical_db()
    new_key = f"med-{secrets.token_hex(16)}"
    cur = conn.execute(
        "UPDATE medical_tenants SET api_key=?, api_key_hash=?, api_key_prefix=?, "
        "updated_at=datetime('now') WHERE id=?",
        (f"hashed:{tenant_id}", hash_medical_key(new_key), new_key[:12], tenant_id))
    conn.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "tenant not found"}), 404
    return jsonify({"api_key": new_key})


@medical_bp.route("/api/medical/tenant/<tenant_id>/stats")
@require_auth
def medical_tenant_stats(tenant_id):
    """Hospital usage statistics
    ---
    tags: [Medical - Admin]
    security: [{Bearer: []}]
    responses:
      200:
        description: "요청 성공"
      404:
        description: "대상을 찾을 수 없음"
        schema:
          type: object
          properties: {error: {type: string, example: "tenant not found"}}
    """
    conn = _get_medical_db()
    t = conn.execute(
        "SELECT id, name, plan, specialty, is_active, created_at, updated_at, max_queries_day, max_queries_month, max_rag_docs FROM medical_tenants WHERE id=?",
        (tenant_id,)).fetchone()
    if not t:
        return jsonify({"error": "tenant not found"}), 404

    today = date.today().isoformat()
    today_usage = conn.execute("SELECT query_count, token_count FROM medical_usage WHERE tenant_id=? AND date=?",
                               (tenant_id, today)).fetchone()
    month_prefix = today[:7]
    month_usage = conn.execute(
        "SELECT COALESCE(SUM(query_count),0) as queries, COALESCE(SUM(token_count),0) as tokens FROM medical_usage WHERE tenant_id=? AND date LIKE ?",
        (tenant_id, f"{month_prefix}%")).fetchone()

    by_action = conn.execute(
        "SELECT action, COUNT(*) as count FROM medical_consultations WHERE tenant_id=? GROUP BY action",
        (tenant_id,)).fetchall()

    return jsonify({
        "tenant": dict(t),
        "today": {"queries": today_usage["query_count"] if today_usage else 0,
                  "tokens": today_usage["token_count"] if today_usage else 0},
        "month": {"queries": month_usage["queries"], "tokens": month_usage["tokens"]},
        "by_action": {r["action"]: r["count"] for r in by_action},
    })


# ---------------------------------------------------------------------------
# Clinical Support APIs (Tenant Auth)
# ---------------------------------------------------------------------------

def _require_tenant():
    """테넌트 인증 + 제한 확인"""
    tenant = _resolve_medical_tenant(request)
    if not tenant:
        return None, (jsonify({"error": "X-Medical-Key required"}), 401)
    ok, current, limit = _check_tenant_limit(tenant["id"])
    if not ok:
        return None, (jsonify({"error": f"daily limit exceeded ({current}/{limit})"}), 429)
    return tenant, None


@medical_bp.route("/api/medical/summarize", methods=["POST"])
def medical_summarize():
    """Summarize clinical notes (SOAP format)
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "진료 기록 원문"}
            format: {type: string, default: "soap", enum: [soap, brief, detailed]}
            specialty: {type: string, default: "내과"}
            model: {type: string, default: "qwen2.5:14b"}
            split_soap: {type: boolean, default: false, description: "S/O/A/P 각각 별도 LLM 호출 (정확도 향상, 응답 느림)"}
            lang: {type: string, default: "ko", enum: [ko, en, zh, vi, ja], description: "면책 문구 언어"}
    responses:
      200:
        description: SOAP 요약 결과 (icd_validation, quality 포함)
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    fmt = data.get("format", "soap")
    specialty = data.get("specialty", "내과")
    model = _get_medical_model("summarize", data.get("model"))
    split_soap = data.get("split_soap", False)

    opts = {"temperature": 0.2, "num_predict": 2000}
    total_tokens = 0
    total_duration = 0

    # P2-4: 전문과별 프롬프트 보강
    specialty_hint = SPECIALTY_HINTS.get(specialty, "")

    if split_soap and fmt.lower() == "soap":
        # --- SOAP 분리 생성 (v2.38.0) — 각 섹션 별도 LLM 호출 ---
        soap_sections = {}
        soap_fields = {
            "subjective": f"다음 진료 기록에서 Subjective(주관적 증상, 환자 호소, 병력)만 추출하세요. {specialty} 관점. 한국어. 텍스트만 출력:\n\n",
            "objective": f"다음 진료 기록에서 Objective(활력징후, 검사 결과, 신체검사 소견)만 추출하세요. {specialty} 관점. 한국어. 텍스트만 출력:\n\n",
            "assessment": f"다음 진료 기록에서 Assessment(진단, 감별진단, ICD-10 코드)만 추출하세요. {specialty} 관점. 한국어. 텍스트만 출력:\n\n",
            "plan": f"다음 진료 기록에서 Plan(검사 계획, 처방, 추적 관찰)만 추출하세요. {specialty} 관점. 한국어. 텍스트만 출력:\n\n",
        }
        section_opts = {"temperature": 0.2, "num_predict": 500}

        # SOAP 4섹션 + 메타데이터 병렬 생성 (v2.39.2 — 12초→4초)
        from concurrent.futures import ThreadPoolExecutor, as_completed

        section_system = f"당신은 {specialty} 전문의를 보조하는 의료 AI입니다. 반드시 한국어로 답변하세요. 요청된 섹션만 간결하게 출력하세요."
        meta_prompt = f"다음 진료 기록에서 ICD-10 코드, 핵심 소견, 긴급도를 추출하세요.\nJSON만 출력: {{\"icd_codes\":[\"...\"], \"key_findings\":[\"...\"], \"urgency\":\"low/medium/high/critical\"}}\n\n{text}"

        def _gen_section(name, prompt_prefix):
            # not-json-contract: SOAP 섹션은 산문이다(뒤의 force_json은 _meta 몫)
            return name, _medical_generate(model, prompt_prefix + text, section_system, section_opts)

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_gen_section, name, prefix) for name, prefix in soap_fields.items()]
            meta_future = pool.submit(lambda: ("_meta", _medical_generate(model, meta_prompt,
                "의료 데이터 추출 전문가. JSON만 출력.", section_opts,
                accept=_json_acceptable)))
            futures.append(meta_future)

            for f in as_completed(futures):
                name, (resp, toks, dur) = f.result()
                if name == "_meta":
                    meta_resp = resp
                else:
                    soap_sections[name] = resp.strip()
                total_tokens += toks
                total_duration += dur

        # v2.125.72: 메타는 하위 호출 — SOAP 절은 살리고 메타만 실패로 표시한다.
        #   종전엔 파싱 실패 시 `urgency: "medium"`을 **지어내** 정상값처럼 냈다.
        from core.quality import parse_json_strict
        meta = parse_json_strict(meta_resp)
        meta_ok = isinstance(meta, dict)
        if not meta_ok:
            logger.warning("[medical] summarize 메타 구조 미충족 — 절 단위 실패: model=%s len=%d", model, len(meta_resp or ""))
            meta = {}
        result = {
            "summary": soap_sections,
            "icd_codes": meta.get("icd_codes", []),
            "key_findings": meta.get("key_findings", []),
            "urgency": meta.get("urgency", "medium") if meta_ok else None,
            "split_soap": True,
        }
        if not meta_ok:
            result["meta_error"] = "model output is not the promised JSON"
        retries = 0
        passed = True   # split 경로는 validator를 돌리지 않는다(종전 validated=True 유지)
    else:
        # --- 기존 단일 호출 (하위 호환) ---
        system = SUMMARIZE_SYSTEM.format(specialty=specialty, format=fmt.upper())
        system += specialty_hint + SOAP_FEW_SHOT  # P2-4

        response, total_tokens, total_duration, retries, passed = generate_with_validation_ex(
            generate_fn=lambda p, s, o: _medical_generate(model, p, s, o or opts, action="summarize", accept=_json_acceptable),
            prompt=text, system=system, options=opts,
            validators=[validate_soap_fields],
            max_retries=2,
        )

        # v2.125.72: 주 결과 — 파싱 못 하면 502(종전엔 `{"raw": 원문}`이 200으로 나가 **상담 이력에 저장·과금**됐다).
        from core.quality import parse_json_strict
        result = parse_json_strict(response)
        if not isinstance(result, dict):
            logger.warning("[medical] summarize 구조 미충족 → 502: model=%s len=%d", model, len(response or ""))
            raise MedicalGenerationError("summarize: model output is not the promised JSON", status=502)

    # ICD-10 코드 검증 (v2.37.0) — KCD DB 대조
    icd_codes = result.get("icd_codes", [])
    if icd_codes:
        icd_validation = validate_icd_codes(icd_codes)
        result["icd_validation"] = icd_validation
        if icd_validation["valid"]:
            result["icd_codes"] = [v["code"] for v in icd_validation["valid"]]

    # P2-1: 의료 환각 감지
    from core.medical_validator import validate_medical_response
    hallucination_check = validate_medical_response(result, action="summarize")
    result["hallucination_check"] = {
        "passed": hallucination_check["passed"],
        "warnings": hallucination_check["warnings"],
    }

    result["quality"] = {"retries": retries, "validated": passed}
    result["disclaimer"] = get_disclaimer(data.get("lang", "ko"))
    result["model"] = model
    result["tokens"] = total_tokens
    result["duration_ms"] = total_duration

    # 한국어 자연화 품질 체크 (SOAP 텍스트 필드)
    try:
        check_text = ""
        if isinstance(result.get("summary"), dict):
            check_text = " ".join(v for v in result["summary"].values() if isinstance(v, str))
        elif isinstance(result.get("summary"), str):
            check_text = result["summary"]
        elif isinstance(result.get("raw"), str):
            check_text = result["raw"]
        if check_text and len(check_text) >= 50:
            qr = compute_korean_quality(check_text, genre="medical")
            result["quality"]["humanize"] = get_quality_summary(qr)
    except Exception:
        pass

    cid = _save_consultation(tenant["id"], "summarize", text[:500], json.dumps(result, ensure_ascii=False)[:2000],
                             model, total_tokens, total_duration)
    _increment_usage(tenant["id"], total_tokens)
    _audit_log(tenant["id"], "summarize", "/api/medical/summarize", request)

    result["consultation_id"] = cid
    return jsonify(result)


@medical_bp.route("/api/medical/summarize/stream", methods=["POST"])
def medical_summarize_stream():
    """Summarize clinical notes (streaming)
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "text is required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    specialty = data.get("specialty", "내과")
    model = _get_medical_model("summarize", data.get("model"))
    system = SUMMARIZE_SYSTEM.format(specialty=specialty, format="SOAP")

    def generate():
        try:
            r = _ollama_session.post(f"{_OLLAMA_BASE}/api/generate", json={
                "model": model, "prompt": text, "system": system,
                "stream": True, "options": {"temperature": 0.2, "num_predict": 2000},
            }, stream=True, timeout=120)
            for line in r.iter_lines():
                if line:
                    yield line.decode() + "\n"
        except Exception as e:
            yield json.dumps({"error": str(e)}) + "\n"

    _increment_usage(tenant["id"])
    _audit_log(tenant["id"], "summarize_stream", "/api/medical/summarize/stream", request)
    return Response(generate(), mimetype="application/x-ndjson")


@medical_bp.route("/api/medical/analyze", methods=["POST"])
def medical_analyze():
    """Differential diagnosis from clinical data
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            symptoms: {type: array, items: {type: string}}
            vitals: {type: object}
            labs: {type: object}
            history: {type: string}
            age: {type: integer}
            sex: {type: string, enum: [M, F]}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "at least one clinical data field required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    symptoms = data.get("symptoms", data.get("patient_text", ""))
    model = _get_medical_model("analyze", data.get("model"), input_text=symptoms)

    prompt_parts = []
    if data.get("age"):
        prompt_parts.append(f"Patient: {data['age']}yo {data.get('sex', '')}")
    if data.get("symptoms"):
        prompt_parts.append(f"Symptoms: {', '.join(data['symptoms'])}")
    if data.get("vitals"):
        prompt_parts.append(f"Vitals: {json.dumps(data['vitals'])}")
    if data.get("labs"):
        prompt_parts.append(f"Labs: {json.dumps(data['labs'])}")
    if data.get("history"):
        prompt_parts.append(f"History: {data['history']}")

    prompt = "\n".join(prompt_parts)
    if not prompt.strip():
        return jsonify({"error": "at least one clinical data field required"}), 400

    opts = {"temperature": 0.2, "num_predict": 2000}

    # Validated generation: JSON 배열 검증 + 자동 재시도
    response, tokens, duration_ms, retries, passed = generate_with_validation_ex(
        generate_fn=lambda p, s, o: _medical_generate(model, p, s, o or opts, accept=_json_acceptable),
        prompt=prompt, system=ANALYZE_SYSTEM, options=opts,
        validators=[validate_json_array_response],
        max_retries=2,
    )

    # Parse differential — v2.125.72: 파싱 실패면 502. 종전 `force_json_array`는 실패 시 `[]`를 줘
    #   **'감별진단 없음'**으로 읽히는 200이 저장·과금됐다(정상 JSON `[]`는 여전히 성공).
    from core.quality import parse_json_array_strict
    differential = parse_json_array_strict(response)
    if differential is None:
        logger.warning("[medical] analyze 구조 미충족 → 502: model=%s len=%d", model, len(response or ""))
        raise MedicalGenerationError("analyze: model output is not the promised JSON", status=502)

    # ICD-10 검증 — 감별진단 결과에서 ICD 코드 추출 후 KCD DB 대조
    all_icd = []
    for dx in differential:
        if isinstance(dx, dict):
            for key in ("icd10", "icd_code", "code", "icd"):
                if key in dx and dx[key]:
                    all_icd.append(str(dx[key]))
                    break
    icd_validation = validate_icd_codes(all_icd) if all_icd else None

    result = {
        "differential_diagnosis": differential,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "disclaimer": DISCLAIMER.strip(),
        "quality": {"retries": retries, "validated": passed},
    }
    if icd_validation:
        result["icd_validation"] = icd_validation

    cid = _save_consultation(tenant["id"], "analyze", prompt[:500],
                             json.dumps(result, ensure_ascii=False)[:2000], model, tokens, duration_ms)
    _increment_usage(tenant["id"], tokens)
    _audit_log(tenant["id"], "analyze", "/api/medical/analyze", request)
    result["consultation_id"] = cid
    return jsonify(result)


@medical_bp.route("/api/medical/reference", methods=["POST"])
def medical_reference():
    """Medical reference with RAG
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [question]
          properties:
            question: {type: string}
            category: {type: string, enum: [guideline, drug, procedure, disease, icd10]}
            n_context: {type: integer, default: 5}
            model: {type: string, default: "qwen2.5:14b"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "question is required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    model = _get_medical_model("reference", data.get("model"), input_text=question)
    category = data.get("category")
    n_context = min(data.get("n_context", 5), 10)

    # P2-3: Hybrid Search (벡터 + 키워드 + 적응적 컨텍스트)
    try:
        from client.rag import hybrid_search, build_context_with_citations
        # 질문 복잡도에 따른 적응적 컨텍스트 크기
        max_ctx_chars = 5000 if len(question) > 50 else 3000
        hybrid_results = hybrid_search(
            question, n_results=n_context,
            collection_name="medical_knowledge",
            category=category,
            tenant=tenant["id"],  # FTS 단계 격리(v2.88.3)
        )
        hybrid_results = _tenant_kb_filter(hybrid_results, tenant["id"])
        hybrid_results = _drop_weak_matches(hybrid_results)
        ctx = build_context_with_citations(hybrid_results, max_chars=max_ctx_chars)
        context = ctx["context"]
        sources = []
        for c in ctx["citations"]:
            sources.append({
                "id": c["id"],
                "title": c["title"],
                "source": c.get("source", ""),
                "version": c.get("version", ""),
                "category": c.get("category", ""),
                # relevance=가중 RRF×100(순위 융합 — 하위호환 유지). 유사도가 아니므로
                # 임계 판단에는 쓸 수 없다. confidence=벡터 코사인(0~1)이 그 용도.
                "relevance": round(c.get("score", 0) * 100, 1),
                "confidence": round(c.get("vector_score", 0) or 0, 3),
                "citation_idx": c["idx"],
            })
    except Exception as e:
        # Fallback: SQL (RAG 미가용 시에도 자문 경로 무중단 — tenant 격리는 WHERE로 보장)
        logger.warning(f"Hybrid search failed, falling back to SQL: {e}")
        conn = _get_medical_db()
        if category:
            rows = conn.execute(
                "SELECT id, title, content, source, category FROM medical_knowledge WHERE (tenant_id IS NULL OR tenant_id=?) AND category=? LIMIT ?",
                (tenant["id"], category, n_context)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, content, source, category FROM medical_knowledge WHERE (tenant_id IS NULL OR tenant_id=?) LIMIT ?",
                (tenant["id"], n_context)).fetchall()
        context = ""
        sources = []
        for r in rows:
            context += f"\n[{r['title']}] {r['content'][:500]}\n"
            sources.append({"id": r["id"], "title": r["title"], "source": r["source"], "category": r["category"]})

    system = f"""당신은 의학 정보 전문 AI입니다. 반드시 한국어로 답변하세요.
아래 참조 자료를 근거로 정확하게 답변하세요. 확실하지 않으면 "확인이 필요합니다"라고 하세요.
답변에 참조 자료 번호를 [1], [2] 형식으로 인용하세요.

[참조 자료]
{context}"""

    response, tokens, duration_ms = _medical_generate(
        model, question, system, {"temperature": 0.3, "num_predict": 1500})

    result = {
        "answer": response + DISCLAIMER,
        "sources": sources,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
    }

    cid = _save_consultation(tenant["id"], "reference", question,
                             response[:2000], model, tokens, duration_ms)
    _increment_usage(tenant["id"], tokens)
    _audit_log(tenant["id"], "reference", "/api/medical/reference", request)
    result["consultation_id"] = cid
    return jsonify(result)


@medical_bp.route("/api/medical/explain", methods=["POST"])
def medical_explain():
    """Generate patient explanation
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [diagnosis]
          properties:
            diagnosis: {type: string}
            treatment_plan: {type: string}
            patient_level: {type: string, default: "general", enum: [general, educated, elderly, child]}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "diagnosis is required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    diagnosis = data.get("diagnosis", "").strip()
    if not diagnosis:
        return jsonify({"error": "diagnosis is required"}), 400

    treatment = data.get("treatment_plan", "")
    level = data.get("patient_level", "general")
    model = _get_medical_model("explain", data.get("model"), input_text=diagnosis)

    prompt = f"진단: {diagnosis}"
    if treatment:
        prompt += f"\n치료 계획: {treatment}"
    prompt += f"\n설명 대상: {level}"

    opts = {"temperature": 0.3, "num_predict": 1500}

    # Validated generation: JSON 검증 + 자동 재시도
    response, tokens, duration_ms, retries, passed = generate_with_validation_ex(
        generate_fn=lambda p, s, o: _medical_generate(model, p, s, o or opts, accept=_json_acceptable),
        prompt=prompt, system=EXPLAIN_SYSTEM, options=opts,
        validators=[validate_json_response],
        max_retries=2,
    )

    # v2.125.72: 설명은 원문 텍스트도 쓸모가 있어(폴백 키 `explanation`이 프롬프트 필드와 같다) 저하를 **유지**하되
    #   표시한다(추가 필드 — 기존 호출자 무영향). 종전엔 구조화 성공과 구분이 없었다.
    from core.quality import parse_json_strict
    parse_ok = isinstance(parse_json_strict(response), dict)
    if not parse_ok:
        logger.warning("[medical] explain 구조 미충족 — 텍스트로 저하: model=%s len=%d", model, len(response or ""))
    result = force_json(response, "explanation")
    if not isinstance(result, dict):
        result = {"explanation": str(result)}
    result["parse_ok"] = parse_ok

    result["disclaimer"] = get_disclaimer(data.get("lang", "ko"))
    result["model"] = model
    result["quality"] = {"retries": retries, "validated": passed}

    # 한국어 자연화 품질 체크
    try:
        explain_text = result.get("explanation", result.get("raw", ""))
        if isinstance(explain_text, str) and len(explain_text) >= 50:
            qr = compute_korean_quality(explain_text, genre="medical")
            result["quality"]["humanize"] = get_quality_summary(qr)
    except Exception:
        pass

    cid = _save_consultation(tenant["id"], "explain", diagnosis,
                             json.dumps(result, ensure_ascii=False)[:2000], model, tokens, duration_ms)
    _increment_usage(tenant["id"], tokens)
    _audit_log(tenant["id"], "explain", "/api/medical/explain", request)
    result["consultation_id"] = cid
    return jsonify(result)


@medical_bp.route("/api/medical/draft", methods=["POST"])
def medical_draft():
    """Generate medical record draft
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [type, data]
          properties:
            type: {type: string, enum: [progress_note, discharge_summary, referral, consultation]}
            data: {type: object}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "data is required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    doc_type = data.get("type", "progress_note")
    doc_data = data.get("data", {})
    model = _get_medical_model("draft", data.get("model"))

    if not doc_data:
        return jsonify({"error": "data is required"}), 400

    system = DRAFT_SYSTEM.format(type=doc_type)
    prompt = json.dumps(doc_data, ensure_ascii=False)

    response, tokens, duration_ms = _medical_generate(
        model, prompt, system, {"temperature": 0.2, "num_predict": 2000})

    result = {
        "draft": response,
        "type": doc_type,
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "disclaimer": DISCLAIMER.strip(),
    }

    cid = _save_consultation(tenant["id"], "draft", prompt[:500], response[:2000], model, tokens, duration_ms)
    _increment_usage(tenant["id"], tokens)
    _audit_log(tenant["id"], "draft", "/api/medical/draft", request)
    result["consultation_id"] = cid
    return jsonify(result)


@medical_bp.route("/api/medical/history")
def medical_history():
    """Consultation history
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: query
        name: limit
        type: integer
        default: 50
      - in: query
        name: action
        type: string
    responses:
      200:
        description: "요청 성공"
    """
    tenant, err = _require_tenant()
    if err:
        return err

    limit = min(int(request.args.get("limit", 50)), 200)
    action = request.args.get("action")

    conn = _get_medical_db()
    if action:
        rows = conn.execute(
            "SELECT id, action, input_text, output_text, model, tokens, duration_ms, created_at FROM medical_consultations WHERE tenant_id=? AND action=? ORDER BY created_at DESC LIMIT ?",
            (tenant["id"], action, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, action, input_text, output_text, model, tokens, duration_ms, created_at FROM medical_consultations WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
            (tenant["id"], limit)).fetchall()

    return jsonify({"history": [dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# Clinical (English, medical models)
# ---------------------------------------------------------------------------

@medical_bp.route("/api/medical/clinical/analyze", methods=["POST"])
def medical_clinical_analyze():
    """Clinical differential diagnosis (English medical model)
    ---
    tags: [Medical - Clinical EN]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          properties:
            symptoms: {type: array, items: {type: string}}
            vitals: {type: object}
            labs: {type: object}
            history: {type: string}
            age: {type: integer}
            sex: {type: string}
            translate: {type: boolean, default: false}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "at least one clinical data field required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    model = "medllama2"
    translate = data.get("translate", False)

    prompt_parts = []
    if data.get("age"):
        prompt_parts.append(f"Patient: {data['age']}yo {data.get('sex', '')}")
    if data.get("symptoms"):
        prompt_parts.append(f"Symptoms: {', '.join(data['symptoms'])}")
    if data.get("vitals"):
        prompt_parts.append(f"Vitals: {json.dumps(data['vitals'])}")
    if data.get("labs"):
        prompt_parts.append(f"Labs: {json.dumps(data['labs'])}")
    if data.get("history"):
        prompt_parts.append(f"History: {data['history']}")

    prompt = "\n".join(prompt_parts)
    if not prompt.strip():
        return jsonify({"error": "at least one clinical data field required"}), 400

    prompt += "\n\nProvide top 3 differential diagnoses with evidence, recommended tests, and urgency."

    response_en, tokens, duration_ms = _medical_generate(
        model, prompt, CLINICAL_ANALYZE_SYSTEM, {"temperature": 0.3, "num_predict": 1500})

    result = {
        "analysis": {"en": response_en},
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "disclaimer": DISCLAIMER_EN.strip(),
    }

    if translate:
        ko_response, ko_tokens, ko_ms = _medical_generate(
            LIGHT_MODEL,
            f"다음 영문 의학 분석을 한국어로 번역하세요. 의학 용어는 한국어(영어) 형식으로 표기하세요:\n\n{response_en}",
            "당신은 의학 전문 번역가입니다. 정확하고 자연스러운 한국어로 번역하세요.",
            {"temperature": 0.2, "num_predict": 2000})
        result["analysis"]["ko"] = ko_response
        result["translated_by"] = LIGHT_MODEL
        tokens += ko_tokens
        duration_ms += ko_ms

    cid = _save_consultation(tenant["id"], "clinical_analyze", prompt[:500],
                             response_en[:2000], model, tokens, duration_ms)
    _increment_usage(tenant["id"], tokens)
    _audit_log(tenant["id"], "clinical_analyze", "/api/medical/clinical/analyze", request)
    result["consultation_id"] = cid
    return jsonify(result)


@medical_bp.route("/api/medical/clinical/drug", methods=["POST"])
def medical_clinical_drug():
    """Drug information (English medical model)
    ---
    tags: [Medical - Clinical EN]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [query]
          properties:
            query: {type: string, description: "약물 관련 질문 (영어)"}
            translate: {type: boolean, default: false}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "query is required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400

    model = "meditron"
    translate = data.get("translate", False)

    response_en, tokens, duration_ms = _medical_generate(
        model, query, CLINICAL_DRUG_SYSTEM, {"temperature": 0.2, "num_predict": 1500})

    result = {
        "answer": {"en": response_en},
        "model": model,
        "tokens": tokens,
        "duration_ms": duration_ms,
        "disclaimer": DISCLAIMER_EN.strip(),
    }

    if translate:
        ko_response, ko_tokens, ko_ms = _medical_generate(
            LIGHT_MODEL,
            f"다음 영문 약물 정보를 한국어로 번역하세요. 약물명과 의학 용어는 한국어(영어) 형식으로 표기하세요:\n\n{response_en}",
            "당신은 약학 전문 번역가입니다. 정확하게 번역하세요.",
            {"temperature": 0.2, "num_predict": 2000})
        result["answer"]["ko"] = ko_response
        result["translated_by"] = LIGHT_MODEL

    cid = _save_consultation(tenant["id"], "clinical_drug", query,
                             response_en[:2000], model, tokens, duration_ms)
    _increment_usage(tenant["id"], tokens)
    _audit_log(tenant["id"], "clinical_drug", "/api/medical/clinical/drug", request)
    result["consultation_id"] = cid
    return jsonify(result)


@medical_bp.route("/api/medical/clinical/translate", methods=["POST"])
def medical_clinical_translate():
    """Translate English clinical text to Korean
    ---
    tags: [Medical - Clinical EN]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [text]
          properties:
            text: {type: string, description: "영문 의학 텍스트"}
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "text is required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    translate_model = _get_medical_model("translate", data.get("model"))
    response, tokens, duration_ms = _medical_generate(
        translate_model,
        f"다음 영문 의학 텍스트를 한국어로 번역하세요. 의학 용어는 한국어(영어) 형식으로 표기하세요:\n\n{text}",
        "당신은 의학 전문 번역가입니다. 정확하고 자연스러운 한국어로 번역하세요. 의학 용어의 정확성을 최우선으로 하세요.",
        {"temperature": 0.2, "num_predict": 2000})

    _increment_usage(tenant["id"], tokens)
    return jsonify({"translation": response, "model": translate_model, "tokens": tokens, "duration_ms": duration_ms})


# ---------------------------------------------------------------------------
# Knowledge Management
# ---------------------------------------------------------------------------

@medical_bp.route("/api/medical/knowledge/index", methods=["POST"])
def medical_knowledge_index():
    """Index medical document
    ---
    tags: [Medical - Knowledge]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
    responses:
      200:
        description: "요청 성공"
      400:
        description: "잘못된 요청 — 필수 필드 누락 또는 검증 실패"
        schema:
          type: object
          properties: {error: {type: string, example: "title and content required"}}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    title = data.get("title", "").strip()
    content = data.get("content", "").strip()
    category = data.get("category", "guideline")
    if not title or not content:
        return jsonify({"error": "title and content required"}), 400

    doc_id = data.get("id", str(uuid.uuid4()))
    is_shared = data.get("shared", False)

    conn = _get_medical_db()
    conn.execute(
        "INSERT OR REPLACE INTO medical_knowledge (id, tenant_id, category, title, content, source, version, metadata) VALUES (?,?,?,?,?,?,?,?)",
        (doc_id, None if is_shared else tenant["id"], category, title, content,
         data.get("source"), data.get("version"), json.dumps(data.get("metadata", {}))))
    conn.commit()

    # Auto-embed into 통합 RAG store (client.rag: vector_db 벡터 + FTS5 키워드 — 검색 경로와 동일)
    try:
        from client.rag import index_document, fts_index
        doc_text = f"{title}\n{content}"
        index_document(doc_id, doc_text, metadata={
            "category": category,
            "title": title,
            "source": data.get("source", ""),
            "tenant_id": tenant["id"] if not is_shared else "shared",
        }, collection_name=MEDICAL_KB_COLLECTION)
        fts_index(doc_id, doc_text, title=title, category=category,
                  collection=MEDICAL_KB_COLLECTION,
                  tenant=tenant["id"] if not is_shared else "shared")
    except Exception as e:
        logger.warning(f"RAG index failed (SQLite 원본은 저장됨): {e}")

    return jsonify({"ok": True, "doc_id": doc_id})


@medical_bp.route("/api/medical/knowledge/search", methods=["POST"])
def medical_knowledge_search():
    """Search medical knowledge (Hybrid: vector + keyword + reranking)

    `medical_knowledge` 컬렉션 하이브리드 검색 — 벡터 + 키워드 + 재랭킹.
    의료법·고시는 별도 코퍼스이므로 `POST /api/medical-law/ask`를 쓴다.
    ---
    tags: [Medical - Knowledge]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        schema:
          type: object
          properties:
            query: {type: string, description: "검색어"}
            category: {type: string}
            limit: {type: integer, default: 10}
            mode: {type: string, enum: [hybrid, vector, keyword, sql], default: hybrid, description: "검색 모드"}
            rerank: {type: boolean, default: false, description: "LLM reranking 활성화"}
    responses:
      200:
        description: 검색 결과
        schema:
          type: object
          properties:
            results: {type: array}
            count: {type: integer}
            mode: {type: string, description: "hybrid | hybrid+rerank | vector | keyword"}
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    query = data.get("query", "").strip()
    category = data.get("category")
    limit = min(data.get("limit", 10), 50)
    mode = data.get("mode", "hybrid")
    rerank = data.get("rerank", False)

    # P2-3: Hybrid Search (기본 모드)
    if mode == "hybrid" and query:
        from client.rag import hybrid_search, build_context_with_citations
        results = hybrid_search(
            query, n_results=limit,
            collection_name="medical_knowledge",
            category=category,
            rerank=rerank,
            tenant=tenant["id"],  # FTS 단계 격리(v2.88.3)
        )
        results = _tenant_kb_filter(results, tenant["id"])
        return jsonify({
            "results": results,
            "count": len(results),
            "mode": "hybrid" + ("+rerank" if rerank else ""),
        })

    if mode == "vector" and query:
        from client.rag import search as vector_search
        results = vector_search(query, n_results=limit, collection_name="medical_knowledge")
        results = _tenant_kb_filter(results, tenant["id"])
        return jsonify({"results": results, "count": len(results), "mode": "vector"})

    if mode == "keyword" and query:
        from client.rag import fts_search
        results = fts_search(query, n_results=limit, collection="medical_knowledge",
                             category=category, tenant=tenant["id"])
        return jsonify({"results": results, "count": len(results), "mode": "keyword"})

    # Fallback: SQL LIKE 검색
    conn = _get_medical_db()
    where = "(tenant_id IS NULL OR tenant_id=?)"
    params = [tenant["id"]]
    if category:
        where += " AND category=?"
        params.append(category)
    if query:
        where += " AND (title LIKE ? ESCAPE '\\' OR content LIKE ? ESCAPE '\\')"
        params.extend([f"%{_escape_like(query)}%", f"%{_escape_like(query)}%"])

    rows = conn.execute(
        f"SELECT id, title, content, source, category, indexed_at FROM medical_knowledge WHERE {where} ORDER BY indexed_at DESC LIMIT ?",
        params + [limit]).fetchall()

    return jsonify({"results": [dict(r) for r in rows], "count": len(rows), "mode": "sql"})


@medical_bp.route("/api/medical/knowledge/<doc_id>", methods=["DELETE"])
def medical_knowledge_delete(doc_id):
    """Delete knowledge document
    ---
    tags: [Medical - Knowledge]
    responses:
      200:
        description: "요청 성공"
    """
    tenant, err = _require_tenant()
    if err:
        return err

    conn = _get_medical_db()
    cur = conn.execute("DELETE FROM medical_knowledge WHERE id=? AND (tenant_id IS NULL OR tenant_id=?)",
                       (doc_id, tenant["id"]))
    conn.commit()
    # SQL이 실제로 지웠을 때만 벡터/FTS 동반 삭제(타 테넌트 문서 보호 — 고아 벡터 방지)
    if cur.rowcount:
        try:
            from client.rag import delete_document, fts_delete
            delete_document(doc_id, collection_name=MEDICAL_KB_COLLECTION)
            fts_delete(doc_id, collection=MEDICAL_KB_COLLECTION)
        except Exception as e:
            logger.warning(f"RAG delete failed (SQLite는 삭제됨): {e}")
    return jsonify({"ok": True, "deleted": cur.rowcount})


@medical_bp.route("/api/medical/knowledge/stats")
def medical_knowledge_stats():
    """Knowledge base statistics
    ---
    tags: [Medical - Knowledge]
    responses:
      200:
        description: "요청 성공"
    """
    tenant, err = _require_tenant()
    if err:
        return err

    conn = _get_medical_db()
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM medical_knowledge WHERE tenant_id IS NULL OR tenant_id=?",
        (tenant["id"],)).fetchone()["cnt"]
    by_cat = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM medical_knowledge WHERE tenant_id IS NULL OR tenant_id=? GROUP BY category",
        (tenant["id"],)).fetchall()

    return jsonify({"total": total, "by_category": {r["category"]: r["cnt"] for r in by_cat}})


# ---------------------------------------------------------------------------
# Usage & Audit
# ---------------------------------------------------------------------------

@medical_bp.route("/api/medical/usage")
def medical_usage():
    """Usage statistics
    ---
    tags: [Medical - Stats]
    responses:
      200:
        description: "요청 성공"
    """
    tenant, err = _require_tenant()
    if err:
        return err

    conn = _get_medical_db()
    rows = conn.execute(
        "SELECT date, query_count, token_count FROM medical_usage WHERE tenant_id=? ORDER BY date DESC LIMIT 30",
        (tenant["id"],)).fetchall()
    return jsonify({"usage": [dict(r) for r in rows]})


@medical_bp.route("/api/medical/audit")
def medical_audit():
    """Audit log
    ---
    tags: [Medical - Stats]
    responses:
      200:
        description: "요청 성공"
    """
    tenant, err = _require_tenant()
    if err:
        return err

    limit = min(int(request.args.get("limit", 50)), 200)
    conn = _get_medical_db()
    rows = conn.execute(
        "SELECT action, endpoint, ip_address, request_summary, response_status, created_at FROM medical_audit_log WHERE tenant_id=? ORDER BY created_at DESC LIMIT ?",
        (tenant["id"], limit)).fetchall()
    return jsonify({"audit": [dict(r) for r in rows], "count": len(rows)})


# ---------------------------------------------------------------------------
# Medical Model Registry API (v2.47.0)
# ---------------------------------------------------------------------------

@medical_bp.route("/api/medical/models")
def api_medical_models():
    """List all registered medical models with domain routing info
    ---
    tags: [Medical - Models]
    parameters:
      - in: query
        name: domain
        type: string
        description: "Filter by domain (e.g., diagnosis, research, vision)"
      - in: query
        name: installed_only
        type: boolean
        default: false
    responses:
      200:
        description: 의료 모델 레지스트리
        schema:
          type: object
          properties:
            models: {}
            total: {type: integer}
            installed: {}
            routing: {}
            active: {type: object}
    """
    domain = request.args.get("domain")
    installed_only = request.args.get("installed_only", "false").lower() == "true"

    # Get installed models from Ollama
    installed_names = set()
    try:
        r = http_requests.get(f"{_OLLAMA_BASE}/api/tags", timeout=5)
        if r.ok:
            installed_names = {m["name"] for m in r.json().get("models", [])}
    except Exception:
        pass

    models = []
    for ckpt, meta in MEDICAL_MODELS.items():
        installed = ckpt in installed_names or any(ckpt.split(":")[0] in n for n in installed_names)
        if installed_only and not installed:
            continue
        if domain and domain not in meta.get("domains", []):
            continue
        models.append({
            **meta,
            "checkpoint": ckpt,
            "installed": installed,
        })

    # Domain routing info
    routing = {}
    if domain:
        routing[domain] = DOMAIN_MODEL_ROUTING.get(domain, [])
    else:
        routing = dict(DOMAIN_MODEL_ROUTING)

    return jsonify({
        "models": models,
        "total": len(models),
        "installed": sum(1 for m in models if m["installed"]),
        "routing": routing,
        "active": {
            "heavy": get_heavy_model(),
            "light": get_light_model(),
        },
    })


@medical_bp.route("/api/medical/models/routing")
def api_medical_model_routing():
    """Get domain-to-model routing table
    ---
    tags: [Medical - Models]
    responses:
      200:
        description: 도메인별 모델 라우팅 테이블
        schema:
          type: object
          properties:
            routing: {type: object}
            active: {type: object}
            defaults: {}
    """
    return jsonify({
        "routing": dict(DOMAIN_MODEL_ROUTING),
        "active": {"heavy": get_heavy_model(), "light": get_light_model()},
        "defaults": _get_model_defaults(),
    })


@medical_bp.route("/api/medical/models/recommend", methods=["POST"])
def api_medical_model_recommend():
    """Recommend best model for a given query/domain
    ---
    tags: [Medical - Models]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            domain: {type: string, description: "의료 도메인 (diagnosis, research, vision 등)"}
            query: {type: string, description: "질의 텍스트 (도메인 자동 판별)"}
            lang: {type: string, default: "ko", description: "ko 또는 en"}
    responses:
      200:
        description: 추천 모델
        schema:
          type: object
          properties:
            domain: {}
            recommended_model: {}
            model_info: {}
            alternatives: {}
            auto_detected: {type: boolean}
    """
    data = request.json or {}
    domain = data.get("domain", "")
    query = data.get("query", "")
    lang = data.get("lang", "ko")

    # 도메인 자동 판별 (query가 있고 domain이 없을 때)
    if query and not domain:
        q = query.lower()
        if any(k in q for k in ["영상", "x-ray", "ct ", "mri", "초음파", "radiolog", "patholog", "image"]):
            domain = "vision"
        elif any(k in q for k in ["논문", "연구", "pubmed", "research", "literature", "paper"]):
            domain = "research"
        elif any(k in q for k in ["약물", "drug", "medication", "pharmacol", "처방", "복용"]):
            domain = "pharmacology"
        elif any(k in q for k in ["감별", "differential", "진단", "diagnosis"]):
            domain = "differential_diagnosis"
        elif any(k in q for k in ["soap", "의무기록", "진료", "기록"]):
            domain = "soap"
        elif any(k in q for k in ["설명", "교육", "patient", "이해", "쉽게"]):
            domain = "patient_education"
        elif lang == "ko":
            domain = "general_ko"
        else:
            domain = "general_en"

    candidates = DOMAIN_MODEL_ROUTING.get(domain, [])
    recommended = candidates[0] if candidates else get_heavy_model()
    meta = MEDICAL_MODELS.get(recommended, {})

    return jsonify({
        "domain": domain,
        "recommended_model": recommended,
        "model_info": meta,
        "alternatives": candidates[1:] if len(candidates) > 1 else [],
        "auto_detected": bool(query and not data.get("domain")),
    })


# ---------------------------------------------------------------------------
# Model Compare & Benchmark API (v2.48.0)
# ---------------------------------------------------------------------------

BENCHMARK_CASES = [
    {"id": "chest_pain_55m", "text": "55세 남성, 3일 전부터 흉통, 좌측 팔 방사통, 운동 시 악화, 발한 동반. BP 150/95.",
     "expected_icd": ["I20.0", "I21.9", "I25.1"], "expected_urgency": "high"},
    {"id": "appendicitis_30f", "text": "30세 여성, 우하복부 통증 12시간, 반동 압통 양성, 발열 38.2도, 오심.",
     "expected_icd": ["K35.80", "N73.0"], "expected_urgency": "high"},
    {"id": "htn_emergency_45m", "text": "45세 남성, 심한 두통, 시야 흐림, BP 200/120, 구역감.",
     "expected_icd": ["I10", "I67.4"], "expected_urgency": "critical"},
    {"id": "heart_failure_70f", "text": "70세 여성, 호흡곤란 악화 1주, 기좌호흡, 양측 하지 부종, 야간 발작성 호흡곤란.",
     "expected_icd": ["I50.9", "I50.0"], "expected_urgency": "high"},
    {"id": "dm_new_25f", "text": "25세 여성, 피로감 2주, 체중감소 3kg, 다음 다뇨, 구갈.",
     "expected_icd": ["E11", "E10", "E05"], "expected_urgency": "medium"},
    {"id": "pneumonia_8m", "text": "8세 남아, 고열 39도 3일, 기침 심함, 빠른 호흡, 흉부 수포음.",
     "expected_icd": ["J18.9", "J15.9"], "expected_urgency": "high"},
    {"id": "hematuria_60m", "text": "60세 남성, 무통성 혈뇨 3회, 측복부 둔통, 체중감소.",
     "expected_icd": ["N20.0", "C67", "C64"], "expected_urgency": "medium"},
    {"id": "sle_35f", "text": "35세 여성, 대칭성 관절통 3개월, 나비 발진, 심한 피로, 구강 궤양.",
     "expected_icd": ["M32", "M32.9"], "expected_urgency": "medium"},
    {"id": "pancreas_50m", "text": "50세 남성, 상복부 통증 방사 배부, 황달, 체중감소 5kg/2개월, 식욕부진.",
     "expected_icd": ["K85", "C25", "K83.1"], "expected_urgency": "high"},
    {"id": "graves_40f", "text": "40세 여성, 두근거림 2개월, 손떨림, 체중감소 4kg, 안구 돌출, 열 불내성.",
     "expected_icd": ["E05.0", "E05"], "expected_urgency": "low"},
]


@medical_bp.route("/api/medical/model/compare", methods=["POST"])
def api_model_compare():
    """Compare multiple models on the same input
    ---
    tags: [Medical - Models]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [patient_text]
          properties:
            patient_text: {type: string}
            models: {type: array, items: {type: string}, description: "비교할 모델 목록 (최대 3개)"}
            task: {type: string, enum: [analyze, summarize, explain], default: analyze}
    responses:
      200:
        description: 모델별 비교 결과
        schema:
          type: object
          properties:
            results: {}
            task: {}
            models: {}
    """
    data = request.json or {}
    patient_text = data.get("patient_text", "").strip()
    if not patient_text:
        return jsonify({"error": "patient_text is required"}), 400

    models = data.get("models", ["werub-medical:7b", "thewindmom/llama3-med42-8b"])[:3]
    task = data.get("task", "analyze")

    defaults = _get_model_defaults()
    system = _get_system_prompt_for_action(task)

    results = {}
    for model in models:
        try:
            response, tokens, duration_ms = _medical_generate(
                model=model, prompt=patient_text, system=system,
                options={"temperature": 0.3}, action=f"compare_{task}",
            )
            results[model] = {
                "response": response, "tokens": tokens,
                "duration_ms": duration_ms, "status": "ok",
            }
        except Exception as e:
            results[model] = {"error": str(e), "status": "failed"}

    return jsonify({"results": results, "task": task, "models": models})


def _get_system_prompt_for_action(action):
    """액션별 시스템 프롬프트 반환"""
    prompts = {
        "analyze": ANALYZE_SYSTEM,
        "summarize": SUMMARIZE_SYSTEM.format(specialty="내과", format="SOAP"),
    }
    return prompts.get(action, ANALYZE_SYSTEM)


@medical_bp.route("/api/medical/model/benchmark", methods=["POST"])
def api_model_benchmark():
    """Run standard benchmark cases against specified models
    ---
    tags: [Medical - Models]
    security: [{Bearer: []}]
    parameters:
      - in: body
        name: body
        schema:
          type: object
          properties:
            models: {type: array, items: {type: string}, description: "벤치마크 대상 모델 (기본: 활성 모델)"}
            cases: {type: string, enum: [standard_10, first_3], default: first_3}
    responses:
      200:
        description: 벤치마크 결과
        schema:
          type: object
          properties:
            benchmark: {}
            case_count: {type: integer}
            models: {}
    """
    data = request.json or {}
    models = data.get("models", [get_heavy_model()])[:3]
    case_set = data.get("cases", "first_3")

    cases = BENCHMARK_CASES if case_set == "standard_10" else BENCHMARK_CASES[:3]

    results = {}
    for model in models:
        model_results = []
        total_tokens = 0
        total_ms = 0
        icd_hits = 0

        for case in cases:
            try:
                response, tokens, duration_ms = _medical_generate(
                    model=model, prompt=case["text"], system=ANALYZE_SYSTEM,
                    options={"temperature": 0.2}, action="benchmark",
                )
                total_tokens += tokens
                total_ms += duration_ms

                # ICD 코드 매칭 검사 (prefix 매칭: I21 matches I21.9)
                matched_icd = []
                for code in case["expected_icd"]:
                    prefix = code.split(".")[0]  # I21.9 → I21
                    if code in response or prefix in response:
                        matched_icd.append(code)
                if matched_icd:
                    icd_hits += 1

                model_results.append({
                    "case_id": case["id"],
                    "tokens": tokens, "duration_ms": duration_ms,
                    "icd_match": len(matched_icd) > 0,
                    "matched_codes": matched_icd,
                })
            except Exception as e:
                model_results.append({"case_id": case["id"], "error": str(e)})

        results[model] = {
            "cases": model_results,
            "summary": {
                "total_cases": len(cases),
                "icd_accuracy": round(icd_hits / max(len(cases), 1) * 100, 1),
                "avg_tokens": round(total_tokens / max(len(cases), 1), 1),
                "avg_duration_ms": round(total_ms / max(len(cases), 1), 1),
                "total_duration_ms": total_ms,
            },
        }

    return jsonify({"benchmark": results, "case_count": len(cases), "models": models})


@medical_bp.route("/api/medical/drug-interactions", methods=["POST"])
def medical_drug_interactions():
    """Check drug interactions from drug list
    ---
    tags: [Medical - Clinical]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [drugs]
          properties:
            drugs:
              type: array
              items: {type: string}
              description: "약물명 목록 (generic 또는 brand)"
              example: ["Warfarin", "Aspirin", "Amlodipine"]
    responses:
      200:
        description: 약물 상호작용 검사 결과
      400:
        description: drugs 필수
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    drugs = data.get("drugs", [])
    if not drugs or len(drugs) < 2:
        return jsonify({"error": "at least 2 drugs required"}), 400

    result = check_drug_interactions(drugs)
    result["disclaimer"] = "약물 상호작용 정보는 참고용이며, 최종 처방 판단은 의사가 수행합니다."

    _audit_log(tenant["id"], "drug-interactions", "/api/medical/drug-interactions", request)
    return jsonify(result)


# ---------------------------------------------------------------------------
# Batch Processing (v2.39.2)
# ---------------------------------------------------------------------------

BATCH_ACTIONS = {
    "summarize": lambda model, text, opts: _medical_generate(
        model, text, SUMMARIZE_SYSTEM.format(specialty=opts.get("specialty", "내과"), format="SOAP"), {"temperature": 0.2, "num_predict": 2000}),
    "analyze": lambda model, text, opts: _medical_generate(
        model, text, ANALYZE_SYSTEM.format(specialty=opts.get("specialty", "내과")), {"temperature": 0.2, "num_predict": 2000}),
    "explain": lambda model, text, opts: _medical_generate(
        model, text, EXPLAIN_SYSTEM.format(level=opts.get("level", "general")), {"temperature": 0.3, "num_predict": 1500}),
}


@medical_bp.route("/api/medical/batch", methods=["POST"])
@require_auth
def medical_batch():
    """Batch processing — 다건 동시 처리
    ---
    tags: [Medical - Clinical]
    security: [{Bearer: []}]
    parameters:
      - in: header
        name: X-Medical-Key
        type: string
        required: true
      - in: body
        name: body
        required: true
        schema:
          type: object
          required: [action, records]
          properties:
            action: {type: string, enum: [summarize, analyze, explain]}
            records: {type: array, items: {type: object, properties: {id: {type: string}, text: {type: string}}}}
            model: {type: string}
            options: {type: object}
    responses:
      200:
        description: 배치 결과
    """
    tenant, err = _require_tenant()
    if err:
        return err

    data = request.json or {}
    action = data.get("action", "")
    records = data.get("records", [])

    if action not in BATCH_ACTIONS:
        return jsonify({"error": f"unsupported action: {action}", "supported": list(BATCH_ACTIONS.keys())}), 400
    if not records:
        return jsonify({"error": "records required (array of {id, text})"}), 400
    if len(records) > 20:
        return jsonify({"error": "max 20 records per batch"}), 400

    model = _get_medical_model(action, data.get("model"))
    opts = data.get("options", {})
    action_fn = BATCH_ACTIONS[action]

    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = []
    total_tokens = 0
    total_duration = 0

    def _process(record):
        rid = record.get("id", "")
        text = record.get("text", "").strip()
        if not text:
            return {"id": rid, "error": "empty text", "status": "failed"}
        try:
            resp, toks, dur = action_fn(model, text, opts)
        except MedicalGenerationError as e:
            # 건별 실패는 건별로 — 종전엔 "Error: …"가 `status: completed`로 세졌다(A11)
            return {"id": rid, "error": str(e.reason)[:300], "status": "failed"}
        # v2.125.72: 파싱 실패도 건별 failed — 종전엔 `{"raw": 원문}`이 `status: completed`로 세졌다.
        from core.quality import parse_json_strict
        parsed = parse_json_strict(resp)
        if parsed is None:
            return {"id": rid, "error": "model output is not the promised JSON", "status": "failed",
                    "tokens": toks, "duration_ms": dur}
        return {"id": rid, "result": parsed, "tokens": toks, "duration_ms": dur, "status": "completed"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_process, r): r for r in records}
        for f in as_completed(futures):
            r = f.result()
            results.append(r)
            total_tokens += r.get("tokens", 0)
            total_duration += r.get("duration_ms", 0)

    # 입력 순서 복원
    id_order = {r.get("id", ""): i for i, r in enumerate(records)}
    results.sort(key=lambda x: id_order.get(x.get("id", ""), 999))

    completed = sum(1 for r in results if r["status"] == "completed")
    failed = sum(1 for r in results if r["status"] == "failed")

    _increment_usage(tenant["id"], total_tokens)
    _audit_log(tenant["id"], f"batch_{action}", "/api/medical/batch", request)

    return jsonify({
        "action": action,
        "model": model,
        "total": len(records),
        "completed": completed,
        "failed": failed,
        "total_tokens": total_tokens,
        "total_duration_ms": total_duration,
        "results": results,
        "disclaimer": get_disclaimer(data.get("lang", "ko")),
    })
