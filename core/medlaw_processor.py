"""의료법 RAG 증분 수집 스케줄러 (medical-law-rag P1 — 꾸준한 학습 보강).

백필(과거분 1회)과 별개로, KHIDI 게시판에 새로 올라오는 고시를 주기 감지·색인한다.
notice_processor.start_notice_scheduler 패턴 차용(threading daemon).

상태: 최고 색인 게시번호(no1)를 state 파일에 기록 → 매일 1페이지(최신순) 조회 →
no1 > last_seen 신규만 다운로드·파싱·임베딩·색인 → 상태 갱신. 멱등(source_file delete-first).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("ollama-admin")

MEDLAW_COLLECTION = "medical_law_knowledge"
_scheduler_started = False

# ── ChromaDB col.delete 폭주 가드 (2026-06-11 인시던트) ─────────────────────────
# 대량 col.delete가 ChromaDB(Rust) HNSW compaction을 폭주시켜 link_lists.bin이 수백 GB로
# 손상→디스크풀→서버다운을 유발(2026-06-11). 일일 증분 dedup/purge는 보통 소량(<수십청크)이라
# 안전하나, 누적·대량 삭제는 위험 → 1회 삭제량을 임계로 캡(초과 시 skip+경고, 수동 처리 유도).
# 운영자 수동 정리는 워치독(load_monitored.sh 패턴) 동반 권장. docs/MEDICAL_LAW_RAG_GUIDE.md §6.
_MAX_SAFE_DELETE_CHUNKS = int(os.environ.get("MEDLAW_MAX_SAFE_DELETE", "500"))
_LINK_LISTS_WARN_MB = int(os.environ.get("MEDLAW_LINK_LISTS_WARN_MB", "500"))


def _link_lists_warn() -> int:
    """vector_db 내 최대 link_lists.bin 크기(MB)를 확인, 임계 초과 시 CRITICAL 로그.
    폭주 조기 감지(관측용) — 자동 truncate는 mmap 위험으로 하지 않음(운영자/워치독 처리)."""
    try:
        vdb = os.path.join(_base_dir(), "vector_db")
        mx = 0
        for root, _dirs, files in os.walk(vdb):
            for f in files:
                if f == "link_lists.bin":
                    mx = max(mx, os.path.getsize(os.path.join(root, f)))
        mb = mx // (1024 * 1024)
        if mb >= _LINK_LISTS_WARN_MB:
            logger.critical(f"[medlaw] link_lists.bin 폭주 의심: {mb}MB "
                            f"(임계 {_LINK_LISTS_WARN_MB}MB) — HNSW 손상 가능, 워치독/수동 점검 요망")
        return mb
    except Exception:  # noqa: BLE001
        return -1

# 노이즈(고시 본문 아님) 제목 키워드 — 보수적
_NOISE_TITLE = ("서식", "양식", "붙임", "검토의견")

# 첨부파일명 노이즈 — 검토의견서/검토서, 또는 (붙임N_)서식/양식으로 시작하는 양식류.
# 주의: "명세서서식" 등 본문 고시 제목에 '서식'이 포함될 수 있어 단순 부분일치 금지(오탐).
_NOISE_ATT_RE = re.compile(r"검토의견|검토서|^(?:붙임\d*[_\-\s]?)?(?:서식|양식)")


def _base_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_path() -> str:
    return os.path.join(_base_dir(), "khidi_state.json")


def _load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"last_no1": 0}


def _save_state(state: dict) -> None:
    # 원자적 쓰기(temp→replace) — 쓰기 중단 시 watermark 손상→전체 재크롤 방지
    try:
        p = _state_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[medlaw] state save fail: {e}")


def _tombstones() -> set:
    """dedup이 지운 source_file 집합 — 수집이 재색인하지 않도록 막는다.

    🔴 2026-09-03 라이브 확진: `known`은 **컬렉션 파생**이라 dedup이 지우면 그 자리가
    비고, 스케줄러가 KHIDI 최신 페이지에서 같은 첨부를 "신규"로 다시 받는다 —
    08-31에 지운 중복 4파일/139청크가 **이틀 만에 전량 재색인**됐다(count 13,388→13,579).
    삭제는 컬렉션에만 남기면 소실되는 사실이므로 state에 따로 기록한다.
    같은 게시글에 **다른 첨부**가 올라오면 fileId가 달라 safe_filename이 바뀌므로
    막히지 않는다(정당한 갱신은 통과).
    """
    return set(_load_state().get("dedup_tombstones") or [])


def _add_tombstones(source_files) -> None:
    state = _load_state()
    cur = set(state.get("dedup_tombstones") or [])
    cur.update(source_files)
    state["dedup_tombstones"] = sorted(cur)
    _save_state(state)


def is_relevant_title(title: str) -> bool:
    """서식/양식 등 고시 본문 아닌 항목 제외(보수적)."""
    return not any(k in (title or "") for k in _NOISE_TITLE)


def is_relevant_attachment(filename: str) -> bool:
    """첨부파일명 노이즈 판정 — 검토의견서/검토서·양식류 제외.
    게시물 제목은 정상이어도 첨부에 양식이 섞이는 케이스(라이브 9건 관찰) 차단."""
    return not _NOISE_ATT_RE.search(filename or "")


def _strip_id_prefix(source_file: str) -> str:
    """색인 source_file(`titleId-fileId-base.ext`) → base 파일명."""
    return re.sub(r"^\d+-\d+-", "", source_file or "")


def purge_noise(collection: str = MEDLAW_COLLECTION) -> dict:
    """기색인 노이즈(검토의견서/양식 첨부) 청크 제거 — 필터 강화 이전 유입분 정리."""
    from client.rag import get_collection
    col = get_collection(collection)
    noise_files: set = set()
    n_chunks = 0
    for m in col.get(include=["metadatas"])["metadatas"]:
        sf = m.get("source_file", "")
        if m.get("origin") == "khidi_notice" and not is_relevant_attachment(_strip_id_prefix(sf)):
            noise_files.add(sf)
            n_chunks += 1
    if n_chunks > _MAX_SAFE_DELETE_CHUNKS:  # 대량 삭제 가드 — col.delete 폭주 방지
        logger.critical(f"[medlaw] purge_noise SKIP: 삭제 대상 {n_chunks}청크 > 임계 "
                        f"{_MAX_SAFE_DELETE_CHUNKS} — col.delete 폭주 위험. 워치독 동반 수동 처리 요망.")
        return {"purged_files": 0, "purged_chunks": 0, "skipped_unsafe": n_chunks}
    for sf in noise_files:
        try:
            col.delete(where={"source_file": sf})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[medlaw] purge_noise delete {sf}: {e}")
    if noise_files:
        logger.info(f"[medlaw] purge_noise: {len(noise_files)}개 양식류 제거({n_chunks}청크)")
        _link_lists_warn()  # 삭제 후 폭주 조기 감지
    return {"purged_files": len(noise_files), "purged_chunks": n_chunks}


def _cid(source_file: str, seq: int) -> str:
    return "kn-" + hashlib.md5(f"{source_file}|{seq}".encode()).hexdigest()[:16]


# ── 버전 dedup: 같은 규정 다버전 중 전문(최대청크·최신titleId) 보존, 구버전 제거 ──
def _base_key(source_file: str) -> str:
    """⚠️ **더 이상 dedup 그룹핑에 쓰지 않는다**(2026-08-31). 마커 제거가 과했다.

    이 함수는 `전문|일부개정|개정|제정`과 고시번호·차수를 **전부 지운 뒤** 묶었기 때문에
    성격이 다른 문서를 한 그룹으로 만들었다. 실데이터 16그룹 판독 결과:
      - `의료급여수가` = 전문(2025-171호) + **일부개정(2026-138호)** 한 그룹
      - `건강보험 행위 급여` = 목록표(2024-280호) + **2025~2026 일부개정 8건** 한 그룹
    전문(통합본)과 일부개정(개정 diff)은 중복이 아니라 **상보 관계**이고, 일부개정은
    회차마다 내용이 다른 별개 문서다. 거기에 정렬 1순위가 청크 수라 **최신 개정분을 지우고
    구버전 전문을 남기는** 동작이었다(16그룹 중 9그룹에서 보존 대상이 최신본과 불일치).
    검색 실측(N=10·top-5)에서 삭제 대상이 근거의 32%였고, 그중 다수가 2026년 최신 개정분이었다.

    호환을 위해 남겨 두되 새 코드는 `_doc_identity`를 쓸 것.
    """
    s = re.sub(r"^\d+-\d+-", "", source_file)
    s = re.sub(r"\.(pdf|hwpx|hwp)$", "", s, flags=re.I)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"제?\s*\d{4}-\d+호", "", s)
    s = re.sub(r"\d+년\s*\d+차", "", s)
    s = re.sub(r"(전문|발령|일부개정|개정|최종|공고|안내서|민원인|_기준|호)", "", s)
    return re.sub(r"[\s_·().\-]+", "", s)


def _tid(source_file: str) -> int:
    m = re.match(r"(\d+)-", source_file)
    return int(m.group(1)) if m else 0


def _doc_identity(source_file: str) -> str:
    """source_file → **문서 동일성** 키. 같은 문서가 두 번 색인됐을 때만 일치한다.

    `_base_key`와 정반대 방향이다 — 지우는 것은 **색인 부수 정보뿐**이다:
      ① 앞머리 titleId 접두사(`529039-1-`) ② 확장자(pdf/hwpx/hwp) ③ 구분 기호·공백.
    성격 마커(전문·일부개정·제정)·고시번호·차수(26년3차)·날짜는 **남긴다** —
    그것들이 다르면 다른 문서이기 때문이다.

    ⇒ 구버전 전문(25년12차 vs 26년3차)은 **서로 다른 키**가 되어 보존된다(정책 결정
    2026-08-31: 과거 시점 기준을 묻는 질의가 있을 수 있으므로 구버전도 보존).
    """
    s = re.sub(r"^\d+-\d+-", "", source_file)
    s = re.sub(r"\.(pdf|hwpx|hwp)$", "", s, flags=re.I)
    return re.sub(r"[\s_·().\-]+", "", s).lower()


def dedup_versions(collection: str = MEDLAW_COLLECTION, apply: bool = False) -> dict:
    """khidi_notice 중 **같은 문서가 두 번 색인된 것**만 정리한다.

    🔑 2026-08-31 재설계 — 종전은 "같은 규정의 다버전 중 가장 긴 것만 보존"이었고,
    그것이 **최신 일부개정을 삭제**하고 있었다(위 `_base_key` docstring의 실측 참조).
    새 규칙은 두 조건을 **동시에** 만족할 때만 삭제 후보로 본다:
      ① `_doc_identity`가 같다(= 같은 문서·같은 고시번호·같은 차수·같은 성격)
      ② **청크 수가 같다** — 다르면 파싱본이 다르거나 동명이 다른 개정본이므로
         `ambiguous`로 분류하고 **계획에 넣지 않는다**(실데이터에서 hwpx/pdf 파싱 차이
         15 vs 11청크, 동명 초안 9 vs 3청크가 여기 걸린다).
    보존은 **최신 titleId**(청크 수가 아니다).

    `apply=False`(기본)면 **계획만 산출하고 삭제하지 않는다**. 증분 스케줄러는 기본값으로
    부르므로 라이브에서 자동 삭제가 일어나지 않는다 — 삭제는 운영자가 워치독을 켜고
    `scripts/dedup_medlaw.py --apply`로 명시 실행한다(ChromaDB `col.delete` 폭주 이력).
    """
    from collections import Counter, defaultdict
    from client.rag import get_collection
    col = get_collection(collection)
    sf_count: Counter = Counter()
    for m in col.get(include=["metadatas"])["metadatas"]:
        if m.get("origin") == "khidi_notice":
            sf_count[m["source_file"]] += 1
    groups: dict = defaultdict(list)
    for sf, n in sf_count.items():
        groups[_doc_identity(sf)].append((sf, n))

    plan: list = []       # (source_file, chunks) — 삭제 후보
    ambiguous: list = []  # 동일 문서명인데 청크 수가 달라 판단 보류
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda x: _tid(x[0]), reverse=True)   # 최신 titleId 보존
        keep_chunks = members[0][1]
        for sf, n in members[1:]:
            (plan if n == keep_chunks else ambiguous).append((sf, n))

    plan_chunks = sum(n for _sf, n in plan)
    result = {"planned_files": len(plan), "planned_chunks": plan_chunks,
              "ambiguous_files": len(ambiguous), "dry_run": not apply,
              "deduped_files": 0, "deduped_chunks": 0}
    if ambiguous:
        logger.info("[medlaw] dedup 보류(동명·청크수 상이) %d건: %s",
                    len(ambiguous), ", ".join(sf for sf, _ in ambiguous[:5]))
    if not apply:
        if plan_chunks:
            logger.info("[medlaw] dedup 계획만 산출(dry-run): %d파일 %d청크 — 삭제하려면 "
                        "워치독 동반 `scripts/dedup_medlaw.py --apply`", len(plan), plan_chunks)
        return result
    if plan_chunks > _MAX_SAFE_DELETE_CHUNKS:  # 대량 삭제 가드 — col.delete 폭주 방지
        logger.critical(f"[medlaw] dedup SKIP: 삭제 대상 {plan_chunks}청크 > 임계 "
                        f"{_MAX_SAFE_DELETE_CHUNKS} — col.delete 폭주 위험. 계획을 먼저 검토할 것"
                        f"(`scripts/dedup_medlaw.py`를 --apply 없이 실행하면 계획만 출력).")
        result["skipped_unsafe"] = plan_chunks
        return result
    deleted_files = deleted_chunks = 0
    deleted_sfs: list = []
    for sf, n in plan:
        try:
            col.delete(where={"source_file": sf})
            deleted_files += 1
            deleted_chunks += n
            deleted_sfs.append(sf)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[medlaw] dedup delete {sf}: {e}")
    if deleted_files:
        # 🔴 tombstone 기록 — 이게 없으면 스케줄러가 지운 파일을 이틀 안에 다시
        #    색인한다(2026-09-03 확진·`_tombstones` docstring). 삭제 성공분만 기록.
        _add_tombstones(deleted_sfs)
        logger.info(f"[medlaw] dedup: {deleted_files}개 중복 색인 제거({deleted_chunks}청크)")
        _link_lists_warn()  # 삭제 후 폭주 조기 감지
    result["deduped_files"] = deleted_files
    result["deduped_chunks"] = deleted_chunks
    return result


def index_files(file_paths: list[str], collection: str = MEDLAW_COLLECTION) -> dict:
    """문서 파일들 → 파싱→청크→bge-m3→ChromaDB 멱등 색인. 색인 결과 dict."""
    import re as _re
    from core.doc_parser import parse_document, chunk_notice, notice_metadata
    from client.rag import embed_text, get_collection

    col = get_collection(collection)
    ok = total = skipped = garbage = ocr_recovered = 0
    for p in file_paths:
        pd = parse_document(p)
        if not pd.ok:
            skipped += 1
            continue
        # 파싱 붕괴 가드(2026-09-03): 폰트 toUnicode 결손 PDF가 한글을 통째로 잃고도
        # 색인돼 쓰레기 청크가 검색을 오염시켰다(530076-1 실사고 — 한글 2.1%).
        # 임계 5%는 코퍼스 실측에서 옴: 정상 최저 11.9%(질병군 표) vs 붕괴 2.1%.
        # 500자 미만은 판정 보류(짧은 표지는 비율이 흔들린다). 무성 드롭 금지 — 보고+로그.
        text = pd.text
        parse_method = None
        if len(text) >= 500:
            ratio = len(_re.findall(r"[가-힣]", text)) / len(text)
            if ratio < 0.05:
                # OCR 폴백(v2.125.37) — 가드에 걸린 PDF를 코퍼스에서 잃지 않는다.
                # OCR 산출물도 같은 게이트(≥500자·한글≥5%)를 통과해야 색인된다
                # (쓰레기를 쓰레기로 대체하지 않는다).
                rec = None
                if p.lower().endswith(".pdf"):
                    try:
                        from core.medlaw_ocr import ocr_pdf_text
                        rec = ocr_pdf_text(p)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"[medlaw] OCR 폴백 예외 {os.path.basename(p)[:50]}: {e}")
                r2 = (len(_re.findall(r"[가-힣]", rec)) / len(rec)) if rec else 0.0
                if rec and len(rec) >= 500 and r2 >= 0.05:
                    logger.info(f"[medlaw] OCR 구제: 한글 {ratio:.1%}→{r2:.1%} "
                                f"len={len(rec)} — {os.path.basename(p)[:60]}")
                    text = rec
                    parse_method = "ocr"
                    ocr_recovered += 1
                else:
                    garbage += 1
                    logger.warning(f"[medlaw] skip(파싱 붕괴 의심): 한글 {ratio:.1%} "
                                   f"len={len(text)} OCR구제실패={rec is not None} — "
                                   f"{os.path.basename(p)[:60]}")
                    continue
        meta = notice_metadata(p, text)
        if parse_method:
            meta["parse_method"] = parse_method
        chunks = chunk_notice(text)
        try:
            col.delete(where={"source_file": meta["source_file"]})  # 멱등
        except Exception:  # noqa: BLE001
            pass
        for seq, ch in enumerate(chunks):
            total += 1
            try:
                emb = embed_text(ch, prefix="search_document")
                m = dict(meta); m["chunk_seq"] = seq
                col.upsert(ids=[_cid(meta["source_file"], seq)], embeddings=[emb],
                           documents=[ch], metadatas=[m])
                ok += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[medlaw] upsert {meta['source_file']}#{seq}: {e}")
    return {"ok": ok, "total": total, "skipped": skipped, "garbage_skipped": garbage,
            "ocr_recovered": ocr_recovered}


def collect_and_index_new(max_scan_pages: int = 5, dest_dir: str | None = None) -> dict:
    """신규 고시 수집·색인. **source_file(titleId-fileId) 기준 dedup** — 이미 색인된 고시는 skip.
    (no1은 phantom 행번호로 불안정 → watermark 비교 폐기. 신규 고시는 최신 페이지에 노출되므로
    max_scan_pages만 스캔하고, 컬렉션에 없는 source_file만 다운로드·색인.)"""
    import core.khidi_collector as khidi

    dest_dir = dest_dir or os.path.join(_base_dir(), "khidi_notices")
    # 이미 색인된 source_file 집합(증분 dedup 기준)
    try:
        from client.rag import get_collection
        col = get_collection(MEDLAW_COLLECTION)
        known = {m.get("source_file") for m in col.get(include=["metadatas"])["metadatas"]}
    except Exception:  # noqa: BLE001
        known = set()

    sess = khidi._session()
    tombstones = _tombstones()  # dedup 삭제분 — 재색인 차단(churn 봉쇄·2026-09-03)
    new_files: list[str] = []
    skipped_titles = skipped_atts = skipped_tombstones = 0  # 노이즈 제외 보고(silent drop 금지, 기획 §9-1)
    for page in range(1, max_scan_pages + 1):
        posts = khidi.fetch_list_page(page, sess=sess)
        for post in posts:
            time.sleep(khidi.DELAY_S)
            det = khidi.fetch_detail(post, sess=sess)
            if not is_relevant_title(det.title):
                skipped_titles += 1
                logger.info(f"[medlaw] skip title(노이즈): {det.title[:60]}")
                continue
            atts = [a for a in det.pdf_first() if is_relevant_attachment(a.filename)]
            n_noise = len(det.attachments) - len(atts)
            if n_noise:
                skipped_atts += n_noise
                logger.info(f"[medlaw] skip att(양식류) {n_noise}건: {det.title[:60]}")
            pdfs = [a for a in atts if a.ext == "pdf"]
            for att in (pdfs or atts[:1])[:1]:
                sf = khidi.safe_filename(att)
                if sf in known:
                    continue  # 이미 색인된 고시 — 재다운로드/재색인 안 함
                if sf in tombstones:
                    skipped_tombstones += 1
                    logger.info(f"[medlaw] skip att(dedup tombstone): {sf[:60]}")
                    continue  # dedup이 지운 중복 — known이 비어도 다시 받지 않는다
                time.sleep(khidi.DELAY_S)
                p = khidi.download_attachment(att, dest_dir, sess=sess)
                if p:
                    new_files.append(p)
        time.sleep(khidi.DELAY_S)

    result = {"new_files": len(new_files), "known_skipped": True,
              "noise_skipped_titles": skipped_titles, "noise_skipped_atts": skipped_atts,
              "tombstone_skipped": skipped_tombstones}
    if new_files:
        result.update(index_files(new_files))
        result.update(dedup_versions())  # 신규 색인 후 구버전 자동 정리(재블로트 방지)
        result.update(purge_noise())     # 필터 우회 유입분 정리(자가유지)
    # last_no1은 정보성으로만 보존(증분 판정엔 미사용)
    state = _load_state()
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    state["last_run_new"] = len(new_files)
    _save_state(state)
    logger.info(f"[medlaw] incremental: {result}")
    return result


def start_medlaw_scheduler(daily_hour: int = 5, interval_s: int = 3600) -> None:
    """증분 수집 스케줄러(daemon). 매일 daily_hour(UTC)에 신규 고시 수집·색인.
    config medlaw_scheduler_enabled=true일 때만 app.py에서 기동(백필 완료 후 활성화)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        last_day = None
        while True:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == daily_hour and last_day != now.date():
                    collect_and_index_new()
                    last_day = now.date()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[medlaw] scheduler error: {e}")
            time.sleep(interval_s)

    threading.Thread(target=_loop, name="medlaw-scheduler", daemon=True).start()
    logger.info(f"[medlaw] scheduler started (daily_hour={daily_hour} UTC)")
