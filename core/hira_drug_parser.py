"""HIRA 「약제급여목록 및 급여상한금액표」(xlsx) 파서 — fee_codes(DRUG) 적재용.

순수 표준 라이브러리(zipfile + ElementTree 스트리밍)로 xlsx를 읽어
제품코드 → DRUG → notice_type 해석용 fee_rows를 생성한다(openpyxl 불요).

대량(~22K 품목) 코드표이므로 **RAG 색인은 하지 않는다**:
구조적 lookup은 fee_codes로 충분하고, 대형 ChromaDB 컬렉션의 HNSW link_lists
폭주(2026-06-11 인시던트)를 회피한다. 가격·주성분 등 상세는 name 필드에 패킹.

ERP 합의 매핑: DRUG(약가) → [DRUG_MATERIAL, REVIEW_CRITERIA] (core/fee_codes.py).

  venv/bin/python scripts/ingest_hira_drug.py ~/drug_list.xlsx --dry-run
  venv/bin/python scripts/ingest_hira_drug.py ~/drug_list.xlsx
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# 헤더 텍스트 → 표준 필드명. 개행/공백은 정규화 후 매칭(컬럼 순서·줄바꿈 변화에 강건).
_HEADER_MAP = {
    "제품코드": "code",
    "제품명": "product",
    "주성분명": "ingredient",
    "규격": "spec",
    "단위": "unit",
    "상한금액표금액": "price",
    "상한금액": "price",
    "급여상한금액": "price",
    "업체명": "maker",
    "전일": "rx",          # 전문/일반 구분
    "투여": "route",
    "비고": "note",
}


def _norm_header(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).strip()


@dataclass
class DrugDoc:
    source_file: str = ""
    edition: str = ""
    records: list = field(default_factory=list)  # [dict(code, product, ...)]

    @property
    def count(self) -> int:
        return len(self.records)


def _colnum(ref: str) -> int:
    """셀 주소(예 'AB12')의 열 부분을 1-기반 정수로."""
    m = re.match(r"[A-Z]+", ref or "")
    if not m:
        return 0
    n = 0
    for ch in m.group(0):
        n = n * 26 + (ord(ch) - 64)
    return n


def _load_shared_strings(z: zipfile.ZipFile) -> list:
    try:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root.findall(_NS + "si"):
        out.append("".join(t.text or "" for t in si.iter(_NS + "t")))
    return out


_RELNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKGREL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _sheet_paths(z: zipfile.ZipFile) -> list:
    """워크북 정의 순서대로 워크시트 xml 경로 목록(r:id→rels 해석, 실패 시 sheetN.xml 폴백)."""
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_tgt = {r.get("Id"): r.get("Target") for r in rels.findall(_PKGREL + "Relationship")}
        paths = []
        for s in wb.find(_NS + "sheets").findall(_NS + "sheet"):
            tgt = rid_to_tgt.get(s.get(_RELNS + "id"))
            if tgt:
                tgt = tgt.lstrip("/")
                paths.append(tgt if tgt.startswith("xl/") else "xl/" + tgt)
        if paths:
            return paths
    except Exception:  # noqa: BLE001
        pass
    # 폴백: 존재하는 sheetN.xml 순서
    return sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))


def _sheet_name(z: zipfile.ZipFile, index: int) -> str:
    try:
        wb = z.read("xl/workbook.xml").decode("utf-8", "ignore")
        names = re.findall(r'<sheet [^>]*name="([^"]+)"', wb)
        return names[index] if index < len(names) else ""
    except Exception:  # noqa: BLE001
        return ""


def iter_xlsx_records(path: str, header_map: dict, sheet_index: int = 0,
                      required_field: str = "code") -> tuple:
    """xlsx → (records, edition, source_file). header_map={헤더텍스트: 필드}(정규화 매칭).

    헤더 행을 **스캔**해 `required_field`가 매핑되는 첫 행을 헤더로 잡는다(제목·안내가 위에
    있어 헤더가 첫 행이 아닌 양식도 처리). `required_field` 비어있는 데이터 행은 제외.
    """
    z = zipfile.ZipFile(path)
    shared = _load_shared_strings(z)
    paths = _sheet_paths(z)
    if sheet_index >= len(paths):
        raise ValueError(f"시트 인덱스 {sheet_index} 없음(시트 {len(paths)}개)")
    hmap = {_norm_header(k): v for k, v in header_map.items()}
    colmap: dict = {}
    records = []
    for _ev, el in ET.iterparse(io.BytesIO(z.read(paths[sheet_index])), events=("end",)):
        if el.tag != _NS + "row":
            continue
        cells = {}
        for c in el.findall(_NS + "c"):
            cn = _colnum(c.get("r") or "")
            if cn:
                cells[cn] = _cell_value(c, shared)
        el.clear()
        if not colmap:
            cand = {cn: hmap[_norm_header(t)] for cn, t in cells.items()
                    if _norm_header(t) in hmap}
            if required_field in cand.values():
                colmap = cand
            continue
        rec = row_to_record(cells, colmap)
        if (rec.get(required_field) or "").strip():
            records.append(rec)
    if not colmap:
        raise ValueError(f"'{required_field}' 헤더를 찾지 못함")
    import os
    return records, _edition_from(os.path.basename(path), _sheet_name(z, sheet_index)), os.path.basename(path)


def _cell_value(c, shared: list) -> str:
    """셀 → 문자열. shared string(t='s')·inline(t='inlineStr')·raw 모두 처리."""
    t = c.get("t")
    if t == "inlineStr":
        return "".join(x.text or "" for x in c.iter(_NS + "t")).strip()
    v = c.find(_NS + "v")
    if v is None or v.text is None:
        return ""
    val = v.text
    if t == "s":
        try:
            return (shared[int(val)] or "").strip()
        except (ValueError, IndexError):
            return ""
    return val.strip()


def row_to_record(cells: dict, colmap: dict) -> dict:
    """{colnum: text} + {colnum: field} → 표준 레코드 dict(순수 함수, 테스트 대상)."""
    rec = {}
    for cn, fld in colmap.items():
        rec[fld] = cells.get(cn, "")
    return rec


def compose_name(rec: dict) -> str:
    """레코드 → fee_codes.name(상세 패킹). 빈 필드는 생략(순수 함수, 테스트 대상)."""
    parts = []
    if rec.get("product"):
        parts.append(rec["product"])
    if rec.get("ingredient"):
        parts.append(rec["ingredient"])
    spec = (rec.get("spec") or "").strip()
    unit = (rec.get("unit") or "").strip()
    if spec or unit:
        parts.append((spec + unit).strip())
    if rec.get("maker"):
        parts.append(rec["maker"])
    price = (rec.get("price") or "").strip()
    if price:
        # 숫자면 '상한 N원', '산정불가' 등 비숫자는 라벨 그대로.
        parts.append(f"상한 {price}원" if price.replace(",", "").isdigit() else f"상한 {price}")
    if rec.get("rx"):
        parts.append(rec["rx"])
    return " | ".join(p for p in parts if p)


def _edition_from(source_file: str, sheet_name: str) -> str:
    """파일명/시트명에서 'YYYY-MM-DD' 판본 추정(실패 시 '')."""
    for s in (sheet_name, source_file):
        m = re.search(r"(\d{4})[._년\s]+(\d{1,2})[._월\s]+(\d{1,2})", s or "")
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return ""


def parse_drug_xlsx(path: str) -> DrugDoc:
    """xlsx 약제급여목록 → DrugDoc(records). 헤더 행 스캔 후 데이터 추출(스트리밍)."""
    records, edition, src = iter_xlsx_records(path, _HEADER_MAP, sheet_index=0)
    return DrugDoc(source_file=src, edition=edition, records=records)


def fee_rows(doc: DrugDoc, category: str = "DRUG") -> list:
    """DrugDoc → fee_codes 적재용 rows([{code, category, name}]). 멱등 적재 가능."""
    rows = []
    for rec in doc.records:
        code = (rec.get("code") or "").strip()
        if not code:
            continue
        rows.append({"code": code, "category": category, "name": compose_name(rec)})
    return rows
