

def test_auto_purge_matches_null_completed_at(tmp_path):
    """completed_at이 NULL인 실패 잡도 나이 기준으로 삭제돼야 한다(영구 잔존 방지).

    `completed_at < cutoff`는 NULL을 매칭하지 않아 과거엔 그런 행이 영구 잔존했다.
    """
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE jobs (id TEXT, status TEXT, created_at TEXT, completed_at TEXT)")
    db.executemany(
        "INSERT INTO jobs VALUES (?,?,?,?)",
        [("old-null", "failed", "2026-06-29T00:00:00Z", None),      # 오래됨·NULL
         ("old-set", "completed", "2026-06-29T00:00:00Z", "2026-06-29T01:00:00Z"),
         ("new-null", "failed", "2026-07-21T00:00:00Z", None)],     # 최근·NULL(보존)
    )
    cutoff = "2026-07-14T00:00:00Z"
    n = db.execute("DELETE FROM jobs WHERE status IN ('completed','failed') "
                   "AND COALESCE(completed_at, created_at) < ?", (cutoff,)).rowcount
    left = {r[0] for r in db.execute("SELECT id FROM jobs")}
    assert n == 2 and left == {"new-null"}
