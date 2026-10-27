"""식약처 DUR(의약품 안전사용서비스) 병용금기 — 정본 데이터 적재 + 체크.

식약처 공개 데이터(OpenData_PotOpenDur{Ingr,Item})를 dur.db에 적재하고, 처방 약물
리스트의 **병용금기(DDI)**를 권위 데이터로 점검한다(LLM 추측 아님).

- `dur_interaction`: 성분 pair 병용금기 룰(Ingr, ~1.7K) — **권위 룰셋**.
- `dur_product`: 품목 → DUR성분코드 색인(Item slim, dedup) — 제품 해석용.

⚠️ 정본은 식약처/심평원. 본 모듈은 **미러 + 자문 보조**이며 실시간 차단용이 아니다.
최종 판단은 처방의·약사. 연령/임부/용량/기간 금기는 별도 데이터셋(미적재) 영역.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUR_DB_PATH = os.path.join(BASE_DIR, "dur.db")

DUR_DISCLAIMER = (
    "정본은 식약처 DUR(의약품안전사용서비스)이며, 본 결과는 미러 데이터 기반 참고 자문입니다. "
    "최종 처방·조제 판단은 처방의·약사가 실시간 DUR과 환자 상태로 확정하세요."
)

_D000 = re.compile(r"^D\d{6}$")  # DUR성분코드 패턴 (예: D000353)

_local = threading.local()
_init_lock = threading.Lock()

_DDL = """
CREATE TABLE IF NOT EXISTS dur_interaction (
  a_code      TEXT,            -- DUR성분코드 A
  a_name      TEXT,            -- DUR성분명 A (한글)
  a_name_en   TEXT,
  b_code      TEXT,            -- 병용금기 DUR성분코드 B
  b_name      TEXT,
  b_name_en   TEXT,
  reason      TEXT,            -- 금기내용(사유)
  grade       TEXT,            -- 등급
  notice_date TEXT,            -- 고시일자
  dur_no      TEXT,            -- DUR일련번호
  PRIMARY KEY (a_code, b_code, dur_no)
);
CREATE INDEX IF NOT EXISTS idx_dur_a ON dur_interaction(a_code);
CREATE INDEX IF NOT EXISTS idx_dur_b ON dur_interaction(b_code);
CREATE INDEX IF NOT EXISTS idx_dur_aname ON dur_interaction(a_name);
CREATE INDEX IF NOT EXISTS idx_dur_bname ON dur_interaction(b_name);

CREATE TABLE IF NOT EXISTS dur_product (
  product_serial TEXT PRIMARY KEY,  -- 품목일련번호
  product_name   TEXT,              -- 품목명
  ingr_code      TEXT,              -- DUR성분코드
  ingredient     TEXT,              -- 주성분
  maker          TEXT
);
CREATE INDEX IF NOT EXISTS idx_durprod_ingr ON dur_product(ingr_code);
CREATE INDEX IF NOT EXISTS idx_durprod_name ON dur_product(product_name);

CREATE TABLE IF NOT EXISTS dur_single (
  dur_type    TEXT,             -- 연령금기/임부금기/용량주의/투여기간주의/노인주의/효능군중복
  dur_seq     TEXT,
  ingr_code   TEXT,             -- DUR성분코드(D000)
  ingr_name   TEXT,             -- 성분명(한글)
  ingr_eng    TEXT,
  class_name  TEXT,             -- 약효분류
  form_name   TEXT,
  prohbt_content TEXT,          -- 금기/주의 내용
  criteria    TEXT,             -- 유형별 기준값(연령 AGE_BASE/임부 GRADE/용량 MAX_QTY/기간 MAX_DOSAGE_TERM/효능군 SERS_NAME)
  effect_code TEXT,             -- 효능군중복 그룹키(EFFECT_CODE)
  notice_date TEXT,
  PRIMARY KEY (dur_type, dur_seq, ingr_code)
);
CREATE INDEX IF NOT EXISTS idx_dursingle_ingr ON dur_single(ingr_code);
CREATE INDEX IF NOT EXISTS idx_dursingle_type ON dur_single(dur_type);
CREATE INDEX IF NOT EXISTS idx_dursingle_effect ON dur_single(effect_code);

