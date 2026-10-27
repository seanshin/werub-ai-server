"""국가법령정보센터 3단비교표 PDF 파서.

law.go.kr이 생성하는 「3단비교표(법률-시행령-시행규칙)」 PDF를 컬럼 단위로 분리하고
조문(제N조) 경계로 청킹한다. 의료법 RAG 부트스트랩 코퍼스 적재(medical-law-rag-augmentation P0)용.

- 순수 Python(pdfplumber만 의존, lazy import — 미설치 시 import 시점 아닌 호출 시 에러).
- 컬럼 경계는 표 괘선(vertical edges) x좌표로 자동 추정, 실패 시 고정 분수 폴백.
- rhwp(hwp) 경로와 별개 — 이 모듈은 law.go.kr 텍스트 PDF 전용.

검증: 의료법(134조)/약사법(217)/의료급여법(50)/국민건강보험법(147)/의료기기유통규칙(13) 5종 3단 동형.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 컬럼 라벨(3단 = 법률/시행령/시행규칙)
LAW_LEVELS = ("법률", "시행령", "시행규칙")

# 헤더/푸터 등 노이즈 라인
_NOISE = [
    re.compile(r"\(3단비교표\)"),
    re.compile(r"\(법령단위비교\)"),
    re.compile(r"^법제처$"),
    re.compile(r"^국가법령정보센터$"),
    re.compile(r"^\d{1,4}$"),  # 페이지 번호
]

# 조문 헤더: 제N조 / 제N조의M  + (제목)
_ARTICLE_HEAD = re.compile(r"제\d+조(?:의\d+)?\s*\(")
_ARTICLE_TITLE = re.compile(r"(제\d+조(?:의\d+)?)\s*\(\s*([^)]{1,40}?)\s*\)")
# 상단 메타: [법률 제21524호, 2026. 4. 7., 일부개정]
_META = re.compile(r"제\s*(\d+)\s*호[,，]\s*([\d.\s]+?)[,，]\s*([^\]\n]+)")


@dataclass
class LawArticle:
    law_level: str          # 법률 | 시행령 | 시행규칙
    article_number: str     # 제22조 / 제24조의2
    article_title: str      # 진료기록부 등
    text: str               # 조문 본문(헤더 포함)


@dataclass
class Law3DanDoc:
    law_name: str
    source_file: str
    meta: dict = field(default_factory=dict)  # level→{number, effective, kind}
    articles: list[LawArticle] = field(default_factory=list)

    def by_level(self, level: str) -> list[LawArticle]:
        return [a for a in self.articles if a.law_level == level]


def _is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    return any(p.search(s) for p in _NOISE)


def _join_wrapped(lines: list[str]) -> str:
    """law.go.kr PDF는 음절 중간에서 강제 개행한다. 구조 마커(①②/숫자./가나다/<개정>)로
    시작하지 않는 후속 라인은 직전 라인에 이어붙여 가독·검색 품질을 높인다."""
    out: list[str] = []
    marker = re.compile(r"^(\s*[①-⑳]|\s*\d+\.|\s*[가-힣]\.|\s*제\d+조|\s*<|\s*\[|\s*-)")
    for ln in lines:
        ln = ln.rstrip()
        if not ln:
            continue
        if out and not marker.match(ln):
            # 직전이 문장부호로 안 끝나면(=중간 wrap) 공백 없이 붙임
            prev = out[-1]
            if prev and prev[-1] not in ".!?。:":
                out[-1] = prev + ln.strip()
                continue
        out.append(ln.strip())
    return "\n".join(out)


def _detect_boundaries(page) -> list[float]:
    """표 괘선(vertical edges) x좌표 → 컬럼 경계 4개. 실패 시 빈 리스트."""
    xs = sorted({round(e["x0"]) for e in page.edges if e.get("orientation") == "v"})
    # 인접 중복 제거(±3px)
    merged: list[float] = []
    for x in xs:
        if not merged or x - merged[-1] > 3:
            merged.append(x)
    return merged if len(merged) >= 4 else []


def _column_text(page, x0: float, x1: float) -> str:
    crop = page.crop((x0, 0, x1, page.height))
    txt = crop.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
    lines = [l for l in txt.split("\n") if not _is_noise(l)]
    return "\n".join(lines)


def _parse_header_meta(page, boundaries: list[float]) -> dict:
    """1페이지 상단 메타행에서 컬럼별 법령번호/시행일 추출."""
    meta: dict = {}
    for i, lvl in enumerate(LAW_LEVELS):
        x0, x1 = boundaries[i], boundaries[i + 1]
        head = _column_text(page, x0, x1)[:300]
        m = _META.search(head)
        if m:
            meta[lvl] = {
                "number": m.group(1),
                "effective": re.sub(r"\s+", "", m.group(2)).rstrip("."),
                "kind": m.group(3).strip(),
            }
    return meta


def _split_articles(col_text: str, level: str) -> list[LawArticle]:
    arts: list[LawArticle] = []
    heads = list(_ARTICLE_HEAD.finditer(col_text))
    for idx, h in enumerate(heads):
        start = h.start()
        end = heads[idx + 1].start() if idx + 1 < len(heads) else len(col_text)
        seg = col_text[start:end]
        tm = _ARTICLE_TITLE.match(seg)
        num = tm.group(1) if tm else seg[:10]
        title = tm.group(2).replace("\n", "").strip() if tm else ""
        body = _join_wrapped(seg.split("\n"))
        arts.append(LawArticle(law_level=level, article_number=num,
                               article_title=title, text=body))
    return arts


def parse_3dan_pdf(path: str) -> Law3DanDoc:
    """3단비교표 PDF → Law3DanDoc(법률/시행령/시행규칙 조문)."""
    import pdfplumber  # lazy

    law_name = _infer_law_name(path)
    doc = Law3DanDoc(law_name=law_name, source_file=path)
    col_buf: dict[str, list[str]] = {lvl: [] for lvl in LAW_LEVELS}

    with pdfplumber.open(path) as pdf:
        p0 = pdf.pages[1] if len(pdf.pages) > 1 else pdf.pages[0]
        bounds = _detect_boundaries(p0)
        if not bounds:
            w = p0.width
            bounds = [w * f for f in (0.047, 0.349, 0.652, 0.954)]
        bounds = bounds[:4]
        doc.meta = _parse_header_meta(pdf.pages[0], bounds)

        for pg in pdf.pages:
            for i, lvl in enumerate(LAW_LEVELS):
                col_buf[lvl].append(_column_text(pg, bounds[i], bounds[i + 1]))

    for lvl in LAW_LEVELS:
        full = "\n".join(col_buf[lvl])
        doc.articles.extend(_split_articles(full, lvl))
    return doc


def to_chunks(doc: Law3DanDoc) -> list[dict]:
    """RAG 색인용 청크 리스트. 조문 1개 = 청크 1개(법/시행령/시행규칙 각각)."""
    chunks: list[dict] = []
    for seq, a in enumerate(doc.articles):
        if len(a.text.strip()) < 10:
            continue
        eff = (doc.meta.get(a.law_level) or {}).get("effective", "")
        chunks.append({
            "text": a.text,
            "metadata": {
                "law_name": doc.law_name,
                "law_level": a.law_level,
                "article_number": a.article_number,
                "article_title": a.article_title,
                "effective_date": eff,
                "source": "law.go.kr/3dan",
                "source_file": doc.source_file.rsplit("/", 1)[-1],
                "chunk_seq": seq,
                "chunk_kind": "article",
                "origin": "law3dan",
            },
        })
    return chunks


def _infer_law_name(path: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFC", path.rsplit("/", 1)[-1])  # macOS NFD 대응
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    name = re.sub(r"[\(（].*?[\)）]", "", name)          # (3단비교) 제거
    name = re.sub(r"3단비교|법령단위비교|_+", " ", name).strip()
    return name or "법령"
