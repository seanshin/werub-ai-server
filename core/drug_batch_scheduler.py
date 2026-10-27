"""Drug Batch Scheduler — 파이프라인 오케스트레이션 + 야간 자동 모드

gpu_scheduler.py 프로파일 연동:
  - off_hours (19:00~08:00): 야간 모드 자동 활성화
    → limit 200, model qwen2.5:14b, priority 3, 연속 실행
  - medical_hours (08:00~19:00): 일반 모드
    → limit 50, model werub-medical:7b, priority 4, 스케줄 실행만
"""
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone

from .drug_collector import ADAPTERS, get_adapter, insert_to_staging
from .drug_enricher import (
    calc_confidence,
    parse_llm_response,
    poll_batch_results,
    submit_enrich_batch,
)
from .drug_validator import validate_drug

logger = logging.getLogger("ollama-admin")

CLINIC_DB_PATH = None
_scheduler_lock = threading.Lock()
_last_runs: dict[str, str] = {}

# ── 야간 모드 설정 ──────────────────────────
# ⚠️ v2.125.47: 기본 off — 자동 수집 소스가 LLM_SEED(모델 자체 지식)뿐이었고, 06-04~09-27
# 같은 약을 반복 생성해 staging 2.5만 건이 전부 중복으로 버려지는 **수렴하지 않는 루프**였다
# (밤마다 14b 호출 ~17회·산출 0). 켜려면 config `drug_batch_night_enabled` + 명시 소스.
NIGHT_MODE = {
    "enabled": False,
    "hours": (22, 6),              # 22:00~06:00 자동 실행
    "limit": 200,                  # 최대 200건/회
    "model": "qwen2.5:14b",       # 고품질 모델 (야간 상주)
    "priority": 3,                 # 일반급 승격
    "batch_size": 100,             # LLM 배치 크기
    "poll_timeout": 600,           # LLM 대기 10분
    "interval_minutes": 30,        # 30분마다 재실행 (새 데이터 있으면)
    "max_runs_per_night": 10,      # 야간 최대 10회
    "pause_on_medical": True,      # 의료 긴급 요청 시 양보
}

DAY_MODE = {
    "limit": 50,
    "model": "werub-medical:7b",
    "priority": 4,
    "batch_size": 50,
    "poll_timeout": 300,
}

# 스케줄 (일반 모드용)
SCHEDULES = {
    "daily_collect": {"hour": 2, "minute": 0, "interval": "daily"},
    "weekly_interaction": {"weekday": 0, "hour": 3, "minute": 0, "interval": "weekly"},
    "monthly_denial": {"day": 1, "hour": 4, "minute": 0, "interval": "monthly"},
    "monthly_validate": {"day": 15, "hour": 5, "minute": 0, "interval": "monthly"},
}

# 야간 모드 상태
_night_state = {
    "active": False,
    "runs_tonight": 0,
    "last_night_date": "",
    "current_run_id": None,
}


def _get_clinic_db() -> sqlite3.Connection:
    import os
    path = CLINIC_DB_PATH or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clinic_codes.db"
    )
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def is_night_hours() -> bool:
    """현재가 야간 시간대인지 확인."""
    hour = datetime.now().hour
    start, end = NIGHT_MODE["hours"]
    if start > end:  # 22~6 같이 자정을 넘는 경우
        return hour >= start or hour < end
    return start <= hour < end


def get_active_mode() -> dict:
    """현재 활성 모드 설정 반환."""
    if NIGHT_MODE["enabled"] and is_night_hours():
        return {
            "mode": "night",
            "night_active": _night_state["active"],
            "runs_tonight": _night_state["runs_tonight"],
            "max_runs": NIGHT_MODE["max_runs_per_night"],
            **NIGHT_MODE,
        }
    return {"mode": "day", "night_active": False, **DAY_MODE}


def _should_run(name: str, sched: dict, now: datetime) -> bool:
    """스케줄 실행 여부 판단."""
    key = f"{name}:{now.strftime('%Y-%m-%d')}"
    if sched["interval"] == "daily":
        if now.hour == sched["hour"] and now.minute == sched["minute"]:
            if key not in _last_runs:
                _last_runs[key] = now.isoformat()
                return True
    elif sched["interval"] == "weekly":
        if now.weekday() == sched.get("weekday", 0) and now.hour == sched["hour"] and now.minute == sched["minute"]:
            if key not in _last_runs:
                _last_runs[key] = now.isoformat()
                return True
    elif sched["interval"] == "monthly":
        if now.day == sched.get("day", 1) and now.hour == sched["hour"] and now.minute == sched["minute"]:
            if key not in _last_runs:
                _last_runs[key] = now.isoformat()
                return True
    return False