CREATE TABLE IF NOT EXISTS dur_split (
  item_seq    TEXT PRIMARY KEY,   -- 품목일련번호(서방정분할주의는 품목 단위)
  item_name   TEXT,               -- 제품명
  ingredient  TEXT,               -- 주성분
  content     TEXT,               -- 분할 주의 내용(예: 분할불가)
  maker       TEXT,
  form_name   TEXT                -- 제형
);
CREATE INDEX IF NOT EXISTS idx_dursplit_name ON dur_split(item_name);
"""

# Ingr(성분) xlsx 헤더 → 필드
INGR_HEADERS = {
    "DUR일련번호": "dur_no", "DUR유형": "dur_type",
    "DUR성분코드": "a_code", "DUR성분명": "a_name", "DUR성분명영문": "a_name_en",
    "금기내용": "reason", "등급": "grade", "고시일자": "notice_date",
    "병용금기DUR성분코드": "b_code", "병용금기DUR성분명": "b_name",
    "병용금기DUR성분영문명": "b_name_en",
}
# Item(품목) xlsx 헤더 → 필드 (슬림: 품목→성분코드 해석용만)
ITEM_HEADERS = {
    "품목일련번호": "product_serial", "품목명": "product_name",
    "DUR성분코드": "ingr_code", "주성분": "ingredient", "업소명": "maker",
}


def get_dur_db(db_path: str | None = None) -> sqlite3.Connection:
    """thread-local 연결 (WAL, busy_timeout 5000ms). 최초 호출 시 DDL 적용."""
    path = db_path or DUR_DB_PATH
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    with _init_lock:
        conn.executescript(_DDL)
        conn.commit()
    _local.conn = conn
    _local.path = path
    return conn


def _ensure(conn):
    conn.executescript(_DDL)
    conn.commit()


def import_interactions(records, conn=None) -> int:
    """Ingr 성분 pair 병용금기 룰 적재(upsert). 적재 건수 반환."""
    conn = conn or get_dur_db()
    _ensure(conn)
    n = 0
    for r in records:
        a, b = (r.get("a_code") or "").strip(), (r.get("b_code") or "").strip()
        if not a or not b:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO dur_interaction
               (a_code,a_name,a_name_en,b_code,b_name,b_name_en,reason,grade,notice_date,dur_no)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (a, (r.get("a_name") or "").strip(), (r.get("a_name_en") or "").strip(),
             b, (r.get("b_name") or "").strip(), (r.get("b_name_en") or "").strip(),
             (r.get("reason") or "").strip(), (r.get("grade") or "").strip(),
             (r.get("notice_date") or "").strip(), (r.get("dur_no") or "").strip()))
        n += 1
    conn.commit()
    return n


def import_products(records, conn=None) -> int:
    """Item 품목→DUR성분코드 색인 적재(품목일련번호 dedup, upsert). 적재 건수 반환."""
    conn = conn or get_dur_db()
    _ensure(conn)
    seen = set()
    n = 0
    for r in records:
        serial = (r.get("product_serial") or "").strip()
        ingr = (r.get("ingr_code") or "").strip()
        if not serial or not ingr or serial in seen:
            continue
        seen.add(serial)
        conn.execute(
            """INSERT OR REPLACE INTO dur_product
               (product_serial,product_name,ingr_code,ingredient,maker) VALUES (?,?,?,?,?)""",
            (serial, (r.get("product_name") or "").strip(), ingr,
             (r.get("ingredient") or "").strip(), (r.get("maker") or "").strip()))
        n += 1
        if n % 20000 == 0:
            conn.commit()
    conn.commit()
    return n


def import_interactions_api(records, conn=None, clear=False) -> int:
    """DUR 성분정보 API 병용금기(getUsjntTabooInfoList02) item → dur_interaction 적재."""
    conn = conn or get_dur_db()
    _ensure(conn)
    if clear:
        conn.execute("DELETE FROM dur_interaction")
    n = 0
    for r in records:
        a = (r.get("INGR_CODE") or "").strip()
        b = (r.get("MIXTURE_INGR_CODE") or "").strip()
        if not a or not b:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO dur_interaction
               (a_code,a_name,a_name_en,b_code,b_name,b_name_en,reason,grade,notice_date,dur_no)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (a, (r.get("INGR_NAME") or "").strip(), (r.get("INGR_ENG_NAME") or "").strip(),
             b, (r.get("MIXTURE_INGR_KOR_NAME") or r.get("MIXTURE_INGR_NAME") or "").strip(),
             (r.get("MIXTURE_INGR_ENG_NAME") or "").strip(),
             (r.get("PROHBT_CONTENT") or "").strip(), "",
             (r.get("NOTIFICATION_DATE") or "").strip(), (r.get("DUR_SEQ") or "").strip()))
        n += 1
    conn.commit()
    backfill_a_names(conn)  # a측 한글명 빈값(식약처 특성) → 크로스레프 백필
    return n


def backfill_a_names(conn=None) -> int:
    """`dur_interaction.a_name` 백필(멱등). 식약처 병용금기 API가 a측 한글명(INGR_NAME)을
    빈값으로 반환 → a_name 전량 빈값 → 한글 a측 성분 이름해석 불가. a_code의 한글명을
    ①b_name(b_code=a_code 크로스레프·동일 테이블) ②dur_single.ingr_name ③dur_product.ingredient
    순으로 찾아 **salt 제거 기본형**으로 채운다(일반명 exact 매칭 가능). 빈 a_name만 갱신."""
    conn = conn or get_dur_db()
    _ensure(conn)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT a_code FROM dur_interaction "
        "WHERE (a_name IS NULL OR a_name='') AND a_code<>''").fetchall()]
    n = 0
    for code in codes:
        src = None
        for q in ("SELECT b_name FROM dur_interaction WHERE b_code=? AND b_name<>'' LIMIT 1",
                  "SELECT ingr_name FROM dur_single WHERE ingr_code=? AND ingr_name<>'' LIMIT 1",
                  "SELECT ingredient FROM dur_product WHERE ingr_code=? AND ingredient<>'' LIMIT 1"):
            try:
                row = conn.execute(q, (code,)).fetchone()
            except sqlite3.OperationalError:
                row = None
            if row and row[0]:
                src = row[0]
                break
        if src:
            conn.execute(
                "UPDATE dur_interaction SET a_name=? WHERE a_code=? AND (a_name IS NULL OR a_name='')",
                (_strip_salt(src), code))
            n += 1
    conn.commit()
    return n


# 유형별 기준값 필드(criteria) — API extra 필드명
_CRITERIA_FIELD = {
    "연령금기": "AGE_BASE", "임부금기": "GRADE", "용량주의": "MAX_QTY",
    "투여기간주의": "MAX_DOSAGE_TERM", "효능군중복": "SERS_NAME",
}


def import_single(records, dur_type, conn=None, clear=False) -> int:
    """단일성분 금기/주의(연령·임부·용량·기간·노인·효능군중복) API item → dur_single 적재."""
    conn = conn or get_dur_db()
    _ensure(conn)
    if clear:
        conn.execute("DELETE FROM dur_single WHERE dur_type=?", (dur_type,))
    cfield = _CRITERIA_FIELD.get(dur_type)
    n = 0
    for r in records:
        ic = (r.get("INGR_CODE") or "").strip()
        seq = (r.get("DUR_SEQ") or "").strip()
        if not ic or not seq:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO dur_single
               (dur_type,dur_seq,ingr_code,ingr_name,ingr_eng,class_name,form_name,
                prohbt_content,criteria,effect_code,notice_date)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (dur_type, seq, ic, (r.get("INGR_NAME") or "").strip(),
             (r.get("INGR_ENG_NAME") or "").strip(), (r.get("CLASS_NAME") or "").strip(),
             (r.get("FORM_NAME") or "").strip(), (r.get("PROHBT_CONTENT") or "").strip(),
             (r.get(cfield) or "").strip() if cfield else "",
             (r.get("EFFECT_CODE") or "").strip(),
             (r.get("NOTIFICATION_DATE") or "").strip()))
        n += 1
    conn.commit()
    return n


def import_split(records, conn=None, clear=False) -> int:
    """서방정분할주의(품목 단위, getSeobangjeongPartitnAtentInfoList03) → dur_split 적재."""
    conn = conn or get_dur_db()
    _ensure(conn)
    if clear:
        conn.execute("DELETE FROM dur_split")
    n = 0
    for r in records:
        seq = (r.get("ITEM_SEQ") or "").strip()
        if not seq:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO dur_split
               (item_seq,item_name,ingredient,content,maker,form_name) VALUES (?,?,?,?,?,?)""",
            (seq, (r.get("ITEM_NAME") or "").strip(), (r.get("MAIN_INGR") or "").strip(),
             (r.get("PROHBT_CONTENT") or "").strip(), (r.get("ENTP_NAME") or "").strip(),
             (r.get("FORM_CODE_NAME") or "").strip()))
        n += 1
    conn.commit()
    return n


