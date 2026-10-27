"""core.dur 순수 단위테스트 (인메모리 SQLite, xls/GPU 불요).

병용금기 적재·약물 해석(코드/성분명/품목)·쌍 점검·프로파일·stats 검증.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import dur  # noqa: E402


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(dur._DDL)
    c.commit()
    return c


def _seed(c):
    inter = [
        {"a_code": "D000353", "a_name": "파록세틴", "a_name_en": "Paroxetine",
         "b_code": "D000139", "b_name": "셀레길린염산염", "b_name_en": "Selegiline",
         "reason": "세로토닌성 증후군", "grade": "", "notice_date": "20090303", "dur_no": "23"},
        {"a_code": "D000455", "a_name": "아토르바스타틴", "a_name_en": "Atorvastatin",
         "b_code": "D000769", "b_name": "케토코나졸", "b_name_en": "Ketoconazole",
         "reason": "근육병증", "grade": "", "notice_date": "20090303", "dur_no": "29"},
    ]
    dur.import_interactions(inter, conn=c)
    prods = [
        {"product_serial": "P1", "product_name": "팍실정", "ingr_code": "D000353",
         "ingredient": "파록세틴염산염", "maker": "갑제약"},
        {"product_serial": "P1", "product_name": "팍실정", "ingr_code": "D000353",
         "ingredient": "파록세틴염산염", "maker": "갑제약"},  # dup serial → 1건
        {"product_serial": "P2", "product_name": "마오비정", "ingr_code": "D000139",
         "ingredient": "셀레길린염산염", "maker": "을제약"},
    ]
    dur.import_products(prods, conn=c)


def test_import_and_stats():
    c = _conn()
    _seed(c)
    s = dur.stats(conn=c)
    assert s["interactions"] == 2
    assert s["products"] == 2           # dup serial dedup
    assert s["distinct_ingredients"] == 2


def test_check_detects_contraindication_by_name():
    c = _conn()
    _seed(c)
    r = dur.check(["파록세틴", "셀레길린염산염", "아세트아미노펜"], conn=c)
    assert r["has_contraindication"] is True
    assert r["count"] == 1
    a = r["contraindications"][0]
    assert {a["drug_a"], a["drug_b"]} == {"파록세틴", "셀레길린염산염"}
    assert "세로토닌" in a["reason"]


def test_check_no_contraindication():
    c = _conn()
    _seed(c)
    r = dur.check(["파록세틴", "케토코나졸"], conn=c)  # 무관 조합
    assert r["has_contraindication"] is False and r["count"] == 0


def test_check_resolves_by_ingredient_code_and_product():
    c = _conn()
    _seed(c)
    # D000 코드 + 품목일련번호로 식별 → 동일 병용금기 탐지
    r = dur.check([{"ingredient_code": "D000353"}, {"product_serial": "P2"}], conn=c)
    assert r["count"] == 1
    assert r["resolved"][0]["via"] == "ingredient_code"
    assert r["resolved"][1]["via"] == "product_serial"


def test_check_symmetric():
    # 입력 순서 무관(양방향 매칭)
    c = _conn()
    _seed(c)
    assert dur.check(["셀레길린염산염", "파록세틴"], conn=c)["count"] == 1


def test_check_dedup_pair():
    # 같은 성분이 여러 품목명으로 들어와도 쌍 1회만
    c = _conn()
    _seed(c)
    r = dur.check([{"product_name": "팍실정"}, {"product_name": "마오비정"}], conn=c)
    assert r["count"] == 1


def test_drug_profile():
    c = _conn()
    _seed(c)
    p = dur.drug_profile("파록세틴", conn=c)
    assert p["codes"] == ["D000353"]
    assert p["contraindicated_count"] == 1
    assert p["contraindicated_with"][0]["name"] == "셀레길린염산염"


def test_resolve_unknown():
    c = _conn()
    _seed(c)
    r = dur.check(["존재하지않는약"], conn=c)
    assert r["resolved"][0]["resolved"] is False
    assert r["count"] == 0


def test_d000_pattern_string_input():
    c = _conn()
    _seed(c)
    # 문자열이 D000 패턴이면 성분코드로 직접 해석
    r = dur.check(["D000353", "D000139"], conn=c)
    assert r["count"] == 1 and r["resolved"][0]["via"] == "ingredient_code"


# ── 단일성분 금기/주의 (API dur_single) ──
def test_age_applies_parsing():
    assert dur._age_applies(10, "12세 미만") is True
    assert dur._age_applies(12, "12세 미만") is False
    assert dur._age_applies(18, "18세 이하") is True
    assert dur._age_applies(20, "18세 이하") is False
    assert dur._age_applies(70, "65세 이상") is True
    assert dur._age_applies(None, "12세 미만") is False
    assert dur._age_applies(5, "알수없는기준") is True   # 미파싱 = 보수적 포함


def _seed_single(c):
    dur.import_single([
        {"DUR_SEQ": "1", "INGR_CODE": "D000353", "INGR_NAME": "파록세틴",
         "PROHBT_CONTENT": "기형유발", "GRADE": "2"}], "임부금기", conn=c)
    dur.import_single([
        {"DUR_SEQ": "2", "INGR_CODE": "D000147", "INGR_NAME": "아세트아미노펜",
         "PROHBT_CONTENT": "소아주의", "AGE_BASE": "12세 미만"}], "연령금기", conn=c)
    dur.import_single([
        {"DUR_SEQ": "3", "INGR_CODE": "D000353", "INGR_NAME": "파록세틴",
         "PROHBT_CONTENT": "노인 이상반응"}], "노인주의", conn=c)
    dur.import_single([
        {"DUR_SEQ": "4", "INGR_CODE": "D000353", "INGR_NAME": "파록세틴",
         "EFFECT_CODE": "E1", "SERS_NAME": "SSRI"},
        {"DUR_SEQ": "5", "INGR_CODE": "D000139", "INGR_NAME": "셀레길린염산염",
         "EFFECT_CODE": "E1", "SERS_NAME": "SSRI"}], "효능군중복", conn=c)


def test_pregnancy_warning():
    c = _conn(); _seed(c); _seed_single(c)
    r = dur.check(["파록세틴"], conn=c, pregnant=True)
    assert len(r["pregnancy_warnings"]) == 1
    # 임신 여부 미제공 시 점검 안 함
    assert dur.check(["파록세틴"], conn=c)["pregnancy_warnings"] == []


def test_elderly_and_age_warning():
    c = _conn(); _seed(c); _seed_single(c)
    r = dur.check(["파록세틴", "아세트아미노펜"], conn=c, age=70)
    assert len(r["elderly_warnings"]) == 1           # 파록세틴 노인주의
    r2 = dur.check(["아세트아미노펜"], conn=c, age=5)
    assert len(r2["age_warnings"]) == 1              # 12세 미만 해당
    assert dur.check(["아세트아미노펜"], conn=c, age=20)["age_warnings"] == []


def test_duplicate_therapy():
    c = _conn(); _seed(c); _seed_single(c)
    r = dur.check(["파록세틴", "셀레길린염산염"], conn=c)
    assert len(r["duplicate_therapy"]) == 1          # 같은 효능군 E1
    assert r["duplicate_therapy"][0]["sers_name"] == "SSRI"


def test_split_caution():
    # 서방정분할주의(품목 단위) — 제품명/품목코드 매칭
    c = _conn()
    dur.import_split([
        {"ITEM_SEQ": "197100081", "ITEM_NAME": "키모랄에스정", "MAIN_INGR": "...",
         "PROHBT_CONTENT": "분할불가", "ENTP_NAME": "에이프로젠"}], conn=c)
    r = dur.check(["키모랄에스정"], conn=c)
    assert len(r["split_cautions"]) == 1
    assert r["split_cautions"][0]["content"] == "분할불가"
    assert r["has_warning"] is True
    # 품목일련번호로도 매칭
    assert len(dur.check(["197100081"], conn=c)["split_cautions"]) == 1
    # 무관 약은 없음
    assert dur.check(["타이레놀"], conn=c)["split_cautions"] == []


def _seed_split_aspirin(c):
    dur.import_split([
        {"ITEM_SEQ": "200000001", "ITEM_NAME": "아스피린서방정100밀리그램",
         "MAIN_INGR": "아스피린", "PROHBT_CONTENT": "분할불가", "ENTP_NAME": "바이엘"},
        {"ITEM_SEQ": "200000002", "ITEM_NAME": "콘서타",  # SR 토큰 없는 브랜드명(exact 전용)
         "MAIN_INGR": "메칠페니데이트", "PROHBT_CONTENT": "분할불가", "ENTP_NAME": "한국얀센"}], conn=c)


def test_split_caution_no_overmatch_for_bare_ingredient():
    # 짧은 성분명("아스피린")은 서방정 제형 미명시 → LIKE 과매칭 안 함(false positive 방지)
    c = _conn(); _seed_split_aspirin(c)
    assert dur.check(["아스피린"], conn=c)["split_cautions"] == []
    assert dur.check(["아스피린장용정"], conn=c)["split_cautions"] == []  # 서방정 아님


def test_split_caution_matches_when_sr_form_specified():
    # 서방정 제형을 명시하면 부분매칭 유지(서방/SR/OROS 등)
    c = _conn(); _seed_split_aspirin(c)
    assert len(dur.check(["아스피린서방"], conn=c)["split_cautions"]) == 1
    assert len(dur.check(["아스피린서방정"], conn=c)["split_cautions"]) == 1
    # 정확한 품목명은 게이트 무관하게 exact 매칭(SR 토큰 없는 브랜드도)
    assert len(dur.check(["콘서타"], conn=c)["split_cautions"]) == 1


def test_looks_like_sr_markers():
    assert dur._looks_like_sr("아스피린서방정") is True
    assert dur._looks_like_sr("콘서타OROS") is True
    assert dur._looks_like_sr("니페디핀CR정") is True
    assert dur._looks_like_sr("아스피린") is False
    assert dur._looks_like_sr("타이레놀정") is False


def test_import_interactions_api():
    c = _conn()
    n = dur.import_interactions_api([
        {"INGR_CODE": "D000353", "INGR_NAME": "파록세틴", "INGR_ENG_NAME": "Paroxetine",
         "MIXTURE_INGR_CODE": "D000139", "MIXTURE_INGR_KOR_NAME": "셀레길린염산염",
         "PROHBT_CONTENT": "세로토닌 증후군", "DUR_SEQ": "23"}], conn=c)
    assert n == 1
    assert dur.check(["파록세틴", "셀레길린염산염"], conn=c)["count"] == 1


# ── salt-명 관대 해석 회귀 (일반명 병용금기 누락 버그 fix) ──────────────────

def test_strip_salt():
    assert dur._strip_salt("셀레길린염산염") == "셀레길린"
    assert dur._strip_salt("암로디핀베실산염") == "암로디핀"
    assert dur._strip_salt("에스오메프라졸수화물") == "에스오메프라졸"
    assert dur._strip_salt("파록세틴") == "파록세틴"   # 기본형 그대로
    assert dur._strip_salt("") == ""


def _seed_realistic(c):
    """실제 식약처 병용금기 구조 재현: a측 INGR_NAME 빈값(코드만)→a_name='',
    b측만 salt 정식명(셀레길린염산염). a측 성분은 dur_single에 일반명으로 존재."""
    dur.import_interactions([
        {"a_code": "D000353", "a_name": "", "a_name_en": "",
         "b_code": "D000139", "b_name": "셀레길린염산염", "b_name_en": "",
         "reason": "세로토닌성 증후군", "grade": "", "notice_date": "20090303", "dur_no": "23"},
    ], conn=c)
    dur.import_single([
        {"INGR_CODE": "D000353", "DUR_SEQ": "1", "INGR_NAME": "파록세틴",
         "PROHBT_CONTENT": "1일 최대 용량 주의"},
    ], "용량주의", conn=c)


def test_check_common_name_when_aname_empty():
    """🐞fix: a_name 빈값 + b_name salt명이라 일반명('셀레길린')이 미검출되던 버그."""
    c = _conn()
    _seed_realistic(c)
    r = dur.check(["파록세틴", "셀레길린"], conn=c)   # 일반명(salt 없음)
    assert r["count"] == 1, r
    assert "세로토닌" in r["contraindications"][0]["reason"]


def test_check_common_name_symmetric_empty_aname():
    c = _conn()
    _seed_realistic(c)
    assert dur.check(["셀레길린", "파록세틴"], conn=c)["count"] == 1


def test_salt_like_no_overmatch_short():
    """3자 미만/미매칭 성분은 접두 LIKE 과매칭 유발 안 함(false positive 방지)."""
    c = _conn()
    _seed_realistic(c)
    assert dur.check(["파록세틴", "XY"], conn=c)["count"] == 0
    # 무관 일반명도 미검출
    assert dur.check(["파록세틴", "메트포르민"], conn=c)["count"] == 0


# ── a_name 백필 하드닝 (a측 한글명 빈값 데이터 레벨 봉합) ────────────────────

def test_backfill_a_names_from_crossref():
    c = _conn()
    dur.import_interactions([
        {"a_code": "D000353", "a_name": "", "b_code": "D000139", "b_name": "셀레길린염산염",
         "reason": "세로토닌", "grade": "", "notice_date": "", "dur_no": "1"},
        {"a_code": "D000139", "a_name": "", "b_code": "D000999", "b_name": "리네졸리드",
         "reason": "MAOI", "grade": "", "notice_date": "", "dur_no": "2"},
    ], conn=c)
    filled = dur.backfill_a_names(conn=c)
    assert filled >= 1
    # D000139의 a_name이 b_name(셀레길린염산염)→salt제거→셀레길린으로 채워짐
    row = c.execute("SELECT a_name FROM dur_interaction WHERE a_code='D000139'").fetchone()
    assert row[0] == "셀레길린"


def test_backfill_from_product_adds_coverage():
    """이전 fix(b_name/dur_single)로도 못 잡던 a측-only(dur_product 전용) 성분을 백필이 커버."""
    c = _conn()
    dur.import_interactions([
        {"a_code": "D000500", "a_name": "", "b_code": "D000139", "b_name": "셀레길린염산염",
         "reason": "X", "grade": "", "notice_date": "", "dur_no": "1"},
    ], conn=c)
    dur.import_products([
        {"product_serial": "P9", "product_name": "테스트정", "ingr_code": "D000500",
         "ingredient": "이소카르복사지드", "maker": "z"}], conn=c)
    # 백필 전: 이소카르복사지드는 b_name/dur_single에 없어 해석 불가 → 미검출
    assert dur.check(["이소카르복사지드", "셀레길린"], conn=c)["count"] == 0
    dur.backfill_a_names(conn=c)
    # 백필 후: a_name 채워져 일반명 해석 가능 → 병용금기 검출
    assert dur.check(["이소카르복사지드", "셀레길린"], conn=c)["count"] == 1