# 자동 트리거는 **명시 소스가 있을 때만** 수집한다. LLM_SEED(T4=모델 자체 지식)는 수동 전용 —
# 모델이 지어낸 약물 정보가 임상 표(drug_codes → medical_cli 처방 추천)로 들어가면 안 된다.
_AUTO_TRIGGERS = {"scheduler", "night_auto"}
# 임상 표에 자동 반영하지 않는 출처 등급(사람 검토 필요)
_REVIEW_ONLY_TIERS = {"T4"}


def start_scheduler_loop(night_enabled: bool | None = None):
    """스케줄러 데몬 스레드 시작."""
    if night_enabled is not None:
        NIGHT_MODE["enabled"] = bool(night_enabled)
    def _loop():
        logger.info("[drug-batch] scheduler started (night mode: %s)", "ON" if NIGHT_MODE["enabled"] else "OFF")
        night_last_run_time = 0

        while True:
            try:
                now = datetime.now()
                today = now.strftime("%Y-%m-%d")

                # 날짜 바뀌면 야간 카운터 리셋
                if _night_state["last_night_date"] != today:
                    _night_state["runs_tonight"] = 0
                    _night_state["last_night_date"] = today

                # ── 야간 자동 모드 ──
                if NIGHT_MODE["enabled"] and is_night_hours():
                    if not _night_state["active"]:
                        _night_state["active"] = True
                        logger.info("[drug-batch] NIGHT MODE activated")

                    interval = NIGHT_MODE["interval_minutes"] * 60
                    can_run = (
                        _night_state["runs_tonight"] < NIGHT_MODE["max_runs_per_night"]
                        and time.time() - night_last_run_time >= interval
                    )

                    if can_run:
                        night_last_run_time = time.time()
                        _night_state["runs_tonight"] += 1
                        logger.info(
                            "[drug-batch] night run %d/%d",
                            _night_state["runs_tonight"],
                            NIGHT_MODE["max_runs_per_night"],
                        )
                        threading.Thread(
                            target=run_pipeline,
                            kwargs={
                                "run_type": "night_collect",
                                "trigger": "night_auto",
                                "limit": NIGHT_MODE["limit"],
                                "model": NIGHT_MODE["model"],
                                "poll_timeout": NIGHT_MODE["poll_timeout"],
                            },
                            daemon=True,
                            name="drug-night",
                        ).start()

                else:
                    # 야간 모드 종료
                    if _night_state["active"]:
                        logger.info(
                            "[drug-batch] NIGHT MODE deactivated (runs: %d)",
                            _night_state["runs_tonight"],
                        )
                        _night_state["active"] = False

                    # ── 일반 스케줄 ──
                    for name, sched in SCHEDULES.items():
                        if _should_run(name, sched, now):
                            logger.info(f"[drug-batch] triggering {name}")
                            threading.Thread(
                                target=run_pipeline,
                                args=(name, "scheduler"),
                                daemon=True,
                                name=f"drug-{name}",
                            ).start()

            except Exception as e:
                logger.error(f"[drug-batch] scheduler error: {e}")
            time.sleep(60)

    t = threading.Thread(target=_loop, daemon=True, name="drug-batch-scheduler")
    t.start()
    return t