# 서방정(서방형) 제형 식별 토큰. dur_split은 전량 '서방정분할주의' 품목이므로,
# 입력 약물명이 서방정 형태를 명시할 때만 LIKE 부분매칭을 허용한다 — 짧은 성분명
# (예: "아스피린")이 "아스피린서방정" 등 특정 서방정 제품을 과매칭하는 false positive 방지.
_SR_MARKERS_KO = ("서방", "지속", "데포", "듀로", "오로스")
_SR_MARKERS_LATIN = re.compile(r"(?:^|[^a-z])(oros|cr|sr|er|xr|xl|pr|mr)(?:[^a-z]|$)", re.I)


def _looks_like_sr(name: str) -> bool:
    """약물명이 서방정(서방형) 제형을 명시하는가 — LIKE 부분매칭 허용 게이트."""
    if any(k in name for k in _SR_MARKERS_KO):
        return True
    return bool(_SR_MARKERS_LATIN.search(name))


# 성분 salt/수화물 접미. DUR 병용금기 정본은 b_name을 salt 정식명으로 저장하고
# (예: '셀레길린염산염') a_name은 전량 빈값(a측은 코드만) → 일반명('셀레길린') exact-match 실패.
# 접미 제거 + 접두 LIKE로 관대 해석해 임상 중요 병용금기 누락 방지.
_SALT_SUFFIXES = (
    "브롬화수소산염", "메탄술폰산염", "타르타르산염", "푸마르산염", "말레산염",
    "숙신산염", "주석산염", "구연산염", "메실산염", "베실산염", "토실산염",
    "아세트산염", "글루콘산염", "팔미트산염", "스테아르산염", "옥살산염",
    "염산염", "황산염", "인산염", "질산염", "요오드화물", "브롬화물",
    "수화물", "이수화물", "삼수화물", "일수화물", "무수물",
    "나트륨", "칼륨", "칼슘", "마그네슘",
)


