#!/usr/bin/env python3
# Created: 2026-04-20
# Content Safety — 전체 학습 파이프라인 오케스트레이션
# prepare → train → evaluate → deploy (조건부)

import json, os, sys, subprocess, time, sqlite3

TRAINING_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.dirname(TRAINING_DIR)
DB_PATH = os.path.join(ROOT_DIR, "training.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS training_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, base_model TEXT, status TEXT DEFAULT 'pending',
        f1_score REAL, precision_score REAL, recall_score REAL,
        sample_count INTEGER DEFAULT 0, prev_f1_score REAL,
        model_path TEXT, error_message TEXT, priority TEXT DEFAULT 'P2', trigger_type TEXT,
        started_at TEXT, completed_at TEXT, deployed_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        correction_count INTEGER DEFAULT 0, synthetic_count INTEGER DEFAULT 0, clean_count INTEGER DEFAULT 0
    )""")
    return conn


def run_step(name, script):
    """파이프라인 스텝 실행"""
    print(f"\n{'='*60}")
    print(f"  Step: {name}")
    print(f"{'='*60}")
    result = subprocess.run(
        [sys.executable, os.path.join(TRAINING_DIR, script)],
        cwd=ROOT_DIR, timeout=3600
    )
    if result.returncode != 0:
        raise RuntimeError(f"{name} 실패 (exit code {result.returncode})")
    print(f"  ✅ {name} 완료")


def main():
    trigger = sys.argv[1] if len(sys.argv) > 1 else "manual"
    print(f"🚀 Content Safety Training Pipeline 시작 (trigger: {trigger})")

    # DB 기록
    conn = get_db()
    conn.execute(
        "INSERT INTO training_runs (base_model, status, trigger_type, started_at) VALUES ('qwen2.5:1.5b', 'running', ?, datetime('now'))",
        (trigger,)
    )
    conn.commit()
    run_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    print(f"  Run #{run_id}")

    try:
        # 1. 데이터 준비
        run_step("데이터 준비", "prepare_data.py")

        # 2. Fine-tuning
        run_step("Fine-tuning", "train.py")

        # 3. 평가
        run_step("모델 평가", "evaluate.py")

        # 평가 결과 확인
        eval_path = os.path.join(TRAINING_DIR, "outputs", "eval_result.json")
        with open(eval_path) as f:
            eval_result = json.load(f)

        f1 = eval_result.get("f1_score", 0)
        passed = eval_result.get("passed", False)

        # DB 업데이트
        conn = get_db()
        conn.execute(
            """UPDATE training_runs SET
                f1_score=?, precision_score=?, recall_score=?,
                sample_count=?, status=?, completed_at=datetime('now')
            WHERE id=?""",
            (f1, eval_result.get("precision", 0), eval_result.get("recall", 0),
             eval_result.get("total", 0),
             "completed" if passed else "failed",
             run_id)
        )
        conn.commit()
        conn.close()

        if not passed:
            print(f"\n❌ 평가 미통과 (F1={f1:.4f} < threshold) — 배포 건너뜀")
            return

        # 4. 배포
        run_step("Ollama 배포", "deploy_ollama.py")

        # DB 배포 기록
        conn = get_db()
        # 이전 deployed 해제
        conn.execute("UPDATE training_runs SET status='completed' WHERE status='deployed' AND id!=?", (run_id,))
        conn.execute(
            "UPDATE training_runs SET status='deployed', deployed_at=datetime('now') WHERE id=?",
            (run_id,)
        )
        conn.commit()
        conn.close()

        print(f"\n✅ Training Pipeline 완료 — Run #{run_id}, F1={f1:.4f}, 배포 완료")

    except Exception as e:
        print(f"\n❌ Pipeline 실패: {e}")
        conn = get_db()
        conn.execute(
            "UPDATE training_runs SET status='failed', error_message=?, completed_at=datetime('now') WHERE id=?",
            (str(e), run_id)
        )
        conn.commit()
        conn.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