def run_pipeline(
    run_type: str = "daily_collect",
    trigger: str = "manual",
    source: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    model: str | None = None,
    poll_timeout: int | None = None,
) -> dict:
    """파이프라인 전체 실행. 야간 모드 시 자동 설정 적용."""
    # 모드에 따른 기본값 자동 적용
    mode = get_active_mode()
    if limit is None:
        limit = mode.get("limit", 50)
    if model is None:
        model = mode.get("model", "werub-medical:7b")
    if poll_timeout is None:
        poll_timeout = mode.get("poll_timeout", 300)

    if source is None and trigger in _AUTO_TRIGGERS:
        # 무성 루프 금지 — 자동 실행이 LLM_SEED로 떨어지지 않게 하고, 그 사실을 남긴다
        logger.info("[drug-batch] %s(%s) skip — 자동 수집 소스 미지정(LLM_SEED는 수동 전용)",
                    run_type, trigger)
        return {"run_type": run_type, "status": "skipped", "reason": "no_automatic_source"}

    conn = _get_clinic_db()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    start_time = time.time()

    # batch_run 레코드 생성
    cur = conn.execute(
        "INSERT INTO batch_run (run_type, trigger, source, status, started_at, llm_model) VALUES (?,?,?,?,?,?)",
        [run_type, trigger, source, "running", now_str, model],
    )
    conn.commit()
    run_id = cur.lastrowid
    _night_state["current_run_id"] = run_id
    logger.info(
        "[drug-batch] pipeline #%d started: %s trigger=%s mode=%s limit=%d model=%s",
        run_id, run_type, trigger, mode["mode"], limit, model,
    )

    result = {
        "batch_run_id": run_id,
        "run_type": run_type,
        "mode": mode["mode"],
        "status": "completed",
        "fetched": 0,
        "enriched": 0,
        "validated": 0,
        "committed": 0,
        "skipped": 0,
        "failed": 0,
        "needs_review": 0,
    }

    try:
        # ── Phase 1: COLLECT ──
        adapter_name = source or "LLM_SEED"
        adapter = get_adapter(adapter_name)
        if not adapter:
            adapter = get_adapter("LLM_SEED")

        records = adapter.fetch(conn, limit=limit)
        if records:
            inserted = insert_to_staging(conn, records, run_id)
            result["fetched"] = inserted
            conn.execute("UPDATE batch_run SET total_fetched=? WHERE id=?", [inserted, run_id])
            conn.commit()
        else:
            logger.info(f"[drug-batch] #{run_id} no records to collect")

        if result["fetched"] == 0 or dry_run:
            _finalize_run(conn, run_id, result, start_time)
            conn.close()
            return result

        # ── Phase 2: ENRICH (LLM) ──
        staging_rows = conn.execute(
            "SELECT id, source, source_tier, source_url, raw_data, target_table FROM staging_drug WHERE batch_run_id=? AND status='fetched'",
            [run_id],
        ).fetchall()
        staging_dicts = [dict(r) for r in staging_rows]

        if staging_dicts:
            conn.execute("UPDATE staging_drug SET status='enriching' WHERE batch_run_id=? AND status='fetched'", [run_id])
            conn.commit()

            batch_id = submit_enrich_batch(staging_dicts, enrich_type="drug", model=model)
            if batch_id:
                conn.execute("UPDATE batch_run SET llm_batch_id=? WHERE id=?", [batch_id, run_id])
                conn.commit()

                jobs = poll_batch_results(batch_id, timeout_sec=poll_timeout)
                if jobs:
                    _process_enrich_results(conn, run_id, jobs, result)
                else:
                    result["failed"] = result["fetched"]
                    conn.execute("UPDATE staging_drug SET status='failed', error='LLM timeout' WHERE batch_run_id=? AND status='enriching'", [run_id])
                    conn.commit()
            else:
                result["failed"] = result["fetched"]

        # ── Phase 3+4: VALIDATE + COMMIT ──
        enriched_rows = conn.execute(
            "SELECT id, source, source_tier, parsed_data, target_table, confidence FROM staging_drug WHERE batch_run_id=? AND status='enriched'",
            [run_id],
        ).fetchall()

        for row in enriched_rows:
            row_dict = dict(row)
            parsed = json.loads(row_dict["parsed_data"]) if row_dict["parsed_data"] else None
            if not parsed:
                result["failed"] += 1
                conn.execute("UPDATE staging_drug SET status='failed', error='no parsed data' WHERE id=?", [row_dict["id"]])
                continue

            validations = validate_drug(parsed, conn)
            all_passed = all(v.passed for v in validations)

            if all_passed and row_dict.get("source_tier") in _REVIEW_ONLY_TIERS:
                # 검증은 통과했어도 모델 자체 지식은 임상 표에 자동 반영하지 않는다
                result["needs_review"] += 1
                conn.execute("UPDATE staging_drug SET status='needs_review' WHERE id=?", [row_dict["id"]])
            elif all_passed:
                result["validated"] += 1
                if not dry_run:
                    _commit_drug(conn, row_dict, parsed, run_id, model)
                    result["committed"] += 1
                    conn.execute("UPDATE staging_drug SET status='committed' WHERE id=?", [row_dict["id"]])
                else:
                    conn.execute("UPDATE staging_drug SET status='validated' WHERE id=?", [row_dict["id"]])
            else:
                result["skipped"] += 1
                errors = "; ".join(v.message for v in validations if not v.passed)
                conn.execute("UPDATE staging_drug SET status='skipped', error=? WHERE id=?", [errors, row_dict["id"]])

        conn.commit()
        _finalize_run(conn, run_id, result, start_time)

    except Exception as e:
        logger.error(f"[drug-batch] pipeline #{run_id} error: {e}")
        result["status"] = "failed"
        conn.execute("UPDATE batch_run SET status='failed', error=? WHERE id=?", [str(e), run_id])
        conn.commit()

    _night_state["current_run_id"] = None
    conn.close()
    return result