def _strip_salt(name: str) -> str:
    """성분명에서 salt/수화물 접미를 제거한 기본 성분명. 이미 기본형이면 그대로."""
    n = (name or "").strip()
    for suf in _SALT_SUFFIXES:  # 긴 접미 우선(위 정렬)
        if n.endswith(suf) and len(n) > len(suf) + 1:
            return n[: -len(suf)].strip()
    return n


def _split_cautions(drugs, conn) -> list:
    """입력 약물(원문 제품명/품목코드)에서 서방정분할주의 매칭(품목 단위, 이름/품목코드)."""
    out, seen = [], set()
    for d in (drugs or []):
        name = d if isinstance(d, str) else (d.get("product_name") or d.get("name")
                                             or d.get("product_serial") or d.get("code") or "")
        name = str(name).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        row = conn.execute(
            "SELECT * FROM dur_split WHERE item_seq=? OR item_name=? LIMIT 1",
            (name, name)).fetchone()
        # LIKE 폴백은 입력명이 서방정 제형을 명시(서방/CR/OROS 등)할 때만 — 성분명 과매칭 방지.
        if not row and len(name) >= 3 and _looks_like_sr(name):
            row = conn.execute(
                "SELECT * FROM dur_split WHERE item_name LIKE ? LIMIT 1",
                (f"%{name}%",)).fetchone()
        if row:
            out.append({"item_seq": row["item_seq"], "item_name": row["item_name"],
                        "content": row["content"], "matched": name})
    return out


