"""민감 DB 추적 방지 가드 — .gitignore default-deny 회귀(2026-08-04).

종전 .gitignore는 무시할 DB를 하나씩 열거해, 새 DB가 생길 때마다 수동 추가가 필요했다.
그 결과 6개(counseling·medical_cli·medical_law·meeting·orchestrator·rag_fts)가 누락돼
`git add -A` 한 번이면 PHI가 올라갈 상태였다(유출은 없었음 — 커밋 이력 0·원격 없음).
→ *.db 전부 무시 + 시드 DB만 예외로 뒤집었다. 이 테스트가 되돌림을 막는다.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SENSITIVE = [
    "medical.db",       # 환자 상담 PHI
    "counseling.db",    # 상담 대화·위기 감지
    "meeting.db",       # 회의 STT 전사
    "medical_law.db",   # 의료법 질의 이력
    "medical_cli.db",
    "orchestrator.db",
    "rag_fts.db",
    "users.db",         # 비번 해시
    "queue.db",         # 채팅 메시지
    "stt_vocab.db",
    "moderation.db",
    "training.db",
    "future_unknown.db",              # 아직 없는 DB도 자동 보호되어야 한다
    "some/nested/path/secrets.db",
]
SEED_TRACKED = ["clinic_codes.db", "game.db", "hr_law.db"]


def _ignored(path: str) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT)
    return r.returncode == 0


def test_sensitive_dbs_are_ignored():
    leaked = [p for p in SENSITIVE if not _ignored(p)]
    assert not leaked, f"추적 가능한 민감 DB: {leaked}"


def test_seed_dbs_remain_trackable():
    """시드/참조 DB는 의도적으로 추적한다 — 예외가 깨지면 갱신이 막힌다."""
    blocked = [p for p in SEED_TRACKED if _ignored(p)]
    assert not blocked, f"시드 DB가 무시됨: {blocked}"


def test_wal_shm_artifacts_ignored():
    for suffix in ("-shm", "-wal"):
        assert _ignored(f"anything.db{suffix}")


def test_only_seed_dbs_are_tracked():
    """추적 중인 .db가 시드 3종뿐인지 — 실수로 커밋된 게 있으면 잡는다."""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout
    tracked = sorted(x for x in out.splitlines() if x.endswith(".db"))
    assert tracked == sorted(SEED_TRACKED), f"예상 밖 추적 DB: {tracked}"
