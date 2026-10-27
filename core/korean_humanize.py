"""한국어 AI 텍스트 자연화 메트릭 + 품질 분석

원본: epoko77-ai/im-not-ai (MIT License)
- metrics.py v1.6 (8함수): 쉼표·어휘·lexicon 기반 정량 지표
- metrics_v2.py v2.0 (14함수): post-editese 3축 (simplification·normalisation·interference)
통합 이식본. stdlib만 사용 (의존성 0).
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from statistics import StatisticsError, mean, pstdev
from typing import Any

VERSION = "v2.0"

# ---------------------------------------------------------------------------
# 내부 상수 — 정규식·사전
# ---------------------------------------------------------------------------

# 연결어미 뒤 쉼표 (C-11, KatFish 4.84배 최강 신호)
_ENDING_COMMA_RE = re.compile(r"(?:고|며|지만|면서|아서|어서)\s*,")

# 어절 분리
_EOJEOL_SPLIT_RE = re.compile(r"\s+")

# 문장 분리
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.!?。])\s+")

# 구두점 제거
_PUNCT_STRIP_RE = re.compile(r"[\.,!?;:\(\)\[\]\{\}\"'`~、。\u201c\u201d\u2018\u2019\-]+")

# 한자어 명사화 접미사
_HANJA_SUFFIXES = ("성", "적", "화")
_HANJA_SUFFIXES_V2 = ("성", "적", "화", "도", "력", "감", "원")

# 평서형 종결
_DECLARATIVE_ENDINGS = ("한다", "된다", "이다")

# 진행형 ~고 있다
_PROGRESSIVE_RE = re.compile(r"고\s*있(?:다|었|는|을|던|는다)")

# 이중 피동 표층 어휘
_DOUBLE_PASSIVE_TOKENS = (
    "되어진다", "되어졌다", "되어진", "되어지는",
    "여지다", "여진다", "여졌다", "여진",
    "잊혀진", "잊혀졌", "잊혀진다",
    "보여진다", "보여졌다", "보여진",
    "쓰여진다", "쓰여졌다", "쓰여진",
    "닫혀진", "열려진", "불려진", "놓여진",
)

# ~에 의해 + 피동
_BY_PASSIVE_RE = re.compile(
    r"에\s*의(?:해|하여)\s+\S{0,12}?(?:되|받|당하|지)(?:다|었|어|ㄴ다|는다|는|ㄹ|을)"
)

# 인칭 대명사
_PRONOUN_RE = re.compile(
    r"(?:그녀(?:는|가|를|의|에게|와|도|만)?"
    r"|그것(?:은|이|을|의|에|에게)?"
    r"|그들(?:은|이|을|의|에게|과|도)?"
    r"|그(?:는|가|를|의|에게|와|도|만)(?=\s|[\.,!?]|$))"
)

# 무정물 + '-들'
_INANIMATE_DEUL_TOKENS = (
    "데이터들", "정보들", "결과들", "연구들", "아이디어들",
    "방법들", "문제들", "의견들", "시스템들", "기술들",
    "사실들", "사례들", "이론들", "개념들", "현상들",
    "특징들", "요소들", "원인들", "영향들", "변화들",
    "기능들", "조건들", "기준들", "관점들", "원리들",
)

# light verb 직역
_HAVE_MAKE_LITERAL_TOKENS = (
    "가지고 있다", "가지고있다", "가지고 있는", "가지고있는",
    "가지고 있었", "가지고있었", "갖고 있다", "갖고있다",
    "갖고 있는", "갖고있는", "을 가지다", "를 가지다",
    "을 가졌", "를 가졌", "회의를 가지", "회의를 가졌",
    "결정을 내리", "결정을 내렸",
)

# 이중 조사 결합
_DOUBLE_PARTICLE_RE = re.compile(r"(?:에서의|에로의|으로의|에의|으로부터의|로부터의)")

# 단락 분리
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")

# 종결어미 추출
_ENDING_FINAL_RE = re.compile(r"([가-힣]{2})[\.!?]\s*$")
_ENDING_FINAL_FALLBACK_RE = re.compile(r"([가-힣])[\.!?]\s*$")

# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# KSS 문장 분리기 (설치 시 자동 활성화, 미설치 시 regex fallback)
_kss_splitter = None
try:
    from kss import Kss
    _kss_splitter = Kss("split_sentences")
except ImportError:
    pass


def _split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    # KSS 우선 사용 (경어/방언/신조어 대응)
    # backend="fast"(규칙 기반): numpy 비의존. pecab 백엔드는 numpy 2.x(NEP 50)에서
    # 정수 오버플로로 문장 경계를 오절단("넷째다. 다섯"|"째는…")하므로 사용 금지.
    # numpy 2.x는 torch/transformers 의존이라 다운그레이드 불가 → 백엔드로 회피. [[device]]
    if _kss_splitter is not None:
        try:
            result = _kss_splitter(text, backend="fast")
            return [s.strip() for s in result if s.strip()]
        except Exception:
            pass
    # regex fallback
    parts = _SENTENCE_SPLIT_RE.split(text)
    out: list[str] = []
    for p in parts:
        for line in p.split("\n"):
            line = line.strip()
            if line:
                out.append(line)
    return out


def _eojeols(text: str) -> list[str]:
    return [tok for tok in _EOJEOL_SPLIT_RE.split(text.strip()) if tok]


def _strip_punct(token: str) -> str:
    return _PUNCT_STRIP_RE.sub("", token)


def _all_tokens(text: str) -> list[str]:
    toks = [_strip_punct(t) for t in _eojeols(text)]
    return [t for t in toks if t]


def _split_paragraphs(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]


def _last_eojeol(sentence: str) -> str:
    toks = _eojeols(sentence)
    if not toks:
        return ""
    return _strip_punct(toks[-1])


# ---------------------------------------------------------------------------
# v1.6 메트릭 (8함수)
# ---------------------------------------------------------------------------


def comma_inclusion_rate(text: str) -> float:
    """쉼표를 포함한 문장 비율 (0~1)."""
    sents = _split_sentences(text)
    if not sents:
        return 0.0
    return sum(1 for s in sents if "," in s) / len(sents)


def comma_usage_rate(text: str) -> float:
    """문장당 평균 쉼표 수."""
    sents = _split_sentences(text)
    if not sents:
        return 0.0
    return sum(s.count(",") for s in sents) / len(sents)


def ending_comma_rate(text: str) -> float:
    """연결어미(-고/-며/-지만 등) 뒤 쉼표 비율. KatFish 4.84배 최강 신호."""
    if not text.strip():
        return 0.0
    boundary_endings = re.findall(
        r"(?:고|며|지만|면서|아서|어서)(?=[\s,\.!?、。]|$)", text
    )
    if not boundary_endings:
        return 0.0
    with_comma = len(_ENDING_COMMA_RE.findall(text))
    return with_comma / len(boundary_endings)


def comma_segment_length(text: str) -> float:
    """쉼표 구간 평균 어절수."""
    sents = _split_sentences(text)
    seg_lens: list[int] = []
    for s in sents:
        if "," not in s:
            seg_lens.append(len(_eojeols(s)))
            continue
        for seg in s.split(","):
            seg = seg.strip()
            if seg:
                seg_lens.append(len(_eojeols(seg)))
    if not seg_lens:
        return 0.0
    return sum(seg_lens) / len(seg_lens)


def conclusion_pivot_count(text: str, lexicon: list[str] | None = None) -> int:
    """결산 피벗 어휘 출현수 (결론적으로/따라서/이를 통해/그러므로)."""
    items = lexicon or ["결론적으로", "따라서", "이를 통해", "그러므로"]
    return sum(text.count(w) for w in items)


def safe_balance_count(text: str, lexicon: list[str] | None = None) -> int:
    """안전 균형 어휘 출현수 (양쪽 모두/장점도 있지만/신중하게/균형)."""
    items = lexicon or ["양쪽 모두", "두 가지 모두", "장점도 있지만", "신중하게", "균형"]
    return sum(text.count(w) for w in items)


def hanja_nominalizer_density(text: str) -> float:
    """한자어 명사화 접미사 (-성/-적/-화) 토큰 밀도 (0~1)."""
    tokens = [_strip_punct(t) for t in _eojeols(text)]
    tokens = [t for t in tokens if t]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if len(t) >= 2 and t[-1] in _HANJA_SUFFIXES)
    return hits / len(tokens)


def lexical_diversity(text: str) -> float:
    """TTR (고유 토큰 / 전체 토큰)."""
    toks = [_strip_punct(t) for t in _eojeols(text)]
    toks = [t for t in toks if t]
    if not toks:
        return 0.0
    return len(set(toks)) / len(toks)


# ---------------------------------------------------------------------------
# v2.0 Simplification 축 (3함수)
# ---------------------------------------------------------------------------


def lexical_diversity_ttr(text: str) -> float:
    """TTR — simplification 축 노출."""
    return lexical_diversity(text)


def lexical_density(text: str) -> float:
    """내용어 비율 (접미사·종결 기반 근사)."""
    tokens = _all_tokens(text)
    if not tokens:
        return 0.0
    stop = {
        "그리고", "그러나", "하지만", "또한", "또는", "혹은", "즉",
        "예를", "예컨대", "이는", "이것은", "그것은", "그러므로", "따라서",
    }
    content_endings = (
        "한다", "된다", "이다", "했다", "였다", "었다",
        "답다", "스럽다", "롭다", "하다", "되다",
    )
    hits = 0
    for t in tokens:
        if len(t) < 2 or t in stop:
            continue
        if t[-1] in _HANJA_SUFFIXES_V2:
            hits += 1
        elif any(t.endswith(end) for end in content_endings):
            hits += 1
    return hits / len(tokens)


def ending_diversity(text: str) -> float:
    """종결어미 다양성 (고유/전체)."""
    sents = _split_sentences(text)
    keys: list[str] = []
    for s in sents:
        m = _ENDING_FINAL_RE.search(s)
        if m:
            keys.append(m.group(1))
            continue
        m2 = _ENDING_FINAL_FALLBACK_RE.search(s)
        if m2:
            keys.append(m2.group(1))
    if not keys:
        return 0.0
    return len(set(keys)) / len(keys)


# ---------------------------------------------------------------------------
# v2.0 Normalisation 축 (2함수)
# ---------------------------------------------------------------------------


def normalisation_score(text: str) -> float:
    """평서형 (~한다/~된다/~이다) 집중도 (0~1)."""
    sents = _split_sentences(text)
    if not sents:
        return 0.0
    hits = 0
    for s in sents:
        last = _last_eojeol(s)
        if last and any(last.endswith(e) for e in _DECLARATIVE_ENDINGS):
            hits += 1
    return hits / len(sents)


def da_streak_rate(text: str) -> int:
    """'-다' 4연속 streak 횟수."""
    sents = _split_sentences(text)
    streaks = 0
    cur = 0
    for s in sents:
        last = _last_eojeol(s)
        if last.endswith("다"):
            cur += 1
        else:
            if cur >= 4:
                streaks += 1
            cur = 0
    if cur >= 4:
        streaks += 1
    return streaks


# ---------------------------------------------------------------------------
# v2.0 Interference 축 T1~T8 (9함수)
# ---------------------------------------------------------------------------


def inanimate_subject_rate(text: str) -> float:
    """T1: 무생물 주어 + 만능 동사 비율."""
    sents = _split_sentences(text)
    if not sents:
        return 0.0
    inanimate_subjects = (
        "연구", "데이터", "분석", "결과", "시스템", "기술", "사례",
        "현상", "이론", "정책", "보고서", "AI", "인공지능", "모델",
        "알고리즘", "변화", "위기", "혁신", "사회", "경제",
    )
    universal_verbs = (
        "보여준다", "보여줬다", "보여주는", "시사한다", "시사하는",
        "만든다", "만들어", "드러낸다", "드러냈다", "드러내는",
        "제시한다", "제시했다", "나타낸다", "나타냈다", "나타내는",
        "증명한다", "증명했다", "말해준다", "말해주는",
        "의미한다", "의미하는", "가져온다", "가져왔다", "가져오는",
    )
    hits = 0
    for s in sents:
        toks = _all_tokens(s)
        if not toks:
            continue
        head = toks[0]
        head_stem = head
        for josa in ("은", "는", "이", "가", "도"):
            if head.endswith(josa) and len(head) > 1:
                head_stem = head[:-1]
                break
        is_inanimate = (
            head_stem in inanimate_subjects
            or (len(head_stem) >= 2 and head_stem[-1] in _HANJA_SUFFIXES_V2)
        )
        if not is_inanimate:
            continue
        if any(any(uv in t for uv in universal_verbs) for t in toks[1:]):
            hits += 1
    return hits / len(sents)


def by_passive_count(text: str) -> int:
    """T2a: ~에 의해 + 피동 동사 동시 출현 수."""
    if not text.strip():
        return 0
    return len(_BY_PASSIVE_RE.findall(text))


def double_passive_count(text: str) -> int:
    """T2b: 이중 피동 (되어진다/보여진다 등) 수."""
    if not text.strip():
        return 0
    return sum(text.count(tok) for tok in _DOUBLE_PASSIVE_TOKENS)


def pronoun_density(text: str) -> float:
    """T3: 영어 대명사 직역 밀도 (단락 평균)."""
    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return 0.0
    densities: list[float] = []
    for p in paragraphs:
        toks = _all_tokens(p)
        if not toks:
            continue
        pronoun_hits = len(_PRONOUN_RE.findall(p))
        densities.append(pronoun_hits / len(toks))
    if not densities:
        return 0.0
    try:
        return mean(densities)
    except StatisticsError:
        return 0.0


def deul_overuse_rate(text: str) -> float:
    """T4: 무정물 '-들' 과용 비율."""
    toks = _all_tokens(text)
    if not toks:
        return 0.0
    hits = 0
    for t in toks:
        if t in _INANIMATE_DEUL_TOKENS:
            hits += 1
            continue
        for base in _INANIMATE_DEUL_TOKENS:
            if t.startswith(base) and len(t) - len(base) in (1, 2):
                tail = t[len(base):]
                if all("가" <= ch <= "힣" for ch in tail):
                    hits += 1
                    break
    return hits / len(toks)


def relative_clause_nesting(text: str) -> int:
    """T5: 관형절 깊이 ≥3인 문장 수."""
    sents = _split_sentences(text)
    if not sents:
        return 0
    adnominal_re = re.compile(r"[가-힣]+(?:ㄴ|는|ㄹ|던|한|된|할|될|온|간)\s+[가-힣]")
    return sum(1 for s in sents if len(adnominal_re.findall(s)) >= 3)


def have_make_literal_count(text: str) -> int:
    """T6: light verb 직역 (가지고 있다/회의를 가지다 등) 수."""
    if not text.strip():
        return 0
    return sum(text.count(tok) for tok in _HAVE_MAKE_LITERAL_TOKENS)


def double_particle_count(text: str) -> int:
    """T7: 이중 조사 결합 (에서의/에로의/으로의 등) 수."""
    if not text.strip():
        return 0
    return len(_DOUBLE_PARTICLE_RE.findall(text))


def progressive_aspect_rate(text: str) -> float:
    """T8b: '~고 있다' 문장당 비율."""
    sents = _split_sentences(text)
    if not sents:
        return 0.0
    hits = sum(len(_PROGRESSIVE_RE.findall(s)) for s in sents)
    return hits / len(sents)


# ---------------------------------------------------------------------------
# 통합 지표
# ---------------------------------------------------------------------------


def interference_index(text: str) -> dict[str, Any]:
    """T1~T8 가중합 복합 지표."""
    chars = max(len(text), 1)
    components = {
        "T1_inanimate_subject_rate": inanimate_subject_rate(text),
        "T2a_by_passive_per_1k": by_passive_count(text) / chars * 1000,
        "T2b_double_passive_per_1k": double_passive_count(text) / chars * 1000,
        "T3_pronoun_density": pronoun_density(text),
        "T4_deul_overuse_rate": deul_overuse_rate(text),
        "T5_nested_clause_count": relative_clause_nesting(text),
        "T6_have_make_per_1k": have_make_literal_count(text) / chars * 1000,
        "T7_double_particle_per_1k": double_particle_count(text) / chars * 1000,
        "T8b_progressive_rate": progressive_aspect_rate(text),
    }
    weights = {
        "T1_inanimate_subject_rate": 1.0,
        "T2a_by_passive_per_1k": 0.2,
        "T2b_double_passive_per_1k": 0.2,
        "T3_pronoun_density": 4.0,
        "T4_deul_overuse_rate": 4.0,
        "T5_nested_clause_count": 0.05,
        "T6_have_make_per_1k": 0.2,
        "T7_double_particle_per_1k": 0.5,
        "T8b_progressive_rate": 1.0,
    }
    weighted_total = sum(
        min(1.0, max(0.0, v * weights[k])) for k, v in components.items()
    )
    return {
        "components": components,
        "weighted_total": weighted_total,
        "n_chars": chars,
    }


# ---------------------------------------------------------------------------
# Baseline + z-score
# ---------------------------------------------------------------------------


def _load_baseline(path: str | None = None) -> dict[str, Any]:
    p = path or os.path.join(_DATA_DIR, "baseline.json")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_genre_cells(
    baseline: dict[str, Any], genre: str
) -> tuple[dict[str, Any], str | None]:
    genres = baseline.get("genres", {}) or {}
    requested = genres.get(genre)
    fallback = None
    if requested is None:
        fallback = f"baseline_genre_null:{genre}->essay"
        requested = genres.get("essay") or {}
    g = baseline.get("global_average", {}) or {}
    merged: dict[str, Any] = {}
    for k in set(list((requested or {}).keys()) + list(g.keys())):
        cell = (requested or {}).get(k) or g.get(k)
        if cell:
            merged[k] = cell
    return merged, fallback


def _z(value: float, human: float, ai: float, *, percent: bool) -> float | None:
    if human is None or ai is None:
        return None
    val = value * 100 if percent else value
    sd = abs(ai - human) / 2.0
    if sd == 0:
        return 0.0
    return (val - human) / sd


def _classify_risk(
    z_scores: dict[str, float | None], lexicon_hits: dict[str, int]
) -> tuple[str, int]:
    score = 0
    for key in ("comma_inclusion_rate", "ending_comma_rate", "comma_segment_length"):
        z = z_scores.get(key)
        if z is not None and z > 1.0:
            score += 2
    ld = z_scores.get("lexical_diversity")
    if ld is not None and ld < -1.0:
        score += 1
    if lexicon_hits.get("conclusion_pivot_count", 0) >= 2:
        score += 1
    if lexicon_hits.get("safe_balance_count", 0) >= 2:
        score += 1
    hz = z_scores.get("hanja_nominalizer_density")
    if hz is not None and hz > 1.0:
        score += 1
    if score >= 6:
        band = "high"
    elif score >= 4:
        band = "medium"
    else:
        band = "low"
    return band, score


def _evidence_spans(text: str, lexicon: list[str]) -> list[str]:
    return [w for w in lexicon if w in text]


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def compute_korean_quality(
    text: str,
    genre: str = "essay",
    baseline_path: str | None = None,
) -> dict[str, Any]:
    """한국어 AI 텍스트 품질 전체 분석.

    Returns:
        dict: risk_band, risk_score, metrics, z_scores, v2_metrics,
              v2_interference_index, evidence
    """
    baseline = _load_baseline(baseline_path)
    cells, fallback_warning = _resolve_genre_cells(baseline, genre)
    lex = baseline.get("lexicons", {}) or {}
    pivot_lex = lex.get("conclusion_pivot") or [
        "결론적으로", "따라서", "이를 통해", "그러므로",
    ]
    safe_lex = lex.get("safe_balance") or [
        "양쪽 모두", "두 가지 모두", "장점도 있지만", "신중하게", "균형",
    ]

    # v1.6 metrics
    metrics: dict[str, float | int] = {
        "comma_inclusion_rate": comma_inclusion_rate(text),
        "comma_usage_rate": comma_usage_rate(text),
        "ending_comma_rate": ending_comma_rate(text),
        "comma_segment_length": comma_segment_length(text),
        "conclusion_pivot_count": conclusion_pivot_count(text, pivot_lex),
        "safe_balance_count": safe_balance_count(text, safe_lex),
        "hanja_nominalizer_density": hanja_nominalizer_density(text),
        "lexical_diversity": lexical_diversity(text),
    }

    # z-scores
    z_scores: dict[str, float | None] = {}
    for key, percent in (
        ("comma_inclusion_rate", True),
        ("comma_usage_rate", False),
        ("ending_comma_rate", True),
        ("comma_segment_length", False),
    ):
        cell = cells.get(key)
        if cell:
            z_scores[key] = _z(
                metrics[key], cell.get("human"), cell.get("ai"), percent=percent
            )
        else:
            z_scores[key] = None

    z_scores["hanja_nominalizer_density"] = _z(
        metrics["hanja_nominalizer_density"] * 100, 6.0, 12.0, percent=False
    )
    z_scores["lexical_diversity"] = _z(
        metrics["lexical_diversity"], 0.65, 0.55, percent=False
    )

    lexicon_hits = {
        "conclusion_pivot_count": int(metrics["conclusion_pivot_count"]),
        "safe_balance_count": int(metrics["safe_balance_count"]),
    }
    risk_band, risk_score = _classify_risk(z_scores, lexicon_hits)

    # v2.0 metrics
    v2_metrics: dict[str, float | int] = {
        "lexical_diversity_ttr": lexical_diversity_ttr(text),
        "lexical_density": lexical_density(text),
        "ending_diversity": ending_diversity(text),
        "normalisation_score": normalisation_score(text),
        "da_streak_rate": da_streak_rate(text),
        "inanimate_subject_rate": inanimate_subject_rate(text),
        "by_passive_count": by_passive_count(text),
        "double_passive_count": double_passive_count(text),
        "pronoun_density": pronoun_density(text),
        "deul_overuse_rate": deul_overuse_rate(text),
        "relative_clause_nesting": relative_clause_nesting(text),
        "have_make_literal_count": have_make_literal_count(text),
        "double_particle_count": double_particle_count(text),
        "progressive_aspect_rate": progressive_aspect_rate(text),
    }

    out: dict[str, Any] = {
        "version": VERSION,
        "genre": genre,
        "char_count": len(text),
        "metrics": metrics,
        "z_scores": z_scores,
        "risk_band": risk_band,
        "risk_score": risk_score,
        "v2_metrics": v2_metrics,
        "v2_interference_index": interference_index(text),
        "evidence": {
            "conclusion_pivots": _evidence_spans(text, pivot_lex),
            "safe_balances": _evidence_spans(text, safe_lex),
        },
    }
    if fallback_warning:
        out["warning"] = fallback_warning
    return out


def get_s1_prompt_rules() -> str:
    """S1 패턴 제거 규칙을 system prompt 삽입용 텍스트로 반환."""
    return """[한국어 자연스러움 규칙 — 아래 패턴은 반드시 피하세요]