def _age_applies(age, age_base: str) -> bool:
    """환자 나이(세)가 연령금기 기준(AGE_BASE)에 해당하는가. 파싱 불가 시 보수적으로 True."""
    if age is None:
        return False
    s = (age_base or "").strip()
    m = re.search(r"(\d+)\s*세", s)
    months = re.search(r"(\d+)\s*개월", s)
    try:
        if months and ("이하" in s or "미만" in s):
            return age < 1                      # 개월 단위 = 영아 → 만1세 미만 보수
        if m:
            n = int(m.group(1))
            if "이하" in s:
                return age <= n
            if "미만" in s:
                return age < n
            if "이상" in s:
                return age >= n
            if "초과" in s:
                return age > n
            return age <= n                     # 단순 'N세' = 이하로 보수
        if "신생아" in s or "영아" in s:
            return age < 1
    except (TypeError, ValueError):
        pass
    return True                                  # 미파싱 = 안전상 포함(주의 표기)


def _single_warnings(codes, age, pregnant, conn) -> dict:
    """해석된 성분코드 집합 → 단일성분 금기/주의 분류(환자 컨텍스트 반영)."""
    out = {"pregnancy_warnings": [], "elderly_warnings": [], "age_warnings": [],
           "advisories": [], "duplicate_therapy": []}
    if not codes:
        return out
    ph = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT * FROM dur_single WHERE ingr_code IN ({ph})", tuple(codes)).fetchall()
    eff = {}  # effect_code → [성분]
    for r in rows:
        item = {"ingr_code": r["ingr_code"], "ingr_name": r["ingr_name"],
                "dur_type": r["dur_type"], "content": r["prohbt_content"],
                "criteria": r["criteria"]}
        t = r["dur_type"]
        if t == "임부금기":
            if pregnant:
                out["pregnancy_warnings"].append(item)
        elif t == "노인주의":
            if age is not None and age >= 65:
                out["elderly_warnings"].append(item)
        elif t == "연령금기":
            if age is not None and _age_applies(age, r["criteria"]):
                out["age_warnings"].append(item)
        elif t in ("용량주의", "투여기간주의"):
            out["advisories"].append(item)        # 정보성(처방 용량/기간 미수신)
        elif t == "효능군중복":
            ec = (r["effect_code"] or "").strip()
            if ec:
                eff.setdefault(ec, []).append(item)
    # 효능군중복: 같은 효능군에 2개 이상 성분이 처방되면 중복
    for ec, members in eff.items():
        if len({m["ingr_code"] for m in members}) >= 2:
            out["duplicate_therapy"].append({"effect_code": ec,
                                             "sers_name": members[0]["criteria"],
                                             "ingredients": members})
    return out


def _name_for_code(code, conn) -> str:
    row = conn.execute(
        "SELECT a_name n FROM dur_interaction WHERE a_code=? LIMIT 1", (code,)).fetchone()
    if row and row["n"]:
        return row["n"]
    row = conn.execute(
        "SELECT b_name n FROM dur_interaction WHERE b_code=? LIMIT 1", (code,)).fetchone()
    if row and row["n"]:
        return row["n"]
    row = conn.execute(
        "SELECT ingredient n FROM dur_product WHERE ingr_code=? AND ingredient<>'' LIMIT 1",
        (code,)).fetchone()
    return row["n"] if row and row["n"] else code


