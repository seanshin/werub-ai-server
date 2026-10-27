"""KHIDI 법령정보 고시자료 게시판 수집기 (medical-law-rag P1).

게시판: https://www.khidi.or.kr/board?menuId=MENU01516&siteId=SITE00039 (정적 HTML·로그인 없음)
공공 법령정보(저작권 비보호). 서버 예의 준수: 단일 스레드 + 요청 간 지연 + 정직한 UA.
robots.txt /fileDownload는 크롤러 권고 — 정당한 목적·정중한 저빈도 수집으로 운용(운영 판단).

흐름: fetch_list_page(목록) → fetch_detail(상세=titleId+첨부) → download_attachment(파일).
파싱은 정규식(구조가 규칙적, bs4 의존 회피).
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field

BASE = "https://www.khidi.or.kr"
MENU_ID = "MENU01516"
SITE_ID = "SITE00039"
UA = "Mozilla/5.0 (WeRUB medical-law research; respectful crawler)"
DELAY_S = 1.5  # 요청 간 지연(서버 예의)
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024  # 첨부 다운로드 상한 80MB

_VIEW = re.compile(r'/board/view\?[^"\']*?no1=(\d+)&linkId=(\d+)')
# 상세 첨부: <a href="/fileDownload?titleId=..&fileId=..&fileDownType=C&paramMenuId=..">
#           <img src=..icon_pdf|icon_hwp..> (제..호)..파일명.ext(크기)
_DL_BLOCK = re.compile(r'<ul class="download_list">(.*?)</ul>', re.S)
_DL_ITEM = re.compile(
    r'href="(/fileDownload\?titleId=(\d+)&fileId=(\d+)[^"]*)"'
    r'.*?icon_(pdf|hwp)\.gif.*?>\s*([^<]*?\.(pdf|hwpx|hwp))\s*\(',
    re.S)
_TITLE = re.compile(r'<title>([^<]*)</title>')


@dataclass
class KhidiPost:
    no1: str
    link_id: str
    detail_url: str


@dataclass
class KhidiAttachment:
    url: str
    title_id: str
    file_id: str
    kind: str          # pdf | hwp
    filename: str
    ext: str


@dataclass
class KhidiDetail:
    no1: str
    link_id: str
    title: str
    attachments: list[KhidiAttachment] = field(default_factory=list)

    def pdf_first(self) -> list[KhidiAttachment]:
        """PDF 우선 정렬(같은 고시 PDF/HWPX 양본 중 PDF 선호 — rhwp 불필요)."""
        return sorted(self.attachments, key=lambda a: 0 if a.ext == "pdf" else 1)


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": f"{BASE}/board?menuId={MENU_ID}&siteId={SITE_ID}"})
    return s


def fetch_list_page(page: int, rows: int = 20, sess=None) -> list[KhidiPost]:
    sess = sess or _session()
    url = f"{BASE}/board?menuId={MENU_ID}&siteId={SITE_ID}&pageNum={page}&rowCnt={rows}"
    r = sess.get(url, timeout=20)
    r.raise_for_status()
    seen, posts = set(), []
    for m in _VIEW.finditer(r.text):
        no1, link_id = m.group(1), m.group(2)
        if no1 in seen:
            continue
        seen.add(no1)
        posts.append(KhidiPost(no1=no1, link_id=link_id,
                               detail_url=f"{BASE}/board/view?pageNum={page}&rowCnt={rows}"
                                          f"&no1={no1}&linkId={link_id}&menuId={MENU_ID}"))
    return posts


def fetch_detail(post: KhidiPost, sess=None) -> KhidiDetail:
    sess = sess or _session()
    r = sess.get(post.detail_url, timeout=20)
    r.raise_for_status()
    html = r.text
    tm = _TITLE.search(html)
    title = (tm.group(1).strip() if tm else "").replace("&lt;", "<").replace("&gt;", ">")
    atts = []
    blk = _DL_BLOCK.search(html)
    if blk:
        for m in _DL_ITEM.finditer(blk.group(1)):
            href, title_id, file_id, icon, fname, ext = m.groups()
            fname = urllib.parse.unquote(fname.replace("+", " ")).strip()
            atts.append(KhidiAttachment(url=BASE + href.replace("&amp;", "&"),
                                        title_id=title_id, file_id=file_id,
                                        kind=icon, filename=fname, ext=ext.lower()))
    return KhidiDetail(no1=post.no1, link_id=post.link_id, title=title, attachments=atts)


def safe_filename(att: KhidiAttachment) -> str:
    """첨부 저장 파일명 = `titleId-fileId-base.ext`(고유). 충돌 방지 + 증분 dedup 키.
    titleId가 안정적 식별자(no1=행번호는 불안정) → 증분 수집은 이 파일명으로 dedup."""
    base = re.sub(r"\.(pdf|hwpx|hwp)$", "", re.sub(r"[^\w.\-가-힣()]+", "_", att.filename), flags=re.I)[:90]
    return f"{att.title_id}-{att.file_id}-{base}.{att.ext}"


def download_attachment(att: KhidiAttachment, dest_dir: str, sess=None) -> str | None:
    """첨부 다운로드 → 저장 경로. 확장자 검증(pdf/hwpx/hwp만)."""
    if att.ext not in ("pdf", "hwpx", "hwp"):
        return None
    sess = sess or _session()
    os.makedirs(dest_dir, exist_ok=True)
    # 고유 접두(titleId-fileId)로 파일명 충돌 방지 — 긴 이름 절단 시 다른 고시가
    # 같은 prefix로 덮어쓰는 문제 차단. (백필 유실 버그 수정 2026-06-10)
    path = os.path.join(dest_dir, safe_filename(att))
    # 스트리밍 + 크기 상한(메모리 고갈/과대 첨부 방어)
    r = sess.get(att.url, timeout=60, stream=True)
    r.raise_for_status()
    buf, total = bytearray(), 0
    for chunk in r.iter_content(65536):
        total += len(chunk)
        if total > MAX_DOWNLOAD_BYTES:
            return None  # 과대 파일 거부
        buf.extend(chunk)
    if att.ext == "pdf" and bytes(buf[:4]) != b"%PDF":
        return None  # PDF 시그니처 불일치 → 손상/차단 의심
    with open(path, "wb") as f:
        f.write(buf)
    return path


def collect(max_pages: int = 1, start_page: int = 1, pdf_only: bool = True,
            dest_dir: str = "khidi_notices", limit: int | None = None,
            progress=None) -> list[str]:
    """게시판 walk → 첨부 다운로드. 정중한 지연. 다운로드된 파일 경로 리스트 반환."""
    sess = _session()
    saved, n = [], 0
    for page in range(start_page, start_page + max_pages):
        posts = fetch_list_page(page, sess=sess)
        for post in posts:
            if limit and n >= limit:
                return saved
            time.sleep(DELAY_S)
            det = fetch_detail(post, sess=sess)
            atts = det.pdf_first()
            if pdf_only:
                pdfs = [a for a in atts if a.ext == "pdf"]
                atts = pdfs or atts[:1]  # PDF 없으면 첫 첨부(hwpx)만
            else:
                atts = atts[:1]
            for att in atts[:1]:  # 고시당 1개(PDF 우선)
                time.sleep(DELAY_S)
                p = download_attachment(att, dest_dir, sess=sess)
                if p:
                    saved.append(p)
                    n += 1
                    if progress:
                        progress(post.no1, det.title, att.ext, p)
        time.sleep(DELAY_S)
    return saved
