"""의료법 RAG 신규 모듈 회귀 테스트 (medical-law-rag P0/P1).

순수 단위 — 외부 의존(GPU/네트워크/ChromaDB) 0. 파싱·청킹·메타·필터·정규식 검증.
실행: venv/bin/python -m pytest tests/test_medlaw.py -q
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import doc_parser as dp  # noqa: E402
from core import medlaw_processor as mp  # noqa: E402
from core import khidi_collector as kc  # noqa: E402
from core import law_pdf_parser as lpp  # noqa: E402


# ── doc_parser: 청킹(조문/별표/부칙 경계) ──
def test_chunk_notice_boundary():
    text = ("보건복지부 고시 제2026-125호\n"
            "제1조(목적) 이 고시는 정한다.\n"
            "제2조(정의) 이 고시에서 말한다.\n"
            "별표 1 산정기준\n"
            "부 칙\n이 고시는 2026년 6월 22일부터 시행한다.")
    chunks = dp.chunk_notice(text)
    assert len(chunks) >= 4
    assert any("제1조" in c for c in chunks)
    assert any("부 칙" in c or "시행" in c for c in chunks)


def test_chunk_notice_sliding_for_unbounded():
    text = "가" * 1800  # 경계 없음 → 슬라이딩
    chunks = dp.chunk_notice(text, max_chars=700, overlap=80)
    assert len(chunks) >= 2
    assert all(len(c) <= 700 for c in chunks)


def test_chunk_notice_empty():
    assert dp.chunk_notice("") == []
    assert dp.chunk_notice("   \n  ") == []


# ── doc_parser: 고시 메타 추출(고시번호 공백허용 + 부칙 시행일) ──
def test_notice_metadata_docno_spaces():
    text = "보건복지부 고시 제2026 - 125호\n...본문...\n이 고시는 2026년 6월 22일부터 시행한다."
    m = dp.notice_metadata("/x/고시(제2026-125호).pdf", text)
    assert m["doc_no"] == "제2026-125호"
    assert m["effective_date"] == "2026.6.22"
    assert m["origin"] == "khidi_notice"
    assert m["law_level"] == "고시"


def test_notice_metadata_effect_colon_form():
    text = "고시 제2025-1호\n시행: 2025.1.31"
    m = dp.notice_metadata("/x/a.pdf", text)
    assert m["effective_date"] == "2025.1.31"


def test_notice_metadata_missing():
    m = dp.notice_metadata("/x/무번호.pdf", "본문만 있고 번호 없음")
    assert m["doc_no"] == "" and m["effective_date"] == ""


# ── 고시 category 분류(O5) ──
def test_classify_notice_category():
    assert dp.classify_notice_category("신의료기술의 안전성·유효성 평가결과 고시") == "신의료기술"
    assert dp.classify_notice_category("선별급여 지정 및 실시 등에 관한 기준") == "선별급여"
    assert dp.classify_notice_category("의료기기 허가·신고·심사 등에 관한 규정") == "의료기기"
    assert dp.classify_notice_category("치료재료 급여·비급여 목록 및 급여상한금액표") == "급여기준"
    assert dp.classify_notice_category("요양급여의 적용기준 및 방법에 관한 세부사항") == "심사기준"
    assert dp.classify_notice_category("그냥 안내문") == "기타"


def test_notice_metadata_has_category():
    m = dp.notice_metadata("/x/(제2026-1호)_선별급여_지정_및_실시_기준.pdf", "선별급여 ...")
    assert m["category"] == "선별급여"


# ── doc_parser: HWPX 순수 Python 추출(zip+xml) ──
def _make_hwpx(tmp_path, paras):
    p = os.path.join(tmp_path, "t.hwpx")
    with zipfile.ZipFile(p, "w") as z:
        body = "".join(
            f'<hp:p><hp:run><hp:t>{t}</hp:t></hp:run></hp:p>' for t in paras)
        z.writestr("Contents/section0.xml",
                   f'<?xml version="1.0"?><hml xmlns:hp="x">{body}</hml>')
    return p


def test_extract_hwpx(tmp_path):
    p = _make_hwpx(tmp_path, ["보건복지부 고시 제2026-1호", "제1조(목적)", "&lt;개정&gt;"])
    pd = dp.parse_document(p)
    assert pd.ok and pd.parser == "hwpx-py"
    assert "제1조(목적)" in pd.text
    assert "<개정>" in pd.text  # html.unescape 적용


def test_parse_document_unsupported(tmp_path):
    p = os.path.join(tmp_path, "x.xyz")
    open(p, "w").write("x")
    pd = dp.parse_document(p)
    assert not pd.ok and pd.parser == "skipped"


# ── medlaw_processor: 노이즈 필터 + 상태 + cid ──
def test_relevant_title_filter():
    assert mp.is_relevant_title("「선별급여 지정 및 실시 등에 관한 기준」 일부개정")
    assert not mp.is_relevant_title("검토의견 서식")
    assert not mp.is_relevant_title("붙임2 검토의견서 양식")


def test_state_roundtrip(tmp_path, monkeypatch):
    sp = os.path.join(tmp_path, "khidi_state.json")
    monkeypatch.setattr(mp, "_state_path", lambda: sp)
    assert mp._load_state() == {"last_no1": 0}  # 미존재 기본
    mp._save_state({"last_no1": 1574})
    assert mp._load_state()["last_no1"] == 1574


def test_cid_deterministic():
    a = mp._cid("file.pdf", 3)
    assert a == mp._cid("file.pdf", 3)
    assert a != mp._cid("file.pdf", 4)
    assert a.startswith("kn-")


# ── 버전 dedup: 문서 동일성 그룹핑 + titleId 파싱 ──
def test_base_key_groups_versions():
    """`_base_key`는 **더 이상 dedup 그룹핑에 쓰지 않는다**(2026-08-31).

    종전 동작(차수를 지워 같은 키로 묶음)은 그대로 보존한다 — 이 함수를 다시 그룹핑에
    쓰면 안 되는 이유가 이 동작 자체이기 때문이다(정본은 `_doc_identity`).
    """
    a = mp._base_key("529039-1-(26년3차)_신의료기술의_안전성_유효성_평가결과_고시_전문_발령.pdf")
    b = mp._base_key("527628-1-(26년2차)_신의료기술의_안전성_유효성_평가결과_고시_전문_발령.pdf")
    assert a == b and a
    c = mp._base_key("504300-1-(제2024-197호)_선별급여_지정_및_실시_등에_관한_기준_전문.hwpx")
    assert c != a


def test_tid_parse():
    assert mp._tid("529039-1-foo.pdf") == 529039
    assert mp._tid("nope.pdf") == 0
    # 최신 titleId가 더 큼(정렬 기준)
    assert mp._tid("529039-1-x.pdf") > mp._tid("527628-1-x.pdf")


# ── khidi_collector: 목록/첨부 정규식 파싱 ──
def test_view_regex():
    html = '<a href="/board/view?pageNum=1&rowCnt=20&no1=1574&linkId=48945706&menuId=MENU01516">제목</a>'
    m = kc._VIEW.search(html)
    assert m and m.group(1) == "1574" and m.group(2) == "48945706"


def test_attachment_regex():
    blk = ('<a href="/fileDownload?titleId=529116&fileId=1&fileDownType=C&paramMenuId=MENU01516">'
           "<img src='/resources/images/common/icon/icon_pdf.gif'/> "
           "건강보험_일부개정.pdf(168.85KB)</a>")
    m = kc._DL_ITEM.search(blk)
    assert m
    href, tid, fid, icon, fname, ext = m.groups()
    assert tid == "529116" and fid == "1" and icon == "pdf" and ext == "pdf"


def test_pdf_first_ordering():
    d = kc.KhidiDetail(no1="1", link_id="1", title="t", attachments=[
        kc.KhidiAttachment(url="u2", title_id="t", file_id="2", kind="hwp", filename="a.hwpx", ext="hwpx"),
        kc.KhidiAttachment(url="u1", title_id="t", file_id="1", kind="pdf", filename="a.pdf", ext="pdf"),
    ])
    assert d.pdf_first()[0].ext == "pdf"


# ── law_pdf_parser: 법령명 추론 + 청크 변환 ──
def test_infer_law_name():
    assert lpp._infer_law_name("/x/의료법(3단비교).pdf") == "의료법"
    assert "약사법" in lpp._infer_law_name("/x/약사법(3단비교).pdf")


def test_to_chunks_filters_short():
    doc = lpp.Law3DanDoc(law_name="의료법", source_file="/x/의료법.pdf")
    doc.articles = [
        lpp.LawArticle("법률", "제1조", "목적", "이 법은 국민 의료에 관한 사항을 규정한다."),
        lpp.LawArticle("법률", "제2조", "정의", "짧음"),  # <10자 → 제외
    ]
    chunks = lpp.to_chunks(doc)
    assert len(chunks) == 1
    assert chunks[0]["metadata"]["article_number"] == "제1조"
    assert chunks[0]["metadata"]["law_level"] == "법률"


# ── 노이즈필터 강화: 첨부파일명 판정 + 기색인 source_file 매칭 (v2.88.1) ──
def test_relevant_attachment_noise():
    # 라이브 관찰 노이즈 9건 패턴 — 전부 제외돼야 함
    for f in ("붙임3_검토의견서_양식.pdf", "붙임3_검토의견서(허가고시).pdf",
              "검토서_서식__신의료기술의_안전성_유효성_평가결과_고시_.hwpx",
              "검토의견서양식.hwpx", "검토의견_제출(양식).hwpx",
              "붙임2검토의견서-양식.hwpx", "검토의견_서식.hwpx",
              "서식.pdf", "양식_안내.hwpx", "붙임1_서식모음.pdf"):
        assert not mp.is_relevant_attachment(f), f


def test_relevant_attachment_legit():
    # '서식'이 중간에 포함돼도 본문 고시는 통과(오탐 방지)
    for f in ("요양급여비용_청구방법_심사청구서_명세서서식_및_작성요령_일부개정.pdf",
              "(제2026-103호)_요양급여의_적용기준_및_방법에_관한_세부사항_고시_일부개정.hwpx",
              "(붙임2)_만성질환_통합관리료(본사업_전환)_설명자료.pdf",
              "치료재료_급여비급여_목록_및_급여상한금액표_고시_일부개정.pdf"):
        assert mp.is_relevant_attachment(f), f


def test_strip_id_prefix():
    assert mp._strip_id_prefix("518062-1-붙임3_검토의견서_양식.pdf") == "붙임3_검토의견서_양식.pdf"
    assert mp._strip_id_prefix("no-prefix.pdf") == "no-prefix.pdf"
    assert mp._strip_id_prefix("") == ""


def test_noise_purge_key_on_indexed_names():
    # purge_noise의 판정 경로: source_file → prefix 제거 → 첨부 판정
    noise_sf = "522212-1-검토의견서양식.hwpx"
    legit_sf = "524877-1-요양급여비용_청구방법_심사청구서_명세서서식_및_작성요령_일부개정.pdf"
    assert not mp.is_relevant_attachment(mp._strip_id_prefix(noise_sf))
    assert mp.is_relevant_attachment(mp._strip_id_prefix(legit_sf))


# ── L4: top_k 입력 안전 클램프 (비정수/음수/None → 무예외) ──
def test_safe_top_k_clamps_and_no_raise():
    import pytest
    pytest.importorskip("requests")  # routes.medical_law는 requests 의존 (GPU venv서 실행)
    from routes.medical_law import _safe_top_k
    # 정상
    assert _safe_top_k("5") == 5
    assert _safe_top_k(7) == 7
    # 상한/하한 클램프
    assert _safe_top_k("15") == 10
    assert _safe_top_k("0") == 1
    assert _safe_top_k("-3") == 1
    # 비정수/None/빈값 → default (무예외, 과거 int()로 500 나던 경로)
    assert _safe_top_k("abc") == 5
    assert _safe_top_k(None) == 5
    assert _safe_top_k("") == 5
    assert _safe_top_k(5.9) == 5
    # default 인자
    assert _safe_top_k("xyz", default=6) == 6