def _resolve_one(item, conn) -> dict:
    """입력 약물 → DUR성분코드 집합. item=문자열 또는 dict.

    우선순위: ingredient_code(D000) > product_serial/code > product_name > name(성분명).
    """
    if isinstance(item, str):
        item = {"ingredient_code": item} if _D000.match(item.strip()) else {"name": item}
    codes, via = set(), None

    ic = (item.get("ingredient_code") or "").strip()
    if ic and _D000.match(ic):
        codes.add(ic)
        via = "ingredient_code"

    if not codes:
        serial = (item.get("product_serial") or item.get("code") or "").strip()
        if serial:
            r = conn.execute("SELECT ingr_code FROM dur_product WHERE product_serial=?",
                             (serial,)).fetchone()
            if r:
                codes.add(r["ingr_code"])
                via = "product_serial"

    if not codes:
        pn = (item.get("product_name") or "").strip()
        if pn:
            for r in conn.execute(
                    "SELECT DISTINCT ingr_code FROM dur_product WHERE product_name=?",
                    (pn,)).fetchall():
                codes.add(r["ingr_code"])
            if codes:
                via = "product_name"

    if not codes:
        nm = (item.get("name") or item.get("ingredient") or "").strip()
        if nm:
            # 후보 성분명 = 원문 + salt 접미 제거형(양방향 매칭). dur_interaction.a_name은
            # 전량 빈값이라 실효 매칭은 b_name/dur_single에서 발생.
            cands = [nm]
            base = _strip_salt(nm)
            if base and base != nm:
                cands.append(base)
            # 1) exact — a_name(빈값)/b_name/dur_single, 원문 및 salt 제거형
            for c in cands:
                for r in conn.execute(
                        "SELECT a_code c FROM dur_interaction WHERE a_name=? OR a_name_en=?",
                        (c, c)).fetchall():
                    codes.add(r["c"])
                for r in conn.execute(
                        "SELECT b_code c FROM dur_interaction WHERE b_name=? OR b_name_en=?",
                        (c, c)).fetchall():
                    codes.add(r["c"])
                for r in conn.execute(
                        "SELECT DISTINCT ingr_code c FROM dur_single WHERE ingr_name=? OR ingr_eng=?",
                        (c, c)).fetchall():
                    codes.add(r["c"])
            # 2) salt-suffix 관대 매칭 — exact 실패 & 입력 충분히 길면 접두 LIKE
            #    ('셀레길린'→저장 '셀레길린염산염' 매칭). 3자 미만은 과매칭 방지로 제외.
            if not codes and len(base) >= 3:
                like = base + "%"
                for r in conn.execute(
                        "SELECT b_code c FROM dur_interaction WHERE b_name LIKE ?", (like,)).fetchall():
                    codes.add(r["c"])
                for r in conn.execute(
                        "SELECT DISTINCT ingr_code c FROM dur_single WHERE ingr_name LIKE ?",
                        (like,)).fetchall():
                    codes.add(r["c"])
            if codes:
                via = "name"

    names = sorted({_name_for_code(c, conn) for c in codes})
    return {"input": item, "codes": sorted(codes), "names": names,
            "via": via, "resolved": bool(codes)}


