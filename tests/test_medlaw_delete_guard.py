"""medlaw_processor col.delete 대량 삭제 가드 회귀 (2026-06-11 인시던트 방지).

dedup_versions/purge_noise가 임계 초과 삭제를 skip하는지 검증(ChromaDB 모킹, GPU 불요).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import core.medlaw_processor as mp  # noqa: E402


class FakeCol:
    def __init__(self, metas):
        self._metas = metas
        self.deleted = []

    def get(self, include=None):
        return {"metadatas": self._metas}

    def delete(self, where=None):
        self.deleted.append(where)


@pytest.fixture
def patch_col(monkeypatch):
    holder = {}

    def _factory(metas):
        col = FakeCol(metas)
        holder["col"] = col
        monkeypatch.setattr("client.rag.get_collection", lambda name: col)
        return col
    return _factory


# ── dedup_versions ──
def _dedup_metas():
    """같은 문서('foo')가 두 번 색인된 형상 — 2026-08-31 재설계 후의 유일한 삭제 대상.

    ⚠️ 종전 이 픽스처는 10청크 vs 8청크였고 "더 긴 쪽 보존"을 고정하고 있었다. 그 규칙이
    최신 일부개정을 지우던 결함의 정체라, **청크 수가 같은 진짜 중복**으로 바꾼다.
    청크 수가 다른 경우는 `test_medlaw_dedup_identity.py`가 `ambiguous`로 고정한다.
    """
    metas = [{"origin": "khidi_notice", "source_file": "200-1-foo.pdf"} for _ in range(8)]
    metas += [{"origin": "khidi_notice", "source_file": "100-1-foo.pdf"} for _ in range(8)]
    return metas


def test_dedup_skips_when_over_threshold(patch_col, monkeypatch):
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 5)
    col = patch_col(_dedup_metas())
    r = mp.dedup_versions(apply=True)          # 가드는 apply 경로에서 동작한다
    assert r.get("skipped_unsafe") == 8        # 삭제대상 8 > 임계 5
    assert r["deduped_chunks"] == 0
    assert col.deleted == []                    # col.delete 미호출(폭주 차단)


def test_dedup_proceeds_when_under_threshold(patch_col, monkeypatch):
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    monkeypatch.setattr(mp, "_link_lists_warn", lambda: 0)
    col = patch_col(_dedup_metas())
    r = mp.dedup_versions(apply=True)
    assert r["deduped_files"] == 1 and r["deduped_chunks"] == 8
    assert col.deleted == [{"source_file": "100-1-foo.pdf"}]   # 낡은 titleId만 삭제


# ── purge_noise ──
def _noise_metas(n):
    # 검토의견서 = 노이즈 첨부 → 삭제 대상
    return [{"origin": "khidi_notice", "source_file": f"{i}-1-검토의견서.pdf"} for i in range(n)]


def test_purge_skips_when_over_threshold(patch_col, monkeypatch):
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 3)
    col = patch_col(_noise_metas(10))
    r = mp.purge_noise()
    assert r.get("skipped_unsafe") == 10
    assert r["purged_chunks"] == 0 and col.deleted == []


def test_purge_proceeds_when_under_threshold(patch_col, monkeypatch):
    monkeypatch.setattr(mp, "_MAX_SAFE_DELETE_CHUNKS", 500)
    monkeypatch.setattr(mp, "_link_lists_warn", lambda: 0)
    col = patch_col(_noise_metas(4))
    r = mp.purge_noise()
    assert r["purged_chunks"] == 4 and len(col.deleted) == 4
