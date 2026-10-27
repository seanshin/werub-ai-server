"""medlaw dedup 그룹핑 — **라이브 코퍼스 16그룹을 라벨 통제군으로** 고정한다.

배경(2026-08-31 실측). 대량 삭제 가드가 2026-06-12부터 17회 SKIP하며 삭제 대상이
2790→3017청크로 늘어 있었고, 로그는 "워치독 동반 수동 dedup 요망"이라 **삭제를 권하고**
있었다. 착수해서 탐지기를 먼저 통제군에 걸었더니 **전제가 틀렸다**:

  `_base_key`가 `전문|일부개정|개정|제정`과 고시번호·차수를 전부 지운 뒤 묶었기 때문에
  성격이 다른 문서가 한 그룹이 됐고, 정렬 1순위가 청크 수라 **가장 긴 것(대개 구버전 전문)을
  남기고 최신 일부개정을 지우는** 동작이었다. 16그룹 중 **9그룹**에서 보존 대상이 최신본과
  불일치했다. 검색 실측(N=10·top-5)에서 삭제 대상이 근거의 **32%(16/50)**를 차지했고,
  그중 다수가 2026년 최신 개정분이었다(예: 선별급여 질의 top-5에서 2026-102·122·154호가
  전부 삭제 대상이고 보존 대상은 2025-145호였다).

⇒ **가드가 80일간 코퍼스를 지켜온 것**이고, 로그의 권고를 따랐으면 최신 개정분이 사라졌다.

재설계된 규칙(이 파일이 고정한다):
  ① `_doc_identity`는 **색인 부수 정보만** 지운다(titleId 접두사·확장자·구분기호).
     성격 마커·고시번호·차수·날짜는 남긴다 — 다르면 다른 문서다.
  ② 삭제 후보는 `_doc_identity`가 같고 **청크 수까지 같은** 것뿐(같은 문서 두 번 색인).
     청크 수가 다르면 `ambiguous`로 빼둔다(파싱본 차이·동명 다른 개정본).
  ③ 보존은 **최신 titleId**(청크 수가 아니다).
  ④ `apply=False`가 기본 — 스케줄러 경로에서 자동 삭제가 일어나지 않는다.
  ⑤ 구버전 전문은 **보존**한다(정책 결정 2026-08-31: 과거 시점 기준 질의 대비).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from core import medlaw_processor as mp


# ── 라벨 통제군: 라이브 코퍼스에서 실제로 관측된 파일명 ──────────────────────
# 🔴 = 종전 로직이 삭제하려던 최신 개정분(지우면 안 되는 것)
SAME_REGULATION_DIFFERENT_DOCS = [
    # (a, b, 왜 다른 문서인가)
    ("520559-1-의료급여수가의_기준_및_일반기준(고시_제2025-171호_25.10.1._시행)_전문.pdf",
     "530072-1-(고시_제2026-138호)_의료급여수가의_기준_및_일반기준_일부개정.pdf",
     "전문(통합본) vs 일부개정(개정 diff) — 상보 관계 🔴"),
    ("509516-1-(제2024-280호)_건강보험_행위_급여_비급여_목록표_및_급여상대가치점수_.hwpx",
     "532082-1-(제2026-173호_8.24.)_건강보험_행위_급여_비급여_목록표_및_급여_상대가치점수_일부개정.pdf",
     "목록표 vs 2026 일부개정 🔴"),
    ("519307-2-(제2025-145호_2025.08.27.)_선별급여_지정_및_실시_등에_관한_기준_고시_일부개정.pdf",
     "529042-2-(제2026-122호_2026.5.29.)_선별급여_지정_및_실시_등에_관한_기준_고시_일부개정.pdf",
     "고시번호가 다른 별개 개정 🔴"),
    ("505646-2-(제2024-218호_2024.10.28)_요양급여의_적용기준_및_방법에_관한_세부사항_일부개정.pdf",
     "524367-1-(제2026-29호_1.30.)_요양급여의_적용기준_및_방법에_관한_세부사항_일부개정.pdf",
     "고시번호가 다른 별개 개정 🔴"),
    ("526862-1-(제2026-94호_4.23.)_치료재료_급여비급여_목록_및_급여상한금액표_일부개정(안).pdf",
     "528852-1-(제2026-118호_5.27.)_치료재료_급여비급여_목록_및_급여상한금액표_일부개정(안).pdf",
     "118호가 94호보다 최신인데 종전 로직은 118호를 지웠다 🔴"),
    # 구버전 전문 — 정책상 보존(같은 규정이지만 다른 차수)
    ("529039-1-(26년3차)_신의료기술의_안전성_유효성_평가결과_고시_전문_발령.pdf",
     "527628-1-(26년2차)_신의료기술의_안전성_유효성_평가결과_고시_전문_발령.pdf",
     "차수가 다른 전문 — 구버전 보존 정책"),
    ("531529-1-(2026년_5차)_평가_유예_신의료기술_고시_전문(제2026-171호).pdf",
     "521496-1-_(25년7차)_평가_유예_신의료기술_고시_전문_발령.hwpx",
     "차수가 다른 전문 — 구버전 보존 정책"),
    ("532109-2-혁신의료기기_지정_현황(0825).pdf",
     "530775-1-혁신의료기기_지정_현황(0716).pdf",
     "날짜 스냅샷이 다름 — 구버전 보존 정책"),
]

TRUE_DUPLICATES = [
    # (a, b, 왜 같은 문서인가) — titleId만 다르고 나머지가 완전히 같다
    ("524875-1-(참고)_일부개정_고시_별표1_변경사항_표기_.pdf",
     "524764-1-(참고)_일부개정_고시_별표1_변경사항_표기_.pdf",
     "동일 파일명·동일 청크수(119)"),
    ("525746-1-의료기기법_시행령_일부개정령안_입법예고문(제2026-132호).hwpx",
     "525543-1-의료기기법_시행령_일부개정령안_입법예고문(제2026-132호).hwpx",
     "동일 고시번호·동일 청크수(9)"),
    ("525743-2-_의료평가통합정보시스템의_구축_운영_등에_관한_세부사항_고시_제정.pdf",
     "525542-1-_의료평가통합정보시스템의_구축_운영_등에_관한_세부사항_고시_제정.pdf",
     "동일 파일명·동일 청크수(8)"),
    ("531181-1-(제2026-154호_7.27.)_선별급여_지정_및_실시_등에_관한_기준_일부개정.pdf",
     "531038-2-(제2026-154호_7.27.)_선별급여_지정_및_실시_등에_관한_기준_일부개정.pdf",
     "동일 고시번호(154호)·동일 청크수(3)"),
]


@pytest.mark.parametrize("a,b,why", SAME_REGULATION_DIFFERENT_DOCS,
                         ids=[w[:28] for _a, _b, w in SAME_REGULATION_DIFFERENT_DOCS])
def test_different_documents_are_never_grouped(a, b, why):
    """🔴 이게 깨지면 최신 개정분·구버전 전문이 삭제 계획에 들어간다."""
    assert mp._doc_identity(a) != mp._doc_identity(b), why


@pytest.mark.parametrize("a,b,why", TRUE_DUPLICATES,
                         ids=[w[:28] for _a, _b, w in TRUE_DUPLICATES])
def test_true_duplicates_are_grouped(a, b, why):
    """음성 통제 — 너무 좁혀서 진짜 중복까지 못 잡으면 그것도 결함이다."""
    assert mp._doc_identity(a) == mp._doc_identity(b), why


def test_identity_ignores_format_only():
    """hwpx/pdf 같은 문서는 같은 identity(단 청크 수가 다르면 계획에서 빠진다 — 아래)."""
    assert (mp._doc_identity("522928-2-디지털의료제품법에_따른_기관_지정_등에_관한_규정_일부개정고시(안)_최종.hwpx")
            == mp._doc_identity("522915-1-디지털의료제품법에_따른_기관_지정_등에_관한_규정_일부개정고시(안)_최종.pdf"))


# ── dedup_versions 동작 ─────────────────────────────────────────────────────
class _Col:
    def __init__(self, metas):
        self._metas = metas
        self.deleted = []
        self._n = len(metas)

    def get(self, include=None):
        return {"metadatas": self._metas}

    def delete(self, where=None):
        self.deleted.append(where)

    def count(self):
        return self._n


@pytest.fixture
def patch_col(monkeypatch, tmp_path):
    def _factory(metas):
        col = _Col(metas)
        import client.rag as rag
        monkeypatch.setattr(rag, "get_collection", lambda *a, **k: col)
        monkeypatch.setattr(mp, "_link_lists_warn", lambda: 0, raising=False)
        # apply=True가 tombstone을 기록하므로(v2.125.35) 실제 khidi_state.json을
        # 오염시키지 않게 state 경로를 격리한다.
        monkeypatch.setattr(mp, "_state_path", lambda: str(tmp_path / "state.json"))
        return col
    return _factory


def _metas(spec):
    """spec: [(source_file, chunks), ...]"""
    out = []
    for sf, n in spec:
        out += [{"origin": "khidi_notice", "source_file": sf} for _ in range(n)]
    return out


def test_dry_run_is_the_default(patch_col):
    """스케줄러는 인자 없이 부른다 — 라이브에서 자동 삭제가 일어나면 안 된다."""
    col = patch_col(_metas([("200-1-foo.pdf", 5), ("100-1-foo.pdf", 5)]))
    r = mp.dedup_versions()
    assert r["dry_run"] is True
    assert r["planned_files"] == 1 and r["planned_chunks"] == 5
    assert r["deduped_chunks"] == 0
    assert col.deleted == [], "dry-run인데 col.delete가 불렸다"


def test_apply_deletes_older_titleid_not_smaller_chunks(patch_col, monkeypatch):
    """보존은 최신 titleId다 — 종전처럼 '더 긴 쪽'이 아니다."""
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    col = patch_col(_metas([("200-1-foo.pdf", 5), ("100-1-foo.pdf", 5)]))
    r = mp.dedup_versions(apply=True)
    assert r["deduped_files"] == 1 and r["deduped_chunks"] == 5
    assert col.deleted == [{"source_file": "100-1-foo.pdf"}], "낡은 titleId를 지워야 한다"


def test_differing_chunk_counts_are_held_not_deleted(patch_col, monkeypatch):
    """동명이지만 청크 수가 다르면 다른 파싱본/다른 개정본 — 계획에서 뺀다."""
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    col = patch_col(_metas([("200-1-foo.pdf", 9), ("100-1-foo.pdf", 3)]))
    r = mp.dedup_versions(apply=True)
    assert r["ambiguous_files"] == 1
    assert r["planned_chunks"] == 0 and r["deduped_chunks"] == 0
    assert col.deleted == []


def test_amendments_with_distinct_notice_numbers_survive(patch_col, monkeypatch):
    """🔴 회귀 핵심 — 고시번호가 다른 개정 3건은 한 건도 삭제 계획에 들지 않는다."""
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    col = patch_col(_metas([
        ("519307-2-(제2025-145호)_선별급여_기준_고시_일부개정.pdf", 10),
        ("529042-2-(제2026-122호)_선별급여_기준_고시_일부개정.pdf", 7),
        ("527856-1-(제2026-102호)_선별급여_기준_고시_일부개정.pdf", 7),
    ]))
    r = mp.dedup_versions(apply=True)
    assert r["planned_chunks"] == 0 and col.deleted == []


def test_full_text_and_amendment_never_merge(patch_col, monkeypatch):
    """🔴 전문 + 일부개정이 한 그룹이 되면 개정분이 삭제된다(종전 결함의 정체)."""
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    col = patch_col(_metas([
        ("520559-1-의료급여수가의_기준_및_일반기준(고시_제2025-171호)_전문.pdf", 378),
        ("530072-1-(고시_제2026-138호)_의료급여수가의_기준_및_일반기준_일부개정.pdf", 38),
    ]))
    r = mp.dedup_versions(apply=True)
    assert r["planned_chunks"] == 0 and col.deleted == []


def test_guard_still_blocks_bulk_delete(patch_col, monkeypatch):
    """디스크풀 인시던트 가드는 유지된다(apply 경로에서)."""
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 5)
    col = patch_col(_metas([("200-1-foo.pdf", 8), ("100-1-foo.pdf", 8)]))
    r = mp.dedup_versions(apply=True)
    assert r.get("skipped_unsafe") == 8
    assert r["deduped_chunks"] == 0 and col.deleted == []


def test_manual_script_requires_explicit_apply():
    """운영자 스크립트가 기본 삭제면 '계획만 보려다' 지운다."""
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "scripts/dedup_medlaw.py"), encoding="utf-8") as f:
        src = f.read()
    assert '"--apply"' in src and "action=\"store_true\"" in src
    assert "dedup_versions(apply=args.apply)" in src