def check(drugs, conn=None, age=None, pregnant=None) -> dict:
    """처방 약물 리스트의 DUR 점검. drugs=[문자열|dict] 비식별(코드/성분만).

    병용금기(쌍 대조) + 환자 컨텍스트 단일성분 금기/주의(임부·연령·노인·용량·기간·효능군중복).
    age(세)·pregnant(bool)는 비식별 컨텍스트(선택). 미제공 시 해당 유형은 점검 생략.
    """
    conn = conn or get_dur_db()
    _ensure(conn)
    try:
        age = int(age) if age is not None else None
    except (TypeError, ValueError):
        age = None
    resolved = [_resolve_one(d, conn) for d in (drugs or [])]
    alerts, seen = [], set()
    for i in range(len(resolved)):
        for j in range(i + 1, len(resolved)):
            for ca in resolved[i]["codes"]:
                for cb in resolved[j]["codes"]:
                    if ca == cb:
                        continue
                    row = conn.execute(
                        """SELECT * FROM dur_interaction
                           WHERE (a_code=? AND b_code=?) OR (a_code=? AND b_code=?) LIMIT 1""",
                        (ca, cb, cb, ca)).fetchone()
                    if not row:
                        continue
                    key = tuple(sorted((ca, cb)))
                    if key in seen:
                        continue
                    seen.add(key)
                    alerts.append({
                        "drug_a": row["a_name"], "drug_b": row["b_name"],
                        "a_code": row["a_code"], "b_code": row["b_code"],
                        "reason": row["reason"], "grade": row["grade"],
                        "notice_date": row["notice_date"],
                        "input_index": [i, j],
                    })
    # 단일성분 금기/주의(환자 컨텍스트 반영)
    all_codes = {c for r in resolved for c in r["codes"]}
    single = _single_warnings(all_codes, age, pregnant, conn)
    # 서방정분할주의(품목 단위 — 원문 제품명/품목코드 매칭)
    split = _split_cautions(drugs, conn)
    warn_total = (len(single["pregnancy_warnings"]) + len(single["elderly_warnings"])
                  + len(single["age_warnings"]) + len(single["duplicate_therapy"]) + len(split))
    return {
        "input_count": len(resolved),
        "resolved": resolved,
        "patient_context": {"age": age, "pregnant": bool(pregnant) if pregnant is not None else None},
        "contraindications": alerts,
        "count": len(alerts),
        "has_contraindication": bool(alerts),
        "pregnancy_warnings": single["pregnancy_warnings"],
        "elderly_warnings": single["elderly_warnings"],
        "age_warnings": single["age_warnings"],
        "duplicate_therapy": single["duplicate_therapy"],
        "advisories": single["advisories"],
        "split_cautions": split,
        "has_warning": bool(alerts) or warn_total > 0,
        "dur_types_checked": ["병용금기", "임부금기", "연령금기", "노인주의", "용량주의",
                              "투여기간주의", "효능군중복", "서방정분할주의"],
        "disclaimer": DUR_DISCLAIMER,
    }


def drug_profile(drug, conn=None, limit: int = 50) -> dict:
    """단일 약물 → DUR성분코드 + 병용금기 상대 성분 목록."""
    conn = conn or get_dur_db()
    _ensure(conn)
    res = _resolve_one(drug, conn)
    partners, seen = [], set()
    for code in res["codes"]:
        for r in conn.execute(
                """SELECT b_code code,b_name name,reason,grade,notice_date FROM dur_interaction WHERE a_code=?
                   UNION SELECT a_code,a_name,reason,grade,notice_date FROM dur_interaction WHERE b_code=?
                   LIMIT ?""", (code, code, limit)).fetchall():
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            partners.append({"code": r["code"], "name": r["name"],
                             "reason": r["reason"], "grade": r["grade"],
                             "notice_date": r["notice_date"]})
    return {**res, "contraindicated_with": partners,
            "contraindicated_count": len(partners), "disclaimer": DUR_DISCLAIMER}


def stats(conn=None) -> dict:
    conn = conn or get_dur_db()
    _ensure(conn)
    inter = conn.execute("SELECT count(*) FROM dur_interaction").fetchone()[0]
    prods = conn.execute("SELECT count(*) FROM dur_product").fetchone()[0]
    ingr = conn.execute("SELECT count(DISTINCT a_code) FROM dur_interaction").fetchone()[0]
    single = {r["dur_type"]: r["c"] for r in conn.execute(
        "SELECT dur_type, count(*) c FROM dur_single GROUP BY dur_type").fetchall()}
    split = conn.execute("SELECT count(*) FROM dur_split").fetchone()[0]
    if split:
        single["서방정분할주의"] = split
    return {"interactions": inter, "products": prods,
            "distinct_ingredients": ingr, "single_warnings": single,
            "dur_types": ["병용금기"] + list(single.keys())}