def _process_enrich_results(conn: sqlite3.Connection, run_id: int, jobs: list[dict], result: dict):
    """LLM 배치 결과를 staging에 반영."""
    total_tokens = 0
    for job in jobs:
        meta = job.get("metadata", {})
        staging_id = meta.get("staging_id")
        if not staging_id:
            continue

        total_tokens += job.get("tokens", 0)

        if job.get("status") == "completed" and job.get("response"):
            parsed = parse_llm_response(job["response"])
            if parsed:
                confidence = calc_confidence(
                    source_tier=meta.get("source_tier", "T4"),
                    enrich_method="structure",
                    validations=[],
                )
                conn.execute(
                    "UPDATE staging_drug SET status='enriched', parsed_data=?, confidence=? WHERE id=?",
                    [json.dumps(parsed, ensure_ascii=False), confidence, staging_id],
                )
                result["enriched"] += 1
            else:
                conn.execute(
                    "UPDATE staging_drug SET status='failed', error='parse_failed' WHERE id=?",
                    [staging_id],
                )
                result["failed"] += 1
        else:
            error = job.get("error", "unknown")
            conn.execute(
                "UPDATE staging_drug SET status='failed', error=? WHERE id=?",
                [error, staging_id],
            )
            result["failed"] += 1

    result["tokens"] = total_tokens
    conn.execute("UPDATE batch_run SET total_enriched=?, total_failed=?, llm_tokens_used=? WHERE id=?",
                 [result["enriched"], result["failed"], total_tokens, run_id])
    conn.commit()


def _commit_drug(conn: sqlite3.Connection, staging_row: dict, parsed: dict, run_id: int, model: str = "werub-medical:7b"):
    """staging → drug_codes + data_provenance 삽입."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    code = parsed.get("code", "")
    confidence = staging_row.get("confidence", 0.75)

    conn.execute(
        """INSERT OR IGNORE INTO drug_codes
           (code, name_generic, name_ko, dosage_form, strength, unit_price, insurance_type,
            atc_code, category, route, frequency, max_daily_dose, pregnancy_category,
            renal_adjustment, contraindications, side_effects, is_common,
            source, source_tier, source_url, source_version, collected_at, enriched_by, confidence, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            code,
            parsed.get("name_generic", ""),
            parsed.get("name_ko", ""),
            parsed.get("dosage_form", ""),
            parsed.get("strength", ""),
            parsed.get("unit_price", 0),
            parsed.get("insurance_type", "covered"),
            parsed.get("atc_code", ""),
            parsed.get("category", ""),
            parsed.get("route", ""),
            parsed.get("frequency", ""),
            parsed.get("max_daily_dose", ""),
            parsed.get("pregnancy_category", ""),
            parsed.get("renal_adjustment", ""),
            json.dumps(parsed.get("contraindications", []), ensure_ascii=False),
            json.dumps(parsed.get("side_effects", []), ensure_ascii=False),
            parsed.get("is_common", 0),
            staging_row.get("source", "LLM_SEED"),
            staging_row.get("source_tier", "T4"),
            staging_row.get("source_url"),
            f"batch_run#{run_id}",
            now_str,
            f"LLM:{model}",
            confidence,
            now_str,
        ],
    )

    conn.execute(
        """INSERT INTO data_provenance
           (table_name, record_key, action, source, source_tier, source_url, source_version, batch_run_id, actor, confidence, created_at)
           VALUES ('drug_codes', ?, 'CREATE', ?, ?, ?, ?, ?, 'pipeline', ?, ?)""",
        [code, staging_row.get("source", "LLM_SEED"), staging_row.get("source_tier", "T4"),
         staging_row.get("source_url"), f"batch_run#{run_id}", run_id, confidence, now_str],
    )


def _finalize_run(conn: sqlite3.Connection, run_id: int, result: dict, start_time: float):
    """batch_run 완료 처리."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    duration = int((time.time() - start_time) * 1000)
    summary = json.dumps({
        k: v for k, v in result.items() if k not in ("batch_run_id", "run_type")
    }, ensure_ascii=False)

    conn.execute(
        """UPDATE batch_run SET
           status=?, completed_at=?, duration_ms=?,
           total_fetched=?, total_enriched=?, total_validated=?,
           total_committed=?, total_skipped=?, total_failed=?,
           summary=?
           WHERE id=?""",
        [
            result.get("status", "completed"), now_str, duration,
            result["fetched"], result["enriched"], result["validated"],
            result["committed"], result["skipped"], result["failed"],
            summary, run_id,
        ],
    )
    conn.commit()
    logger.info(f"[drug-batch] pipeline #{run_id} done in {duration}ms: {result}")
