"""HIRA 「건강보험요양급여비용」(상대가치점수책) PDF 파서.

건강보험심사평가원이 발간하는 「건강보험요양급여비용」(예: 2021년 2월판)에서
행위 급여 코드마스터와 산정지침을 추출한다. 한 번의 파싱으로 두 산출물을 만든다.

  ① fee_codes 적재용 행 — code → category(TARIFF) → name(분류 경로)
     (ERP 사전심사 code→notice_type 자동해석, core/fee_codes.py)
  ② medical_law_knowledge RAG 청크 — 분류번호 항목별(코드·점수·산정지침) +
     별첨3 신의료기술 안전성·유효성 평가결과(목적·대상·방법)

표 레이아웃(2021.2판 기준, 페이지별 자동검출 + 폴백):
  상대가치점수 표 = 4컬럼  분류번호 | 코 드 | 분 류 | 점 수
  신의료기술 평가결과 = 3컬럼  분류번호 | 코 드 | 분 류  (점수 없음)

순수 Python(pdfplumber만 의존, lazy import). 비식별(환자정보 없음, 공개 고시).
law_pdf_parser(3단비교 전용)·doc_parser(단일컬럼 고시)와 별개 — 이 책은 다컬럼 코드표.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 행위 급여 코드: 영문 1~2 + 숫자 3~4 (예: F6100, AU931, SB271, CZ585)
_CODE_RE = re.compile(r"^[A-Z]{1,2}\d{3,4}$")
# 분류번호: 가-610 / 나-610-1 / 노-581 / 자-17-2 (한글 1 + - + 숫자[-숫자...])
_CATNO_RE = re.compile(r"^[가-힣]-\d+(?:-\d+)?$")
# 점수: 1,220.21 / 146.75 (천단위 콤마 허용, 소수 2자리)
_SCORE_RE = re.compile(r"^\d{1,3}(?:,\d{3})*\.\d{2}$")

# 헤더/꼬리 노이즈(컬럼 본문에서 제외)
_NOISE_RE = re.compile(
    r"행위 급여|비급여 목록|상대가치점수|신의료기술 안전성|분류번호|^제\d+편|^제\d+부|"
    r"^제\d+장|^제\d+절|^[ivxIVX]+$|^\d{1,4}$|^HIRA$|^분\s*류$|^코\s*드$|^점\s*수$"
)
# 목차(점선 리더 + 끝 페이지번호) 라인 — 본문 아님
_TOC_RE = re.compile(r"[·．.…·]{4,}\s*\d{1,4}\s*$")

# 점수책 = 행위 급여(수가) → fee_codes category. 약가/치료재료는 별도 파일.
DEFAULT_CATEGORY = "TARIFF"


@dataclass
class FeeItem:
    code: str
    score: str          # "146.75" 또는 "" (산정코드·주 변형은 점수 없음)
    name: str           # 분류 경로: "신경학적 검사 > 가. 단순검사 Simple"
    category_no: str    # 분류번호(나-610) — 상위 항목
    page: int


@dataclass
class RagChunk:
    text: str
    category_no: str    # 나-610 / 별첨3 항목은 분류번호 또는 ""
    title: str          # 분류명/항목명
    section: str        # "valuepoint" | "eval"  (상대가치점수 / 신의료기술 평가결과)
    page: int


@dataclass
class ParsedValuepoint:
    source_file: str
    edition: str = ""                 # "2021년 2월판"(검출 시)
    fee_items: list = field(default_factory=list)   # FeeItem
    chunks: list = field(default_factory=list)      # RagChunk


def _row_cluster(words: list, y_tol: float = 3.5) -> list:
    """워드를 행(같은 top y) 단위로 묶는다. 반환: top 오름차순 행 리스트(각 행=워드 리스트)."""
    rows: list = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(w["top"] - rows[-1][0]) <= y_tol:
            rows[-1][1].append(w)
        else:
            rows.append((w["top"], [w]))
    return [sorted(r[1], key=lambda w: w["x0"]) for r in rows]


def _detect_inner_bounds(page) -> list:
    """표 괘선(vertical edges) → 내부 컬럼 경계. 4컬럼이면 [b_code, b_name, b_score],
    3컬럼이면 [b_code, b_name]. 검출 실패 시 폭 분수 폴백."""
    xs = sorted({round(e["x0"]) for e in page.edges if e.get("orientation") == "v"})
    merged: list = []
    for x in xs:
        if not merged or x - merged[-1] > 4:
            merged.append(x)
    w = page.width
    if len(merged) >= 5:                       # 분류번호|코드|분류|점수
        return [merged[1], merged[2], merged[3]]
    if len(merged) == 4:                       # 분류번호|코드|분류 (별첨3)
        return [merged[1], merged[2]]
    # 폴백(2021.2판 비율): 코드 95, 분류 128, 점수 354 / width 454
    return [w * 0.209, w * 0.282, w * 0.780]


def _cells(row: list, bounds: list) -> tuple:
    """행 워드를 컬럼 경계로 분배 → (분류번호, 코드, 분류, 점수) 문자열."""
    b_code, b_name = bounds[0], bounds[1]
    b_score = bounds[2] if len(bounds) >= 3 else 10_000.0
    c0: list = []; c1: list = []; c2: list = []; c3: list = []
    for w in row:
        cx = (w["x0"] + w["x1"]) / 2
        if cx < b_code:
            c0.append(w["text"])
        elif cx < b_name:
            c1.append(w["text"])
        elif cx < b_score:
            c2.append(w["text"])
        else:
            c3.append(w["text"])
    return (" ".join(c0).strip(), " ".join(c1).strip(),
            " ".join(c2).strip(), " ".join(c3).strip())


def _page_kind(text: str) -> str:
    if "신의료기술 안전성" in text:
        return "eval"
    if "분류번호" in text and ("점 수" in text or "점수" in text):
        return "valuepoint"
    return "other"


_EDITION_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월판")


def parse_valuepoint(path: str, max_pages: int | None = None) -> ParsedValuepoint:
    """HIRA 상대가치점수책 PDF → fee_items + RAG chunks."""
    import pdfplumber  # lazy

    import os
    doc = ParsedValuepoint(source_file=os.path.basename(path))
    seen_codes: dict = {}        # code → FeeItem (중복 제거, 첫 등장 우선)
    cur_catno = ""               # 진행 중 분류번호
    cur_title = ""               # 진행 중 분류명
    buf: list = []               # 진행 중 청크 라인 버퍼
    buf_section = "valuepoint"
    buf_page = 0

    def _flush():
        nonlocal buf
        if not buf:
            return
        body = "\n".join(buf).strip()
        if len(body) >= 15:
            doc.chunks.append(RagChunk(
                text=body, category_no=cur_catno, title=cur_title[:120],
                section=buf_section, page=buf_page))
        buf = []

    with pdfplumber.open(path) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for pidx, page in enumerate(pages):
            ptext = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
            if not doc.edition:
                em = _EDITION_RE.search(ptext)
                if em:
                    doc.edition = f"{em.group(1)}년 {int(em.group(2))}월판"
            kind = _page_kind(ptext)
            if kind == "other":
                continue
            bounds = _detect_inner_bounds(page)
            words = page.extract_words(x_tolerance=1.5, y_tolerance=3)
            for row in _row_cluster(words):
                catno, code, name, score = _cells(row, bounds)
                line = " ".join(x for x in (code, name, score) if x).strip()
                if not line or _NOISE_RE.search(name) or _TOC_RE.search(line):
                    continue
                # 새 분류번호 → 청크 경계
                if catno and _CATNO_RE.match(catno):
                    _flush()
                    cur_catno = catno
                    cur_title = name or ""
                    buf_section = "eval" if kind == "eval" else "valuepoint"
                    buf_page = pidx + 1
                    buf.append(f"[{catno}] {name}".strip())
                    if score:
                        buf[-1] += f"  {score}점"
                else:
                    if line:
                        buf.append(line)
                        if not buf_page:
                            buf_page = pidx + 1
                # fee_code 추출(상대가치점수 표에서 코드+점수 또는 코드 단독)
                if kind == "valuepoint" and code:
                    first = code.split()[0]
                    if _CODE_RE.match(first) and first not in seen_codes:
                        path_name = (f"{cur_title} > {name}".strip(" >")
                                     if cur_title and name else (name or cur_title))
                        seen_codes[first] = FeeItem(
                            code=first, score=score if _SCORE_RE.match(score) else "",
                            name=path_name[:200], category_no=cur_catno, page=pidx + 1)
    _flush()
    doc.fee_items = list(seen_codes.values())
    return doc


def fee_rows(doc: ParsedValuepoint, category: str = DEFAULT_CATEGORY) -> list:
    """ParsedValuepoint → core.fee_codes.import_fee_codes() 입력 행."""
    rows = []
    for it in doc.fee_items:
        nm = it.name
        if it.score:
            nm = f"{nm} ({it.score}점)" if nm else f"({it.score}점)"
        rows.append({"code": it.code, "category": category, "name": nm[:240]})
    return rows


def _split_body(text: str, max_chars: int = 800, overlap: int = 80) -> list:
    """과대 청크를 라인 경계 우선으로 ~max_chars 조각으로 분할(임베딩 num_ctx·검색 입도 보호)."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    out: list = []
    cur = ""
    for ln in text.split("\n"):
        if len(ln) > max_chars:                      # 단일 초장문 라인 → 하드 슬라이스
            if cur:
                out.append(cur); cur = ""
            i = 0
            while i < len(ln):
                out.append(ln[i:i + max_chars])
                i += max_chars - overlap
            continue
        if cur and len(cur) + len(ln) + 1 > max_chars:
            out.append(cur); cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    if cur:
        out.append(cur)
    return [c.strip() for c in out if len(c.strip()) >= 15]


