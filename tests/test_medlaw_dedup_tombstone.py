"""medlaw dedup tombstone — 삭제분 재색인 churn 봉쇄 (v2.125.35).

🔴 배경(2026-09-03 라이브 확진): 수집의 `known` 집합이 **컬렉션 파생**이라 dedup이
지운 source_file 자리가 비고, 스케줄러가 KHIDI 최신 페이지에서 같은 첨부를 "신규"로
다시 받았다 — 08-31에 지운 중복 4파일/139청크가 이틀 만에 전량 재색인(13,388→13,579).
처치=삭제 사실을 state의 `dedup_tombstones`에 기록하고 수집이 그 파일을 건너뛴다.

불변식:
  1) apply=True 삭제 성공분은 tombstone에 기록된다(실패분은 기록 안 됨).
  2) 수집은 tombstone 파일을 다운로드/색인하지 않고, 건너뛴 수를 **보고**한다(silent drop 금지).
  3) 같은 게시글에 다른 첨부(fileId 상이)가 오면 막히지 않는다 — 정당한 갱신 통과.
"""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import core.medlaw_processor as mp
from core.khidi_collector import KhidiAttachment, KhidiDetail, KhidiPost


class _Col:
    def __init__(self, metas):
        self._metas = metas
        self.deleted = []

    def get(self, include=None):
        return {"metadatas": self._metas}

    def delete(self, where=None):
        self.deleted.append(where)

    def count(self):
        return len(self._metas)


def _metas(spec):
    out = []
    for sf, n in spec:
        out += [{"origin": "khidi_notice", "source_file": sf} for _ in range(n)]
    return out


@pytest.fixture
def iso_state(monkeypatch, tmp_path):
    p = tmp_path / "state.json"
    monkeypatch.setattr(mp, "_state_path", lambda: str(p))
    return p


def _patch_col(monkeypatch, metas):
    col = _Col(metas)
    import client.rag as rag
    monkeypatch.setattr(rag, "get_collection", lambda *a, **k: col)
    monkeypatch.setattr(mp, "_link_lists_warn", lambda: 0, raising=False)
    return col


# ── 1) 삭제 → tombstone 기록 ────────────────────────────────────────────────

def test_apply_records_tombstones(monkeypatch, iso_state):
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    _patch_col(monkeypatch, _metas([("200-1-foo.pdf", 5), ("100-1-foo.pdf", 5)]))
    r = mp.dedup_versions(apply=True)
    assert r["deduped_files"] == 1
    assert mp._tombstones() == {"100-1-foo.pdf"}
    saved = json.loads(iso_state.read_text())
    assert saved["dedup_tombstones"] == ["100-1-foo.pdf"]


def test_failed_delete_is_not_tombstoned(monkeypatch, iso_state):
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    col = _patch_col(monkeypatch, _metas([("200-1-foo.pdf", 5), ("100-1-foo.pdf", 5)]))

    def boom(where=None):
        raise RuntimeError("chroma down")
    col.delete = boom
    r = mp.dedup_versions(apply=True)
    assert r["deduped_files"] == 0
    assert mp._tombstones() == set(), "삭제 안 된 파일이 tombstone되면 코퍼스에서 영구 소실된다"


def test_dry_run_records_nothing(monkeypatch, iso_state):
    _patch_col(monkeypatch, _metas([("200-1-foo.pdf", 5), ("100-1-foo.pdf", 5)]))
    mp.dedup_versions()
    assert mp._tombstones() == set()


def test_tombstones_accumulate(iso_state):
    mp._add_tombstones(["a.pdf"])
    mp._add_tombstones(["b.pdf", "a.pdf"])
    assert mp._tombstones() == {"a.pdf", "b.pdf"}


# ── 2) 수집이 tombstone을 건너뛰고 보고한다 ─────────────────────────────────

def _fake_khidi(monkeypatch, filenames):
    """KHIDI 목록 1페이지·게시글 1건·첨부 filenames를 흉내낸다. 다운로드 호출을 기록."""
    import core.khidi_collector as khidi
    downloads = []
    atts = [KhidiAttachment(url="u", title_id=fn.split("-")[0], file_id=fn.split("-")[1],
                            kind="pdf", filename=fn, ext="pdf") for fn in filenames]
    det = KhidiDetail(no1="1", link_id="L", title="의료기기법 시행령 일부개정", attachments=atts)
    monkeypatch.setattr(khidi, "_session", lambda: None)
    monkeypatch.setattr(khidi, "fetch_list_page",
                        lambda page, sess=None: [KhidiPost(no1="1", link_id="L", detail_url="d")]
                        if page == 1 else [])
    monkeypatch.setattr(khidi, "fetch_detail", lambda post, sess=None: det)
    monkeypatch.setattr(khidi, "safe_filename", lambda a: a.filename)
    monkeypatch.setattr(khidi, "download_attachment",
                        lambda a, d, sess=None: downloads.append(a.filename) or f"/tmp/{a.filename}")
    monkeypatch.setattr(khidi, "DELAY_S", 0)
    return downloads


def test_collect_skips_tombstoned_and_reports(monkeypatch, iso_state, tmp_path):
    mp._add_tombstones(["500-1-dup.pdf"])
    downloads = _fake_khidi(monkeypatch, ["500-1-dup.pdf"])
    _patch_col(monkeypatch, [])                      # known 비어 있음 — tombstone만이 방어선
    monkeypatch.setattr(mp, "index_files", lambda fps, **k: {"indexed": len(fps)})
    r = mp.collect_and_index_new(max_scan_pages=1, dest_dir=str(tmp_path))
    assert downloads == [], "tombstone 파일을 다시 다운로드했다 — churn 재발"
    assert r["new_files"] == 0
    assert r["tombstone_skipped"] == 1, "건너뛴 사실이 보고에 없다(silent drop)"


def test_collect_passes_different_attachment_same_post(monkeypatch, iso_state, tmp_path):
    """같은 게시글의 **다른 첨부**(fileId 상이)는 막히지 않는다 — 정당한 갱신 통과."""
    mp._add_tombstones(["500-1-dup.pdf"])
    downloads = _fake_khidi(monkeypatch, ["500-2-dup.pdf"])   # fileId 1→2
    _patch_col(monkeypatch, [])
    indexed = {}
    monkeypatch.setattr(mp, "index_files", lambda fps, **k: indexed.update(n=len(fps)) or {"indexed": len(fps)})
    monkeypatch.setattr(mp, "purge_noise", lambda **k: {})
    r = mp.collect_and_index_new(max_scan_pages=1, dest_dir=str(tmp_path))
    assert downloads == ["500-2-dup.pdf"]
    assert r["new_files"] == 1 and r["tombstone_skipped"] == 0


def test_known_still_skips_without_tombstone(monkeypatch, iso_state, tmp_path):
    """기존 known 경로 불변 — 컬렉션에 있는 파일은 tombstone 없이도 건너뛴다."""
    downloads = _fake_khidi(monkeypatch, ["500-1-dup.pdf"])
    _patch_col(monkeypatch, _metas([("500-1-dup.pdf", 3)]))
    r = mp.collect_and_index_new(max_scan_pages=1, dest_dir=str(tmp_path))
    assert downloads == [] and r["new_files"] == 0
    assert r["tombstone_skipped"] == 0
