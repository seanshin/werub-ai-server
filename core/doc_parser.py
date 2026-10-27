"""일반 고시/규정 문서 파서 — KHIDI 등에서 사용자가 수동 다운로드한 고시 본문 처리.

law_pdf_parser.py(법령 3단비교 전용)와 별개. KHIDI 고시는 단일컬럼 본문 PDF/HWPX.
robots.txt 준수: 자동 크롤 다운로드 금지(/fileDownload Disallow) → 사용자 수동 다운로드 전제.

확장자 디스패치:
  .pdf            → pdfplumber 전체텍스트(단일컬럼) + 노이즈 제거
  .hwp/.hwpx/.hwp3→ rhwp CLI(subprocess, config.json rhwp.cli_path). 미설정/미배포 시 skip.
  .txt            → 그대로
청킹: 조문(제N조)·별표·항(①) 경계 우선, 없으면 슬라이딩.
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass

# KHIDI/법제처 머리말·꼬리말·페이지 노이즈
_NOISE = [
    re.compile(r"^\s*-\s*\d+\s*-\s*$"),           # - 12 -
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),          # 3/10
    re.compile(r"국가법령정보센터|법제처"),
    re.compile(r"^\s*\d{1,4}\s*$"),                # 페이지 번호 단독
]

# 조문/구조 경계
_BOUNDARY = re.compile(r"(?=^\s*(제\d+조(?:의\d+)?\s*\(|별표\s*\d|\[붙임|부\s*칙|제\d+장\s))", re.M)
_LEGACY_HWP_EXT = {".hwp", ".hwp3"}  # 구형 OLE 바이너리 → rhwp 필요

# HWPX(OWPML, XML ZIP) 텍스트 추출 — 순수 Python(rhwp 불필요)
_HWPX_SEC = re.compile(r"section\d+\.xml$")
_HP_P = re.compile(r"<[^>]*?:p(?:\s[^>]*)?>(.*?)</[^>]*?:p>", re.S)   # 문단
_HP_T = re.compile(r"<[^>]*?:t(?:\s[^>]*)?>(.*?)</[^>]*?:t>", re.S)   # 텍스트 런


_HWPX_MAX_DECOMPRESSED = 100 * 1024 * 1024  # 압축해제 상한 100MB (zip bomb 방어)


def _extract_hwpx(path: str) -> ParsedDoc:
    """HWPX(XML ZIP) → 텍스트. Contents/section*.xml의 hp:p(문단)·hp:t(런) 추출."""
    try:
        with zipfile.ZipFile(path) as z:
            total = sum(i.file_size for i in z.infolist())
            if total > _HWPX_MAX_DECOMPRESSED:
                return ParsedDoc(text="", parser="hwpx-py", ok=False,
                                 error=f"HWPX 압축해제 상한 초과({total} bytes)")
            secs = sorted(n for n in z.namelist() if _HWPX_SEC.search(n))
            paras: list[str] = []
            for s in secs:
                xml = z.read(s).decode("utf-8", "replace")
                pm_any = False
                for pm in _HP_P.finditer(xml):
                    pm_any = True
                    run = "".join(re.sub(r"<[^>]+>", "", t) for t in _HP_T.findall(pm.group(1)))
                    run = html.unescape(run).strip()
                    if run:
                        paras.append(run)
                if not pm_any:  # 문단 매칭 실패 시 런만이라도
                    for t in _HP_T.findall(xml):
                        run = html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
                        if run:
                            paras.append(run)
        text = _clean_lines("\n".join(paras))
        return ParsedDoc(text=text, parser="hwpx-py", ok=bool(text.strip()),
                         error=None if text.strip() else "빈 텍스트")
    except Exception as e:  # noqa: BLE001
        return ParsedDoc(text="", parser="hwpx-py", ok=False, error=str(e))

# 고시 메타: 고시번호 "제2026-125호"(공백허용), 시행일 "...시행"/"시행: ..."
_DOCNO = re.compile(r"제\s*(\d{4})\s*-\s*(\d{1,4})\s*호")
_EFFECT = re.compile(r"(\d{4})[.\-년]\s*(\d{1,2})[.\-월]\s*(\d{1,2})\s*일?\s*(?:부터)?\s*시행"
                     r"|시행\s*[:：]?\s*(\d{4})[.\-년]\s*(\d{1,2})[.\-월]\s*(\d{1,2})")


# 고시 category 분류(키워드 기반, 구체→일반 순서) — /review·/ask 필터 정밀화(O5)
_NOTICE_CATEGORY = [
    (("신의료기술", "의료기술평가", "평가유예"), "신의료기술"),
    (("선별급여",), "선별급여"),
    (("의료기기", "체외진단"), "의료기기"),
    (("적용기준", "세부사항", "심사지침", "심사기준"), "심사기준"),
    (("치료재료", "급여상한금액", "약가", "약제급여", "상대가치점수", "수가",
      "급여·비급여 목록", "급여 비급여 목록", "급여비급여목록", "행위 급여"), "급여기준"),
    (("감염병", "예방접종", "역학"), "infection"),
    (("진료기록", "의무기록", "기록 보존", "기록보존"), "record"),
    (("의료광고", "광고"), "advertisement"),
]


def classify_notice_category(name: str) -> str:
    """고시 제목 → category(키워드). 미매칭=기타."""
    n = name or ""
    for kws, cat in _NOTICE_CATEGORY:
        if any(k in n for k in kws):
            return cat
    return "기타"


def notice_metadata(path: str, text: str) -> dict:
    """고시 문서 → 색인 메타(law_name=제목, doc_no, effective_date, category, origin=khidi_notice)."""
    import unicodedata
    fname = unicodedata.normalize("NFC", os.path.basename(path))
    dm = _DOCNO.search(text[:600])
    em = _EFFECT.search(text)  # 시행일은 부칙(말미)에 흔함 → 전체 검색
    eff = ""
    if em:
        g = em.groups()
        y, mo, d = (g[0], g[1], g[2]) if g[0] else (g[3], g[4], g[5])
        eff = f"{y}.{int(mo)}.{int(d)}"
    title = os.path.splitext(fname)[0]
    return {
        "law_name": title,
        "law_level": "고시",
        "doc_no": (f"제{dm.group(1)}-{dm.group(2)}호") if dm else "",
        "effective_date": eff,
        "category": classify_notice_category(title + " " + text[:200]),
        "source": "khidi",
        "source_file": fname,
        "origin": "khidi_notice",
    }


@dataclass
class ParsedDoc:
    text: str
    parser: str          # pdfplumber | rhwp | text | skipped
    ok: bool
    page_count: int = 0
    error: str | None = None


def _clean_lines(text: str) -> str:
    out = []
    for ln in text.split("\n"):
        s = ln.rstrip()
        if not s.strip():
            continue
        if any(p.search(s.strip()) for p in _NOISE):
            continue
        out.append(s)
    return "\n".join(out)


def _extract_pdf_text(path: str) -> ParsedDoc:
    import pdfplumber  # lazy
    parts, n = [], 0
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        for pg in pdf.pages:
            parts.append(pg.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
    text = _clean_lines("\n".join(parts))
    return ParsedDoc(text=text, parser="pdfplumber", ok=bool(text.strip()), page_count=n)


def _load_rhwp_cfg(base_dir: str | None) -> dict:
    """config.json rhwp 블록(없으면 비활성)."""
    base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base_dir, "config.json"), encoding="utf-8") as f:
            return (json.load(f).get("rhwp") or {})
    except Exception:  # noqa: BLE001
        return {}


def _extract_hancom_via_rhwp(path: str, base_dir: str | None = None) -> ParsedDoc:
    """rhwp CLI(subprocess)로 .hwp/.hwpx → 텍스트. 미설정/미배포 시 skipped."""
    cfg = _load_rhwp_cfg(base_dir)
    cli = cfg.get("cli_path", "/usr/local/bin/rhwp")
    if not cfg.get("enabled") or not os.path.exists(cli):
        return ParsedDoc(text="", parser="skipped", ok=False,
                         error="rhwp 미설정/미배포 — hwp는 PDF 버전 사용 권장")
    fmt = cfg.get("format", "markdown")  # markdown(표보존) | text
    timeout = int(cfg.get("timeout_s", 120))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([cli, f"export-{fmt}", path, "-o", tmp],
                           check=True, capture_output=True, timeout=timeout)
            ext = ".md" if fmt == "markdown" else ".txt"
            files = sorted(f for f in os.listdir(tmp) if f.endswith(ext))
            parts = []
            for f in files:
                with open(os.path.join(tmp, f), encoding="utf-8", errors="replace") as fh:
                    parts.append(fh.read())
            text = "\n".join(parts)
        return ParsedDoc(text=_clean_lines(text), parser="rhwp", ok=bool(text.strip()))
    except Exception as e:  # noqa: BLE001
        return ParsedDoc(text="", parser="rhwp", ok=False, error=str(e))


# HWP PARA_TEXT 인라인/확장 컨트롤은 8 wchar(16바이트) 점유, 그 외 char 컨트롤은 1 wchar.
# 8 wchar 점유 컨트롤 코드(데이터 6 wchar가 임의값이라 통째로 건너뛰어야 본문 오염 없음).
_HWP_CTRL_8WCHAR = frozenset({1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23})


def _hwp_para_text(body: bytes) -> str:
    """HWPTAG_PARA_TEXT 본문(UTF-16LE wchar 스트림) → 문자열. 컨트롤 폭을 정확히 처리."""
    out = []
    i, n = 0, len(body) - (len(body) & 1)
    while i < n:
        wc = body[i] | (body[i + 1] << 8)
        if wc in (10, 13):          # 줄/문단 바꿈
            out.append("\n")
            i += 2
        elif wc < 32:               # 컨트롤
            i += 16 if wc in _HWP_CTRL_8WCHAR else 2
        else:
            out.append(chr(wc))
            i += 2
    return "".join(out)


def _extract_legacy_hwp(path: str):
    """구형 바이너리 HWP 5.x(OLE) → 텍스트. olefile(순수 Python)로 BodyText 스트림을
    zlib 해제 후 HWPTAG_PARA_TEXT(67) 레코드의 UTF-16LE 본문을 추출.

    olefile 미설치 시 None(→ 호출부가 rhwp 폴백). rhwp 불요로 구형 .hwp 자체 처리.
    """
    try:
        import struct
        import zlib

        import olefile  # 순수 Python, 컴파일 의존 없음
    except ImportError:
        return None
    try:
        if not olefile.isOleFile(path):
            return ParsedDoc(text="", parser="hwp-olefile", ok=False, error="OLE 형식 아님")
        ole = olefile.OleFileIO(path)
        try:
            hdr = ole.openstream("FileHeader").read()
            compressed = bool(hdr[36] & 1) if len(hdr) > 36 else True
            paras: list[str] = []
            for entry in ole.listdir():
                if not entry or not entry[0].startswith("BodyText"):
                    continue
                data = ole.openstream(entry).read()
                try:
                    raw = zlib.decompress(data, -15) if compressed else data
                except Exception:  # noqa: BLE001
                    continue
                i, n = 0, len(raw)
                while i + 4 <= n:
                    h = struct.unpack("<I", raw[i:i + 4])[0]
                    tag = h & 0x3FF
                    size = (h >> 20) & 0xFFF
                    i += 4
                    if size == 0xFFF:          # 확장 크기(다음 4바이트)
                        if i + 4 > n:
                            break
                        size = struct.unpack("<I", raw[i:i + 4])[0]
                        i += 4
                    body = raw[i:i + size]
                    i += size
                    if tag == 67:              # HWPTAG_PARA_TEXT
                        s = _hwp_para_text(body).strip()
                        if s:
                            paras.append(s)
        finally:
            ole.close()
        text = _clean_lines("\n".join(paras))
        return ParsedDoc(text=text, parser="hwp-olefile", ok=bool(text.strip()),
                         error=None if text.strip() else "빈 텍스트")
    except Exception as e:  # noqa: BLE001
        return ParsedDoc(text="", parser="hwp-olefile", ok=False, error=str(e))


def parse_document(path: str, base_dir: str | None = None) -> ParsedDoc:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _extract_pdf_text(path)
    if ext == ".hwpx":
        return _extract_hwpx(path)               # 순수 Python(OWPML XML ZIP)
    if ext in _LEGACY_HWP_EXT:
        pd = _extract_legacy_hwp(path)           # 순수 Python(olefile)
        if pd is not None and pd.ok:
            return pd
        rh = _extract_hancom_via_rhwp(path, base_dir)  # 폴백: rhwp CLI
        return rh if rh.ok else (pd or rh)
    if ext in (".txt", ".md"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            t = fh.read()
        return ParsedDoc(text=_clean_lines(t), parser="text", ok=bool(t.strip()))
    return ParsedDoc(text="", parser="skipped", ok=False, error=f"미지원 확장자 {ext}")


def chunk_notice(text: str, max_chars: int = 700, overlap: int = 80) -> list[str]:
    """조문/별표/부칙/장 경계 우선 분할, 과대/무경계는 슬라이딩."""
    if not text.strip():
        return []
    segs = [s.strip() for s in _BOUNDARY.split(text) if s.strip()]
    # 경계가 거의 없으면 전체를 슬라이딩
    if len(segs) <= 1:
        segs = [text]
    chunks: list[str] = []
    for seg in segs:
        if len(seg) <= max_chars:
            chunks.append(seg)
        else:
            i = 0
            while i < len(seg):
                chunks.append(seg[i:i + max_chars])
                i += max_chars - overlap
    return [c for c in chunks if len(c.strip()) >= 10]