1. 번역투 금지:
   - "~에 대해" 대신 "~를" 직접 연결
   - "~를 통해" 대신 "~로", "~해서"
   - "~에 있어서" 대신 "~에서"
   - "가지고 있다" 대신 형용사/동사로 환원
   - 이중 피동 "~되어진다" 대신 "~된다"
   - "그/그녀/그들" 남발 금지 — 생략하거나 명사구 사용

2. AI 관용구 삭제:
   - "결론적으로", "요약하면", "정리하자면" 사용 금지
   - "시사하는 바가 크다", "주목할 만하다" 삭제
   - "본질적으로", "핵심적으로" 삭제
   - "혁신적인", "획기적인", "파격적" 대신 구체적 서술
   - "~할 때다", "지금이야말로" 대신 평서형 종결

3. 구조 규칙:
   - 이모지 사용 금지
   - "X: Y" 콜론 부제 반복 금지
   - 연결어미(-고/-며/-지만) 바로 뒤에 쉼표 금지
   - 동일 종결어미 4문장 연속 금지

4. 접속사·형식명사:
   - 문두 접속사(또한/따라서/즉/나아가) 3회 이상 연속 금지
   - "이는", "이 점에서" 남발 금지
   - "~인 것이다" 결말 대신 평서형 종결
   - 따옴표 강조 5회 이상 금지"""


def get_quality_summary(result: dict[str, Any]) -> dict[str, Any]:
    """API 응답에 첨부할 경량 품질 요약."""
    return {
        "risk_band": result.get("risk_band", "unknown"),
        "risk_score": result.get("risk_score", 0),
        "version": result.get("version", VERSION),
        "key_signals": {
            "ending_comma_rate": result.get("metrics", {}).get("ending_comma_rate", 0),
            "ending_comma_rate_z": result.get("z_scores", {}).get("ending_comma_rate"),
            "conclusion_pivot_count": result.get("metrics", {}).get("conclusion_pivot_count", 0),
            "lexical_diversity": result.get("metrics", {}).get("lexical_diversity", 0),
            "normalisation_score": result.get("v2_metrics", {}).get("normalisation_score", 0),
            "interference_total": result.get("v2_interference_index", {}).get("weighted_total", 0),
        },
    }


# ---------------------------------------------------------------------------
# 한국어 가독성 점수 (Korean Readability Index)
# ---------------------------------------------------------------------------


def compute_readability(text: str) -> dict[str, Any]:
    """한국어 텍스트 가독성 분석.

    영어 Flesch-Kincaid를 한국어에 맞게 재설계:
    - 평균 문장 길이 (어절 수)
    - 평균 어절 길이 (음절 수)
    - 어휘 난이도 (한자어 비율)
    - 종결어미 다양성
    - 문장 길이 편차

    Returns:
        dict: {
            "score": float (0~100, 높을수록 쉬움),
            "grade": str (초등/중등/고등/전문/학술),
            "metrics": dict,
            "recommendations": list[str]
        }
    """
    if not text or not text.strip():
        return {"score": 0, "grade": "unknown", "metrics": {}, "recommendations": []}

    sents = _split_sentences(text)
    if not sents:
        return {"score": 0, "grade": "unknown", "metrics": {}, "recommendations": []}

    # 1. 문장별 어절 수
    sent_lengths = [len(_eojeols(s)) for s in sents]
    avg_sent_len = sum(sent_lengths) / len(sent_lengths) if sent_lengths else 0

    # 2. 문장 길이 표준편차
    if len(sent_lengths) >= 2:
        mean_len = avg_sent_len
        variance = sum((x - mean_len) ** 2 for x in sent_lengths) / len(sent_lengths)
        sent_len_std = variance ** 0.5
    else:
        sent_len_std = 0

    # 3. 평균 어절 길이 (음절 수)
    all_tokens = _all_tokens(text)
    if all_tokens:
        # 한글 음절 수 계산
        syllable_counts = []
        for tok in all_tokens:
            hangul = sum(1 for c in tok if "가" <= c <= "힣")
            syllable_counts.append(max(hangul, 1))
        avg_word_syllables = sum(syllable_counts) / len(syllable_counts)
    else:
        avg_word_syllables = 0

    # 4. 한자어 비율 (-성/-적/-화/-도/-력 등)
    hanja_density = hanja_nominalizer_density(text)

    # 5. 종결어미 다양성
    end_div = ending_diversity(text)

    # 6. 긴 문장 비율 (15어절 이상)
    long_sent_ratio = sum(1 for l in sent_lengths if l >= 15) / len(sent_lengths) if sent_lengths else 0

    # 점수 계산 (100점 기준, 높을수록 쉬움)
    score = 100.0
    score -= max(0, (avg_sent_len - 8)) * 3          # 문장 길어질수록 감점
    score -= max(0, (avg_word_syllables - 2.5)) * 10  # 어절 길수록 감점
    score -= hanja_density * 200                       # 한자어 많을수록 감점
    score -= long_sent_ratio * 20                      # 긴 문장 많을수록 감점
    score += min(end_div * 10, 10)                     # 종결 다양하면 가산
    score += min(sent_len_std * 0.5, 5)                # 길이 변화 있으면 가산
    score = max(0, min(100, round(score, 1)))

    # 등급 판정
    if score >= 80:
        grade = "초등"     # 초등학생도 읽기 쉬움
    elif score >= 65:
        grade = "중등"     # 중학생 수준
    elif score >= 50:
        grade = "고등"     # 고등학생~성인
    elif score >= 35:
        grade = "전문"     # 전문가 수준
    else:
        grade = "학술"     # 학술·논문 수준

    # 개선 제안
    recommendations = []
    if avg_sent_len > 15:
        recommendations.append(f"문장이 너무 깁니다 (평균 {avg_sent_len:.0f}어절). 10어절 내외로 줄이세요.")
    if hanja_density > 0.08:
        recommendations.append(f"한자어 비율이 높습니다 ({hanja_density:.1%}). 쉬운 표현으로 바꿔보세요.")
    if long_sent_ratio > 0.3:
        recommendations.append(f"15어절 이상 긴 문장이 {long_sent_ratio:.0%}입니다. 분리를 권장합니다.")
    if end_div < 0.3:
        recommendations.append("종결어미가 단조롭습니다. 다양한 어미를 사용하세요.")
    if sent_len_std < 3:
        recommendations.append("문장 길이가 균일합니다. 짧은 문장과 긴 문장을 섞으세요.")

    return {
        "score": score,
        "grade": grade,
        "metrics": {
            "avg_sentence_length": round(avg_sent_len, 1),
            "sentence_length_std": round(sent_len_std, 1),
            "avg_word_syllables": round(avg_word_syllables, 2),
            "hanja_density": round(hanja_density, 4),
            "ending_diversity": round(end_div, 3),
            "long_sentence_ratio": round(long_sent_ratio, 3),
            "total_sentences": len(sents),
            "total_words": len(all_tokens),
        },
        "recommendations": recommendations,
    }


# ---------------------------------------------------------------------------
# 텍스트 응집성 분석 (Cohesion Analysis)
# ---------------------------------------------------------------------------


def compute_cohesion(text: str) -> dict[str, Any]:
    """텍스트 응집성 분석 — 문장 간 연결성 측정.

    TAACO 방법론 참조:
    - ASO (Adjacent Sentence Overlap): 인접 문장 간 어휘 겹침
    - 접속사 밀도: 문장 연결 장치 사용 빈도
    - 지시어 체인: 지시대명사(이/그/저)의 연결 패턴
    - 주제어 반복: 핵심 어휘의 문단 간 반복

    Returns:
        dict: {
            "score": float (0~100),
            "grade": str,
            "metrics": dict,
            "weak_points": list[str]
        }
    """
    if not text or not text.strip():
        return {"score": 0, "grade": "unknown", "metrics": {}, "weak_points": []}

    sents = _split_sentences(text)
    if len(sents) < 2:
        return {"score": 100, "grade": "good", "metrics": {"sentence_count": len(sents)}, "weak_points": []}

    # 1. ASO: 인접 문장 어휘 겹침률
    overlaps = []
    for i in range(len(sents) - 1):
        words_a = set(_all_tokens(sents[i]))
        words_b = set(_all_tokens(sents[i + 1]))
        if words_a and words_b:
            overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
            overlaps.append(overlap)
    avg_aso = sum(overlaps) / len(overlaps) if overlaps else 0

    # 2. 접속사 밀도
    connectives = [
        "그리고", "그러나", "하지만", "그래서", "따라서", "그러므로",
        "또한", "게다가", "더욱이", "반면", "한편", "즉", "다시 말해",
        "그런데", "그래도", "오히려", "결국", "마침내", "드디어",
    ]
    connective_count = 0
    for s in sents:
        tokens = _all_tokens(s)
        if tokens and tokens[0] in connectives:
            connective_count += 1
    connective_density = connective_count / len(sents)

    # 3. 지시어 체인
    demonstratives = ["이것", "그것", "저것", "이런", "그런", "저런",
                       "여기", "거기", "저기", "이때", "그때"]
    demo_count = sum(text.count(d) for d in demonstratives)
    demo_density = demo_count / len(sents)

    # 4. 주제어 반복 (상위 5어휘의 문단 간 분포)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    topic_consistency = 0.0
    if len(paragraphs) >= 2:
        all_words = Counter(_all_tokens(text))
        top_words = [w for w, _ in all_words.most_common(5)]
        para_hits = []
        for p in paragraphs:
            p_tokens = set(_all_tokens(p))
            hits = sum(1 for w in top_words if w in p_tokens)
            para_hits.append(hits / max(len(top_words), 1))
        topic_consistency = sum(para_hits) / len(para_hits)

    # 점수 계산
    score = 50.0
    score += avg_aso * 30            # 어휘 겹침 높으면 가산
    score += connective_density * 15  # 접속사 적절하면 가산
    score += demo_density * 5         # 지시어 있으면 가산
    score += topic_consistency * 10   # 주제어 일관성 가산
    # 과도한 접속사는 감점
    if connective_density > 0.5:
        score -= (connective_density - 0.5) * 20
    score = max(0, min(100, round(score, 1)))

    grade = "excellent" if score >= 80 else "good" if score >= 60 else "fair" if score >= 40 else "poor"

    weak_points = []
    if avg_aso < 0.1:
        weak_points.append("인접 문장 간 어휘 연결이 약합니다. 핵심어를 반복하거나 대명사로 연결하세요.")
    if connective_density < 0.1 and len(sents) > 5:
        weak_points.append("접속사가 부족합니다. 문장 간 논리 관계를 명시하세요.")
    elif connective_density > 0.5:
        weak_points.append("접속사가 과다합니다. 문장 자체가 흐름을 잡도록 줄이세요.")
    if topic_consistency < 0.4 and len(paragraphs) >= 2:
        weak_points.append("문단 간 주제 연결이 약합니다. 핵심 키워드를 일관되게 사용하세요.")

    return {
        "score": score,
        "grade": grade,
        "metrics": {
            "adjacent_sentence_overlap": round(avg_aso, 3),
            "connective_density": round(connective_density, 3),
            "demonstrative_density": round(demo_density, 3),
            "topic_consistency": round(topic_consistency, 3),
            "sentence_count": len(sents),
            "paragraph_count": len(paragraphs),
        },
        "weak_points": weak_points,
    }


# ---------------------------------------------------------------------------
# 경어법 수준 판별 (Honorific Level Detection)
# ---------------------------------------------------------------------------

_HONORIFIC_PATTERNS = {
    "하십시오체": re.compile(r"(?:습니다|습니까|십시오|하십시오|주십시오|됩니다|겠습니다)[\.!?\s]"),
    "해요체": re.compile(r"(?:해요|에요|이에요|세요|네요|군요|지요|죠|나요|래요|대요)[\.!?\s]"),
    "해체": re.compile(r"(?:해|야|어|지|거든|잖아|니까|는데|다고)[\.!?\s]"),
    "하라체": re.compile(r"(?:하라|하거라|해라|가거라|먹어라|이다)[\.!?\s]"),
    "해라체": re.compile(r"(?:한다|이다|였다|었다|겠다|리라|더라|느니라)[\.!?\s]"),
}


def detect_honorific_level(text: str) -> dict[str, Any]:
    """경어법 수준 자동 판별 — 문장별 경어체 분류 + 혼용 탐지.

    Returns:
        dict: {
            "dominant": str,  # 주된 경어체
            "distribution": {체: 비율},
            "consistency": float (0~1),
            "mixed_sentences": int,  # 혼용 문장 수
            "detail": [{sentence, level}]  # 처음 10문장
        }
    """
    if not text or not text.strip():
        return {"dominant": "unknown", "distribution": {}, "consistency": 0}

    sents = _split_sentences(text)
    level_counts: dict[str, int] = {}
    details = []

    for s in sents:
        detected = None
        # 우선순위: 하십시오체 > 해요체 > 해라체 > 하라체 > 해체
        for level_name in ["하십시오체", "해요체", "해라체", "하라체", "해체"]:
            if _HONORIFIC_PATTERNS[level_name].search(s + " "):
                detected = level_name
                break
        if detected:
            level_counts[detected] = level_counts.get(detected, 0) + 1
        if len(details) < 10:
            details.append({"sentence": s[:50], "level": detected or "불명"})

    total = sum(level_counts.values())
    if total == 0:
        return {"dominant": "unknown", "distribution": {}, "consistency": 0, "detail": details}

    dominant = max(level_counts, key=level_counts.get)
    distribution = {k: round(v / total, 3) for k, v in sorted(level_counts.items(), key=lambda x: -x[1])}
    consistency = round(level_counts[dominant] / total, 3)

    return {
        "dominant": dominant,
        "distribution": distribution,
        "consistency": consistency,
        "mixed_levels": len(level_counts),
        "warning": "경어체 혼용 감지" if len(level_counts) > 1 and consistency < 0.8 else None,
        "detail": details,
    }


# ---------------------------------------------------------------------------
# 반복/중복 탐지 (Repetition Detection)
# ---------------------------------------------------------------------------


def detect_repetition(text: str) -> dict[str, Any]:
    """반복·중복 패턴 탐지 — 문장/어구/어미 반복.

    Returns:
        dict: {
            "duplicate_sentences": list,  # 동일/유사 문장
            "repeated_phrases": list,     # 3회+ 반복 어구 (2~4어절)
            "ending_monotony": list,      # 동일 종결어미 3연속+
            "score": float (0~100, 낮을수록 반복 많음)
        }
    """
    if not text or not text.strip():
        return {"duplicate_sentences": [], "repeated_phrases": [], "ending_monotony": [], "score": 100}

    sents = _split_sentences(text)

    # 1. 동일/유사 문장 탐지
    duplicates = []
    seen = {}
    for i, s in enumerate(sents):
        key = s.strip().rstrip(".")
        if key in seen:
            duplicates.append({"sentence": s[:60], "first_at": seen[key], "repeat_at": i})
        else:
            seen[key] = i

    # 2. 반복 어구 탐지 (2~4어절 ngram)
    all_tokens = _eojeols(text)
    phrase_counts: dict[str, int] = {}
    for n in range(2, 5):
        for i in range(len(all_tokens) - n + 1):
            phrase = " ".join(all_tokens[i:i+n])
            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
    repeated_phrases = [
        {"phrase": p, "count": c}
        for p, c in sorted(phrase_counts.items(), key=lambda x: -x[1])
        if c >= 3 and len(p) > 4
    ][:10]

    # 3. 종결어미 단조로움 (3연속+ 동일 어미)
    endings = []
    for s in sents:
        last = _last_eojeol(s)
        if last and len(last) >= 2:
            endings.append(last[-2:])  # 마지막 2음절

    monotony = []
    streak = 1
    for i in range(1, len(endings)):
        if endings[i] == endings[i-1]:
            streak += 1
        else:
            if streak >= 3:
                monotony.append({"ending": endings[i-1], "streak": streak, "at_sentence": i - streak})
            streak = 1
    if streak >= 3:
        monotony.append({"ending": endings[-1], "streak": streak, "at_sentence": len(endings) - streak})

    # 점수 (100 = 반복 없음)
    score = 100.0
    score -= len(duplicates) * 15
    score -= len(repeated_phrases) * 5
    score -= sum(m["streak"] for m in monotony) * 3
    score = max(0, min(100, round(score)))

    return {
        "duplicate_sentences": duplicates,
        "repeated_phrases": repeated_phrases,
        "ending_monotony": monotony,
        "score": score,
    }


# ---------------------------------------------------------------------------
# 텍스트 통계 (Text Statistics)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 시점(POV) 분석 (Narrative Voice Detection)
# ---------------------------------------------------------------------------


def detect_pov(text: str) -> dict[str, Any]:
    """소설 시점(POV) 자동 판별 — 1인칭/3인칭/혼합.

    Returns:
        dict: {
            "pov": str,  # "1인칭" | "3인칭" | "혼합" | "불명"
            "confidence": float,
            "indicators": dict,
            "detail": list
        }
    """
    if not text or not text.strip():
        return {"pov": "불명", "confidence": 0, "indicators": {}}

    # 1인칭 지표
    first_person = re.compile(r"(?:나는|내가|나를|나의|나에게|내|나도|난)\s")
    first_count = len(first_person.findall(text))

    # 3인칭 지표
    third_person = re.compile(r"(?:그는|그녀는|그가|그녀가|그의|그녀의|그를|그녀를)\s")
    third_count = len(third_person.findall(text))

    # 1인칭 내면 표현 (-고 싶다, -ㄹ까, 내 생각에)
    first_inner = re.compile(r"(?:고 싶다|고 싶었|ㄹ까|을까|내 생각|나는 생각)")
    first_inner_count = len(first_inner.findall(text))

    total = first_count + third_count
    if total == 0:
        # 대명사 없이 시점 추정
        if first_inner_count > 0:
            return {"pov": "1인칭", "confidence": 0.5, "indicators": {"inner_voice": first_inner_count}}
        return {"pov": "불명", "confidence": 0, "indicators": {}}

    first_ratio = (first_count + first_inner_count * 0.5) / (total + first_inner_count * 0.5)

    if first_ratio > 0.7:
        pov = "1인칭"
    elif first_ratio < 0.3:
        pov = "3인칭"
    else:
        pov = "혼합"

    confidence = max(first_ratio, 1 - first_ratio)

    return {
        "pov": pov,
        "confidence": round(confidence, 3),
        "indicators": {
            "first_person_count": first_count,
            "third_person_count": third_count,
            "inner_voice_count": first_inner_count,
            "first_person_ratio": round(first_ratio, 3),
        },
        "warning": "시점 혼용 감지 — 의도적이 아니라면 통일하세요" if pov == "혼합" else None,
    }


# ---------------------------------------------------------------------------
# 감각 묘사 분석 (Sensory Description Analysis)
# ---------------------------------------------------------------------------

_SENSORY_PATTERNS = {
    "시각": re.compile(r"(?:보이|보았|바라보|눈에|빛나|어둡|밝|빨간|파란|하얀|검은|반짝|흐릿|선명|번쩍|눈부)"),
    "청각": re.compile(r"(?:들리|들었|소리|울리|속삭|외치|고요|시끄|조용|울음|웃음|비명|귓가|들려)"),
    "후각": re.compile(r"(?:냄새|향기|향|악취|구수|비린|달콤|꽃향|커피향|향기로|코끝)"),
    "촉각": re.compile(r"(?:만지|느끼|차가|뜨거|따뜻|부드|거칠|아프|찌르|감촉|피부|손끝)"),
    "미각": re.compile(r"(?:맛|달콤|씁쓸|짠|매운|시큼|맛있|맛없|떫|감칠맛)"),
}


def analyze_sensory(text: str) -> dict[str, Any]:
    """감각 묘사 분석 — 5감 (시각/청각/후각/촉각/미각) 사용 빈도.

    Returns:
        dict: {
            "total_sensory": int,
            "per_sentence": float,
            "senses": {시각: count, ...},
            "dominant": str,
            "missing": list[str],
            "show_dont_tell_score": float (0~100),
            "recommendations": list[str]
        }
    """
    if not text or not text.strip():
        return {"total_sensory": 0, "senses": {}, "show_dont_tell_score": 0}

    sents = _split_sentences(text)
    senses: dict[str, int] = {}
    for name, pattern in _SENSORY_PATTERNS.items():
        senses[name] = len(pattern.findall(text))

    total = sum(senses.values())
    per_sent = round(total / max(len(sents), 1), 2)

    dominant = max(senses, key=senses.get) if total > 0 else "없음"
    missing = [name for name, count in senses.items() if count == 0]

    # 감정 직접 서술 탐지 (Show Don't Tell 위반)
    tell_patterns = re.compile(r"(?:슬펐다|기뻤다|화가 났다|행복했다|우울했다|무서웠다|놀랐다|걱정됐다|외로웠다|짜증났다)")
    tell_count = len(tell_patterns.findall(text))

    # Show Don't Tell 점수 (높을수록 감각적 묘사 많음)
    sdt_score = 50.0
    sdt_score += min(per_sent * 20, 30)  # 감각 묘사 많으면 가산
    sdt_score -= tell_count * 10          # 직접 감정 서술 많으면 감점
    sdt_score += (5 - len(missing)) * 4   # 다양한 감각 사용 가산
    sdt_score = max(0, min(100, round(sdt_score)))

    recs = []
    if per_sent < 0.3:
        recs.append("감각 묘사가 부족합니다. 시각/청각/촉각을 추가하세요.")
    if len(missing) >= 3:
        recs.append(f"사용하지 않은 감각: {', '.join(missing)}. 다양한 감각을 활용하세요.")
    if tell_count >= 3:
        recs.append(f"감정 직접 서술이 {tell_count}건입니다. 행동/감각으로 보여주세요 (Show Don't Tell).")

    return {
        "total_sensory": total,
        "per_sentence": per_sent,
        "senses": senses,
        "dominant": dominant,
        "missing": missing,
        "tell_count": tell_count,
        "show_dont_tell_score": sdt_score,
        "recommendations": recs,
    }


# ---------------------------------------------------------------------------
# 텍스트 비교 (Diff / Change Tracking)
# ---------------------------------------------------------------------------


def compute_diff(original: str, revised: str) -> dict[str, Any]:
    """텍스트 비교 — 수정 전후 변경 추적.

    Returns:
        dict: {
            "change_rate": float,
            "added_count": int,
            "removed_count": int,
            "modified_count": int,
            "changes": list[{type, original, revised, position}],
            "summary": str
        }
    """
    import difflib

    if not original and not revised:
        return {"change_rate": 0, "added_count": 0, "removed_count": 0, "changes": []}

    orig_sents = _split_sentences(original) if original else []
    rev_sents = _split_sentences(revised) if revised else []

    matcher = difflib.SequenceMatcher(None, orig_sents, rev_sents)
    changes = []
    added = 0
    removed = 0
    modified = 0

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        elif tag == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                orig_s = orig_sents[i1 + k][:60] if i1 + k < i2 else ""
                rev_s = rev_sents[j1 + k][:60] if j1 + k < j2 else ""
                changes.append({"type": "modified", "original": orig_s, "revised": rev_s, "position": i1 + k})
            modified += max(i2 - i1, j2 - j1)
        elif tag == "insert":
            for k in range(j1, j2):
                changes.append({"type": "added", "original": "", "revised": rev_sents[k][:60], "position": k})
            added += j2 - j1
        elif tag == "delete":
            for k in range(i1, i2):
                changes.append({"type": "removed", "original": orig_sents[k][:60], "revised": "", "position": k})
            removed += i2 - i1

    total_sents = max(len(orig_sents), len(rev_sents), 1)
    change_rate = round((added + removed + modified) / total_sents, 3)

    # 변경률 가드
    warning = None
    if change_rate > 0.5:
        warning = "과윤문 경고: 변경률 50% 초과"
    elif change_rate > 0.3:
        warning = "주의: 변경률 30% 초과"

    return {
        "change_rate": change_rate,
        "added_count": added,
        "removed_count": removed,
        "modified_count": modified,
        "unchanged_count": len(orig_sents) - removed - modified,
        "changes": changes[:20],  # 상위 20건만
        "warning": warning,
        "summary": f"{modified}건 수정, {added}건 추가, {removed}건 삭제 (변경률 {change_rate:.0%})",
    }


# ---------------------------------------------------------------------------
# 캐릭터 말투 일관성 분석
# ---------------------------------------------------------------------------


def analyze_character_voice(text: str, character_names: list[str] | None = None) -> dict[str, Any]:
    """캐릭터별 말투 특성 분석 — 대사 추출 + 종결어미 + 어휘 패턴.

    Returns:
        dict: {
            "characters": {name: {dialogue_count, endings, avg_length, style}},
            "consistency_issues": list[str]
        }
    """
    if not text or not text.strip():
        return {"characters": {}, "consistency_issues": []}

    # 대사 + 화자 추출 (패턴: "대사" OOO이/가 말했다)
    lines = text.split("\n")
    char_dialogues: dict[str, list[str]] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 패턴 1: "대사" 화자가 말했다
        m = re.search(r'["\u201c](.*?)["\u201d]\s*([가-힣]{2,5})(?:이|가|은|는)\s*(?:말했|물었|대답|외쳤|속삭|중얼|소리)', line)
        if m:
            dialogue, speaker = m.group(1), m.group(2)
            if speaker not in char_dialogues:
                char_dialogues[speaker] = []
            char_dialogues[speaker].append(dialogue)
            continue

        # 패턴 2: 화자가 말했다. "대사"
        m2 = re.search(r'([가-힣]{2,5})(?:이|가|은|는)\s*(?:말했|물었).*?["\u201c](.*?)["\u201d]', line)
        if m2:
            speaker, dialogue = m2.group(1), m2.group(2)
            if speaker not in char_dialogues:
                char_dialogues[speaker] = []
            char_dialogues[speaker].append(dialogue)

    # 지정된 캐릭터 이름으로 필터
    if character_names:
        for name in character_names:
            if name not in char_dialogues:
                char_dialogues[name] = []

    # 캐릭터별 분석
    characters = {}
    for name, dialogues in char_dialogues.items():
        if not dialogues:
            characters[name] = {"dialogue_count": 0, "style": "대사 없음"}
            continue

        # 종결어미 분석
        endings_counter: dict[str, int] = {}
        for d in dialogues:
            d_stripped = d.rstrip("?!.")
            if len(d_stripped) >= 2:
                ending = d_stripped[-2:]
                endings_counter[ending] = endings_counter.get(ending, 0) + 1

        top_endings = sorted(endings_counter.items(), key=lambda x: -x[1])[:5]
        avg_len = round(sum(len(d) for d in dialogues) / len(dialogues), 1)

        # 말투 스타일 추정
        formal_count = sum(1 for d in dialogues if re.search(r"(?:습니다|세요|시오)[\.!?\s]?$", d))
        informal_count = sum(1 for d in dialogues if re.search(r"(?:야|어|지|거든|잖아)[\.!?\s]?$", d))
        style = "격식체" if formal_count > informal_count else "비격식체" if informal_count > 0 else "혼합"

        characters[name] = {
            "dialogue_count": len(dialogues),
            "avg_length": avg_len,
            "top_endings": [{"ending": e, "count": c} for e, c in top_endings],
            "style": style,
        }

    # 일관성 이슈
    issues = []
    for name, info in characters.items():
        if info.get("dialogue_count", 0) >= 3 and info.get("style") == "혼합":
            issues.append(f"{name}의 말투가 격식/비격식 혼용입니다.")

    return {
        "characters": characters,
        "total_speakers": len([c for c in characters.values() if c.get("dialogue_count", 0) > 0]),
        "consistency_issues": issues,
    }


# ---------------------------------------------------------------------------
# 의성어/의태어 분석 (Onomatopoeia & Mimetic Words)
# ---------------------------------------------------------------------------

_ONOMATOPOEIA = [
    # 소리 (의성어)
    "쿵", "탕", "뻥", "쾅", "콩", "딸랑", "찰랑", "졸졸", "콸콸", "쏴아",
    "뚝뚝", "똑똑", "사각사각", "바스락", "부스럭", "삐걱", "끼익",
    "짹짹", "꽥", "멍멍", "야옹", "꼬끼오", "음메", "어흥",
    "딩동", "띵", "쨍", "철컥", "덜컹", "부릉", "윙", "쉿",
    "후우", "휘이", "와르르", "우르르", "펑", "뻥", "빵",
    "깔깔", "킥킥", "히히", "하하", "호호", "흑흑", "엉엉",
    "쩝", "쪽", "꿀꺽", "뽀뽀", "뿌지직",
]

_MIMETIC = [
    # 모양/움직임 (의태어)
    "반짝반짝", "번쩍번쩍", "깜빡깜빡", "살살", "슬슬", "쏙쏙",
    "빙글빙글", "뱅글뱅글", "둥둥", "데굴데굴", "팔랑팔랑",
    "아장아장", "뒤뚱뒤뚱", "어슬렁", "성큼성큼", "총총",
    "두근두근", "콩닥콩닥", "벌벌", "부들부들", "와들와들",
    "꼬불꼬불", "울퉁불퉁", "반들반들", "울긋불긋", "알록달록",
    "보슬보슬", "부슬부슬", "송글송글", "주르르", "줄줄",
    "쨍그랑", "흐물흐물", "말랑말랑", "딱딱", "폭신폭신",
    "찰싹", "후다닥", "번쩍", "우뚝", "벌떡", "꿈틀",
    "빈둥빈둥", "꾸벅꾸벅", "끄덕끄덕", "고개를 갸웃",
]


def analyze_expressiveness(text: str) -> dict[str, Any]:
    """의성어/의태어 + 감각 묘사 통합 분석 — 소설 생동감 측정.

    Returns:
        dict: {
            "onomatopoeia": {found: list, count: int},
            "mimetic": {found: list, count: int},
            "sensory": {5감 빈도},
            "vividness_score": float (0~100),
            "recommendations": list
        }
    """
    if not text or not text.strip():
        return {"onomatopoeia": {"found": [], "count": 0}, "mimetic": {"found": [], "count": 0}, "vividness_score": 0}

    sents = _split_sentences(text)
    total_sents = max(len(sents), 1)

    # 의성어 탐지
    ono_found = []
    for word in _ONOMATOPOEIA:
        count = text.count(word)
        if count > 0:
            ono_found.append({"word": word, "count": count})

    # 의태어 탐지
    mim_found = []
    for word in _MIMETIC:
        count = text.count(word)
        if count > 0:
            mim_found.append({"word": word, "count": count})

    ono_total = sum(f["count"] for f in ono_found)
    mim_total = sum(f["count"] for f in mim_found)

    # 감각 묘사 (sensory 재사용)
    sensory = analyze_sensory(text)

    # 생동감 점수
    score = 30.0
    score += min(ono_total * 5, 15)                          # 의성어
    score += min(mim_total * 5, 15)                          # 의태어
    score += min(sensory["total_sensory"] * 3, 20)           # 감각 묘사
    score += (5 - len(sensory["missing"])) * 4               # 감각 다양성
    score -= sensory.get("tell_count", 0) * 5                # 직접 서술 감점
    score = max(0, min(100, round(score)))

    recs = []
    if ono_total == 0:
        recs.append("의성어가 없습니다. 소리 묘사를 추가하면 생동감이 높아집니다.")
    if mim_total == 0:
        recs.append("의태어가 없습니다. 움직임/모양 묘사를 추가하면 현장감이 높아집니다.")
    if sensory["total_sensory"] / total_sents < 0.2:
        recs.append("감각 묘사가 부족합니다. 시각/청각/촉각을 보강하세요.")

    return {
        "onomatopoeia": {"found": sorted(ono_found, key=lambda x: -x["count"]), "count": ono_total},
        "mimetic": {"found": sorted(mim_found, key=lambda x: -x["count"]), "count": mim_total},
        "sensory_summary": {
            "total": sensory["total_sensory"],
            "senses": sensory["senses"],
            "missing": sensory["missing"],
        },
        "vividness_score": score,
        "per_sentence": round((ono_total + mim_total + sensory["total_sensory"]) / total_sents, 2),
        "recommendations": recs,
    }


_HANGUL_RE = re.compile(r"[\uAC00-\uD7AF]")


def compute_text_stats(text: str) -> dict[str, Any]:
    """한국어 텍스트 종합 통계 분석.

    Returns:
        dict: {
            "char_count": int,
            "word_count": int,
            "sentence_count": int,
            "paragraph_count": int,
            "avg_sentence_length": float,
            "word_frequency": list[{word, count}],  # TOP 20
            "repeated_words": list[{word, count}],   # 5회+ 반복
            "sentence_types": {declarative, interrogative, exclamatory, imperative},
            "dialogue_ratio": float,
            "vocabulary_richness": {ttr, hapax_ratio, top10_coverage},
        }
    """
    if not text or not text.strip():
        return {"char_count": 0, "word_count": 0, "sentence_count": 0}

    sents = _split_sentences(text)
    tokens = _all_tokens(text)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    # 어휘 빈도
    from collections import Counter
    word_counts = Counter(tokens)
    top_words = [{"word": w, "count": c} for w, c in word_counts.most_common(20)]
    repeated = [{"word": w, "count": c} for w, c in word_counts.most_common() if c >= 5]

    # 문장 유형 분류
    declarative = 0
    interrogative = 0
    exclamatory = 0
    imperative = 0
    for s in sents:
        s_stripped = s.rstrip()
        if s_stripped.endswith("?"):
            interrogative += 1
        elif s_stripped.endswith("!"):
            exclamatory += 1
        elif any(s_stripped.endswith(e) for e in ("세요", "하세요", "하라", "해라", "하시오")):
            imperative += 1
        else:
            declarative += 1

    total_sents = max(len(sents), 1)
    sent_types = {
        "declarative": round(declarative / total_sents, 3),
        "interrogative": round(interrogative / total_sents, 3),
        "exclamatory": round(exclamatory / total_sents, 3),
        "imperative": round(imperative / total_sents, 3),
    }

    # 대화문 비율 (따옴표 안 텍스트)
    dialogue_chars = 0
    for match in re.finditer(r'["\u201c\u201d](.*?)["\u201c\u201d]', text):
        dialogue_chars += len(match.group(1))
    dialogue_ratio = round(dialogue_chars / max(len(text), 1), 3)

    # 어휘 풍부도
    unique_count = len(set(tokens))
    total_count = len(tokens)
    ttr = round(unique_count / total_count, 3) if total_count > 0 else 0
    hapax = sum(1 for c in word_counts.values() if c == 1)
    hapax_ratio = round(hapax / total_count, 3) if total_count > 0 else 0
    top10_count = sum(c for _, c in word_counts.most_common(10))
    top10_coverage = round(top10_count / total_count, 3) if total_count > 0 else 0

    # 평균 문장 길이
    sent_lengths = [len(_eojeols(s)) for s in sents]
    avg_sent_len = round(sum(sent_lengths) / len(sent_lengths), 1) if sent_lengths else 0

    return {
        "char_count": len(text),
        "word_count": total_count,
        "sentence_count": len(sents),
        "paragraph_count": len(paragraphs),
        "avg_sentence_length": avg_sent_len,
        "word_frequency": top_words,
        "repeated_words": repeated,
        "sentence_types": sent_types,
        "dialogue_ratio": dialogue_ratio,
        "vocabulary_richness": {
            "ttr": ttr,
            "unique_words": unique_count,
            "hapax_ratio": hapax_ratio,
            "top10_coverage": top10_coverage,
        },
    }