def rag_documents(doc: ParsedValuepoint, max_chars: int = 800) -> list:
    """ParsedValuepoint → RAG 색인용 (doc_id, text, metadata) 리스트.

    metadata는 medical_law_knowledge 컨벤션(law_name/category/origin/source_file)에 맞춘다.
    origin='hira_valuepoint'로 KHIDI/law3dan과 구분(덮어쓰기·정리 안전).
    과대 청크는 _split_body로 서브분할(상위 분류번호·제목 메타는 공유)."""
    import hashlib
    sf = doc.source_file
    ed = doc.edition or ""
    out = []
    for seq, ch in enumerate(doc.chunks):
        law_name = ("건강보험요양급여비용 신의료기술 평가결과"
                    if ch.section == "eval"
                    else "건강보험요양급여비용 상대가치점수")
        if ed:
            law_name += f" ({ed})"
        parts = _split_body(ch.text, max_chars=max_chars)
        for sub, body in enumerate(parts):
            did = "hv-" + hashlib.md5(f"{sf}|{seq}|{sub}".encode("utf-8")).hexdigest()[:16]
            out.append((did, body, {
                "law_name": law_name,
                "law_level": "고시",
                "category": "신의료기술" if ch.section == "eval" else "급여기준",
                "doc_no": "제2021-45호",
                "source": "HIRA",
                "source_file": sf,
                "origin": "hira_valuepoint",
                "section": ch.section,
                "category_no": ch.category_no,
                "title": ch.title,
                "chunk_seq": seq,
                "sub_seq": sub,
                "page": ch.page,
            }))
    return out
