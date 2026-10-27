#!/usr/bin/env python3
"""RAG 답변 충실도 배치 측정 (LLM 평가 Phase 2 러너).

- --controls : 통제 페어로 judge 변별력 검증(P0서 HHEM이 실패한 페어 포함) — 수용 게이트
- 기본       : 의료법 RAG 실질의 → /api/medical-law/ask 답변 + client.rag 근거 → 충실도 채점
- 결과 → eval.db `faithfulness_results` (환각률 = 안전 KPI, Phase 4 추세 합류용)
- judge = 상주 qwen2.5:14b(스왑 0) · 외부 API 0

사용법:
  scripts/eval_faithfulness.py --controls
  scripts/eval_faithfulness.py                      # 기본 의료법 질의 세트
  scripts/eval_faithfulness.py --questions q.txt    # 줄단위 질의 파일
"""
import argparse, json, os, sqlite3, sys, time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import faithfulness as F

API = os.environ.get("ADMIN_API", "http://127.0.0.1:8585")
EVAL_DB = os.environ.get("EVAL_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval.db"))

# v2.122.0: 질의는 config/eval_questions.json(core 고정+pool 일자 회전, core.eval_pool)
# — 기존 5문항은 core로 유지(추세 연속성). 아래는 최후 폴백용.
from core.eval_pool import load_pool, questions_for_day  # noqa: E402

DEFAULT_QUESTIONS = load_pool()["core"]

# P0서 HHEM이 실패한 통제 페어(한국어 변별) 포함 — judge 수용 게이트
CONTROLS = [
    ("와파린과 아스피린 병용은 출혈 위험을 높인다.",
     "와파린과 아스피린을 같이 복용하면 출혈 위험이 증가한다.", False),
    ("와파린과 아스피린 병용은 출혈 위험을 높인다.",
     "와파린은 혈압을 낮추는 고혈압 치료제이다.", True),
    ("의료법 시행규칙 제15조에 따라 진료기록부는 10년간 보존한다.",
     "진료기록부 보존기간은 10년이다.", False),
    ("의료법 시행규칙 제15조에 따라 진료기록부는 10년간 보존한다.",
     "진료기록부는 3년만 보관하면 된다.", True),
]


def init_db():
    c = sqlite3.connect(EVAL_DB, timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS faithfulness_results(
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, question TEXT,
        n_claims INTEGER, n_supported INTEGER, n_unsupported INTEGER,
        n_contradicted INTEGER, faithfulness REAL, hallucination INTEGER,
        judge_model TEXT, sec REAL)""")
    # v2.120.0 MiniCheck 교차검증 + v2.121.0 상세 영속 + v2.124.0 retrieval 품질
    for col, typ in (("mc_agree", "REAL"), ("mc_conflicts", "INTEGER"),
                     ("strict_faithfulness", "REAL"),
                     ("answer", "TEXT"), ("detail", "TEXT"),
                     ("ctx_precision", "REAL"), ("ctx_relevant", "INTEGER"),
                     ("ctx_chunks", "INTEGER"), ("ctx_sufficiency", "REAL")):
        try:
            c.execute(f"ALTER TABLE faithfulness_results ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    c.commit()
    return c


def run_controls():
    print("=== 통제 페어 (judge 변별력 검증, P0 HHEM 실패 페어 포함) ===")
    ok = 0
    for prem, hyp, expect_halluc in CONTROLS:
        r = F.score(hyp, prem)
        got = r["hallucination"]
        mark = "OK " if got == expect_halluc else "FAIL"
        if got == expect_halluc:
            ok += 1
        print("  [%s] faith=%s halluc=%s (기대 %s)  %s"
              % (mark, r["faithfulness"], got, expect_halluc, hyp[:40]))
    print("→ %d/%d 정확 (HHEM P0: 2/4)" % (ok, len(CONTROLS)))
    return ok == len(CONTROLS)


def get_evidence_chunks(question, top_k=5):
    """/ask 실제 컨텍스트와 정합: ①RAG 본코퍼스+발간물 양 컬렉션 + ②시드 조문(law_articles).
    한쪽만 쓰면 그 출처 주장이 unsupported 오판(방법론 갭 — 발간물 v2.113 봉합·시드 조문 본 fix).
    시드 쿼리는 routes/medical_law._ask_assemble과 동일(content LIKE question[:20]).
    반환=청크 리스트(v2.124.0 retrieval 품질 측정이 청크 단위 판정에 사용).

    ⚠️컬렉션 블록은 일과성 실패(임베딩 콜드 로드 timeout 등)에 1회 재시도 후 실패를
    로그로 남긴다 — 07-27 배치 첫 문항(id360) 실측: 본 코퍼스 질의가 무성(except pass)
    실패해 근거가 발간물 4청크만 남았고, judge가 정확한 답변(제15조 보존기간 표)을
    근거 부재로 contradicted 판정 = 배치 위양성으로 지수 하락. 실패는 판정 결과가
    아니라 측정 불능이므로 최소한 로그에 보여야 드릴다운에서 즉시 분류 가능."""
    from client.rag import search as rag_search
    texts = []
    # 법령 컬렉션은 floor 0.2 — 자연어 질문의 법령 유사도가 낮아(제21조 0.234 실측)
    # 0.3 게이트가 정답 조문을 잘라냄(/ask의 rank-기반 폴백과 정합)
    for col, ms in (("medical_law_knowledge", 0.2), ("medical_pub_knowledge", 0.3)):
        for attempt in (1, 2):
            try:
                # 본 코퍼스는 /ask와 동일하게 과다 인출→동일 원문 dedup→top_k 절단
                # (고시 판본 간 중복이 슬롯을 잠식. 검색 변경은 생성/판정 양쪽 동시 적용 필수)
                if col == "medical_law_knowledge":
                    from routes.medical_law import _OVERFETCH
                    raw = rag_search(question, n_results=top_k * _OVERFETCH,
                                     collection_name=col, min_score=ms)
                    hits, seen_t = [], set()
                    for h in raw:
                        t = (h.get("text") or "").strip()
                        if t and t in seen_t:
                            continue
                        seen_t.add(t)
                        hits.append(h)
                    hits = hits[:top_k]
                else:
                    hits = rag_search(question, n_results=top_k, collection_name=col, min_score=ms)
                # 결정적 질의 확장(v2.124.7) — /ask(_rag_law_search)와 동일 적용. 생성만
                # 확장하면 judge가 정답 인용을 unsupported로 오판(생성/판정 근거 비대칭의
                # 역방향, id175 재측정 faith 0.000 실측) → 본 코퍼스에 확장 질의 병합.
                if col == "medical_law_knowledge":
                    try:
                        from routes.medical_law import _query_expansions
                        seen = {h.get("id") for h in hits}
                        for exp_q in _query_expansions(question):
                            extra = rag_search(exp_q, n_results=3, collection_name=col,
                                               min_score=ms)
                            hits += [h for h in extra if h.get("id") not in seen][:2]
                            seen |= {h.get("id") for h in extra}
                    except Exception:
                        pass
                    # 벌칙 역참조(v2.124.8) — /ask(_rag_law_search)와 동일 적용.
                    # 검색 변경은 생성/판정 양쪽 동시 적용 필수(비대칭 시 judge 오판).
                    try:
                        from routes.medical_law import _penalty_backref_chunks
                        seen = {h.get("id") for h in hits}
                        hits += [h for h in _penalty_backref_chunks(question, hits)
                                 if h.get("id") not in seen]
                    except Exception:
                        pass
                    # 무관·항 불일치 벌칙 근거 제외(v2.124.94) — 같은 규율로 양쪽 동시 적용
                    try:
                        from routes.medical_law import (_drop_paragraph_mismatch,
                                                        _drop_unrelated_penalty)
                        hits = _drop_unrelated_penalty(hits)
                        hits = _drop_paragraph_mismatch(hits, question)
                    except Exception:
                        pass
                from routes.medical_law import _is_form_chunk  # 서식 청크 억제(id95 오염 봉합)
                # 🔑 법령명 라벨 — `/ask` 생성 컨텍스트와 **같은 형식**으로 붙인다.
                #    종전엔 `text`만 담아 메타데이터를 버렸고, 그래서 근거에 "의료법"이라는
                #    문자열이 아예 없었다(실측 15/40 — 시드 조문만 실었다). 그 상태에서
                #    법령명 오귀속 축을 켜자 정확한 인용이 오귀속으로 잡혔다(22/40 위양성).
                #    ⚠️ 발간물은 `law_name`이 **문서 제목**이라 법령명으로 오인되면
                #    v2.124.8 오귀속이 재발한다 — `/ask`와 같은 문구로 성격을 명시한다.
                for h in hits:
                    body = h.get("text") or ""
                    if _is_form_chunk(body):
                        continue
                    m = h.get("metadata") or {}
                    if col == "medical_pub_knowledge":
                        label = ("[발간물(%s) 「%s」 — 문서 제목이며 법령명 아님]"
                                 % (m.get("doc_type") or "", m.get("law_name") or ""))
                    else:
                        label = ("[%s %s %s(%s)]"
                                 % (m.get("law_name") or "", m.get("law_level") or "",
                                    m.get("article_number") or m.get("doc_no") or "",
                                    m.get("article_title") or ""))
                    texts.append("%s\n%s" % (label, body))
                break
            except Exception as e:  # noqa: BLE001 — 일과성(임베딩 콜드 timeout 등)
                if attempt == 1:
                    print(f"[evidence] {col} 검색 실패(1차) → 재시도: {e}", file=sys.stderr)
                    time.sleep(3)
                else:
                    print(f"[evidence] {col} 검색 실패(재시도 소진) — 이 문항 근거에서 "
                          f"제외됨(판정 오염 주의): {e}", file=sys.stderr)
    # 시드 조문 — /ask가 컨텍스트에 넣는 law_articles(SQLite)도 채점 근거에 포함
    try:
        import sqlite3
        db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "medical_law.db")
        conn = sqlite3.connect(db, timeout=5)
        rows = conn.execute(
            "SELECT law_name, article_number, article_title, content, penalty FROM law_articles "
            "WHERE content LIKE ? LIMIT 5", (f"%{question[:20]}%",)).fetchall()
        for law, art, title, content, penalty in rows:
            texts.append("%s %s (%s): %s%s" % (law, art, title, content,
                                               (" 벌칙: " + penalty) if penalty else ""))
        conn.close()
    except Exception:
        pass
    return [t for t in texts if t]


def get_evidence(question, top_k=5):
    """청크 조인 텍스트(충실도 채점용) — get_evidence_chunks 단일 소스."""
    return "\n".join(get_evidence_chunks(question, top_k=top_k))


#: 근거 발췌 상한. 채점은 **전문**으로 하고 여기 남기는 건 사후 검토용 사본이다.
#: 🐞 2026-08-05: 상한이 4,000자였는데 실제 근거는 5,000~7,700자였다. 드릴다운에서
#: 이 발췌를 근거 전체로 알고 대조한 결과 "근거에 없는 벌칙 수치 5/10"이라는 **허상**을
#: 얻어, 발동 0회짜리 처치를 만들었다가 철회했다. 잘린 사실이 보이지 않는 것이 문제였다.
_EXCERPT_MAX = 20000


def _excerpt(ev):
    """근거 사본 — 자를 때는 **잘렸다고 말한다**(무성 절단 금지)."""
    ev = ev or ""
    if len(ev) <= _EXCERPT_MAX:
        return ev
    return ev[:_EXCERPT_MAX] + ("\n\n[발췌 절단: 원본 %d자 중 %d자만 저장 — "
                                "채점은 전문으로 수행됨]" % (len(ev), _EXCERPT_MAX))


def _prob_summary(r: dict):
    """판정 확률 집계만 뽑는다. 미측정(교차검증 off·확률 미지원)이면 **None**.

    🔑 0으로 채우지 않는다 — 결측을 값으로 세면 축이 깨끗해 보인다(v2.125.9).
    """
    if r.get("entail_prob_n") is None:
        return None
    return {k[len("entail_prob_"):] or "n": v
            for k, v in r.items() if k.startswith("entail_prob_")}

def run_real(questions, cross=True):
    db = init_db()
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    scores, hallucs, pending = [], 0, []
    print("=== 의료법 RAG 답변 충실도 (judge=%s) ===" % F.JUDGE_MODEL)
    # 1단계: 전 질의 judge(상주 모델) — minicheck는 2단계로 몰아 모델 스왑 1회로 배칭
    for q in questions:
        t0 = time.time()
        try:
            ans = requests.post(API + "/api/medical-law/ask",
                                json={"question": q}, timeout=180).json().get("answer", "")
            chunks = get_evidence_chunks(q)
            ev = "\n".join(chunks)
        except Exception as e:
            print("  스킵(%s): %s" % (q, e))
            continue
        if not ans or not ev:
            print("  스킵(빈 답변/근거): %s" % q)
            continue
        r = F.score(ans, ev)
        # v2.124.0 retrieval 품질 — 검색 자체 측정(judge=상주 모델, 스왑 0). 실패=비차단
        try:
            from core import retrieval_eval as R
            r["retrieval"] = R.score(q, chunks)
        except Exception as e:  # noqa: BLE001
            print("  (retrieval 측정 생략: %s)" % str(e)[:80])
            r["retrieval"] = None
        pending.append({"q": q, "ans": ans, "ev": ev, "r": r, "sec": time.time() - t0})

    # 2단계: MiniCheck 교차검증(한국어 변별 PoC 10/10 GO) — 모델 미설치/실패는 생략(비차단)
    if cross:
        for p in pending:
            F.cross_check(p["r"], p["ev"])

    for p in pending:
        q, r, sec = p["q"], p["r"], p["sec"]
        ret = r.get("retrieval") or {}
        # 상세 영속 — 드릴다운 검토용(주장별 판정+교차검증·근거 발췌·청크별 관련성)
        # `law_misattributed` = 법령명 오귀속(v2.125.8) — **충실도 judge가 원리적으로
        # 못 보는 축**이다(주장의 내용만 근거와 대조하고 어느 법률의 조문인가는 안 본다).
        # 여기 안 담으면 score()가 계산해도 배치에 남지 않는다.
        detail = json.dumps({"claims": r.get("claims") or [],
                             "law_misattributed": r.get("law_misattributed") or [],
                             "proviso_misread": r.get("proviso_misread") or [],
                             # P6-1 Phase 0 — 판정 확률 집계. 교차검증이 꺼졌거나 백엔드가
                             # 확률을 못 주면 **키 자체가 없다**(미측정과 0을 가른다).
                             "entail_prob": _prob_summary(r),
                             "evidence_excerpt": _excerpt(p["ev"]),
                             "retrieval_chunks": ret.get("chunks") or []}, ensure_ascii=False)
        db.execute("INSERT INTO faithfulness_results(ts,source,question,n_claims,n_supported,"
                   "n_unsupported,n_contradicted,faithfulness,hallucination,judge_model,sec,"
                   "mc_agree,mc_conflicts,strict_faithfulness,answer,detail,"
                   "ctx_precision,ctx_relevant,ctx_chunks,ctx_sufficiency)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ts, "medical_law", q, r["n_claims"], r["n_supported"], r["n_unsupported"],
                    r["n_contradicted"], r["faithfulness"], int(r["hallucination"]),
                    F.JUDGE_MODEL, round(sec, 1),
                    r.get("mc_agree"), r.get("mc_conflicts"), r.get("strict_faithfulness"),
                    p["ans"], detail,
                    ret.get("ctx_precision"), ret.get("ctx_relevant"),
                    ret.get("ctx_chunks"), ret.get("ctx_sufficiency")))
        db.commit()
        if r["faithfulness"] is not None:
            scores.append(r["faithfulness"])
        hallucs += int(r["hallucination"])
        mc = (" mc_agree=%s conflicts=%s" % (r["mc_agree"], r["mc_conflicts"])
              if r.get("mc_agree") is not None else "")
        rx = (" ctx_p=%s suff=%s" % (ret.get("ctx_precision"), ret.get("ctx_sufficiency"))
              if ret.get("ctx_precision") is not None else "")
        print("  faith=%.3f halluc=%s claims=%d(s%d/u%d/c%d)%s%s %.0fs  Q=%s"
              % (r["faithfulness"] or 0, r["hallucination"], r["n_claims"],
                 r["n_supported"], r["n_unsupported"], r["n_contradicted"], mc, rx, sec, q))
    if scores:
        print("\n평균 충실도=%.3f · 환각률=%d/%d → eval.db 저장" % (sum(scores) / len(scores), hallucs, len(scores)))


EXT_PREFIX = "ext:"          # 외부 제출 표식 — 자체 배치 추세·품질 지수를 오염시키지 않는다


def run_pairs(records, source="external", cross=True):
    """외부 호출자가 만든 (질문·답변·근거) 쌍을 채점 — 생성 단계를 건너뛴다.

    자체 질의 세트는 답을 우리가 만들지만(run_real), Cerno처럼 자기 파이프라인으로 답을
    만드는 호출자는 그 답 그대로 채점해야 의미가 있다. `source`로 구분 적재해
    `/api/eval/status?source=`로 따로 조회한다.

    ⚠️ 검색 품질(ctx_precision 등)은 **측정하지 않는다** — 근거를 우리가 뽑은 게 아니라
    받은 것이라, 우리 검색을 채점하는 것이 아니기 때문이다(빈 값으로 남긴다).
    """
    db = init_db()
    ts = datetime.now(timezone.utc).isoformat() + "Z"
    # 🔑 외부 제출은 `ext:` 접두로 적재한다. 안 붙이면 최신 배치(MAX(ts))를 외부 제출이
    # 차지해 **우리 대시보드·품질 지수가 남의 답변으로 바뀐다**(2026-07-30 라이브서 실측:
    # 테스트 2건 넣자 지수 83.4 → 79.2).
    if not source.startswith(EXT_PREFIX):
        source = EXT_PREFIX + source
    scores, hallucs, pending = [], 0, []
    print("=== 외부 답변 충실도 source=%s n=%d (judge=%s) ===" % (source, len(records), F.JUDGE_MODEL))
    for rec in records:
        t0 = time.time()
        try:
            r = F.score(rec["answer"], rec["evidence"])
        except Exception as e:  # noqa: BLE001 — 한 건 실패가 배치를 죽이지 않는다
            print("  스킵(%s): %s" % (rec["id"], str(e)[:80]))
            continue
        pending.append({"rec": rec, "r": r, "sec": time.time() - t0})

    if cross:                       # 모델 스왑 1회로 배칭(run_real과 동일 순서)
        for p in pending:
            F.cross_check(p["r"], p["rec"]["evidence"])

    for p in pending:
        rec, r, sec = p["rec"], p["r"], p["sec"]
        detail = json.dumps({"claims": r.get("claims") or [],
                             "law_misattributed": r.get("law_misattributed") or [],
                             "proviso_misread": r.get("proviso_misread") or [],
                             "entail_prob": _prob_summary(r),
                             "evidence_excerpt": _excerpt(rec["evidence"]),
                             "external_id": rec["id"], "meta": rec.get("meta")},
                            ensure_ascii=False)
        db.execute("INSERT INTO faithfulness_results(ts,source,question,n_claims,n_supported,"
                   "n_unsupported,n_contradicted,faithfulness,hallucination,judge_model,sec,"
                   "mc_agree,mc_conflicts,strict_faithfulness,answer,detail)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (ts, source, rec["question"], r["n_claims"], r["n_supported"],
                    r["n_unsupported"], r["n_contradicted"], r["faithfulness"],
                    int(r["hallucination"]), F.JUDGE_MODEL, round(sec, 1),
                    r.get("mc_agree"), r.get("mc_conflicts"), r.get("strict_faithfulness"),
                    rec["answer"], detail))
        db.commit()
        if r["faithfulness"] is not None:
            scores.append(r["faithfulness"])
        hallucs += int(r["hallucination"])
        print("  faith=%.3f halluc=%s claims=%d %.0fs  id=%s"
              % (r["faithfulness"] or 0, r["hallucination"], r["n_claims"], sec, rec["id"]))
    if scores:
        print("\n평균 충실도=%.3f · 환각 플래그=%d/%d → eval.db(source=%s)"
              % (sum(scores) / len(scores), hallucs, len(scores), source))
    return len(scores)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--controls", action="store_true")
    ap.add_argument("--pairs", default="",
                    help="외부 답변 NDJSON(질문·답변·근거) 채점 — 생성 생략")
    ap.add_argument("--source", default="external", help="--pairs 적재 시 source 라벨(예: cerno)")
    ap.add_argument("--spool", action="store_true",
                    help="eval_spool/*.jsonl 일괄 채점(제출 엔드포인트 수거) 후 .done 처리")
    ap.add_argument("--questions", default="")
    ap.add_argument("--no-cross-check", action="store_true",
                    help="MiniCheck 교차검증 생략(기본=시도, 모델 미설치 시 자동 생략)")
    ap.add_argument("--all", action="store_true", help="풀 전체(core+pool) 일괄 측정")
    ap.add_argument("--core-only", action="store_true", help="core 5문항만(회전 없이)")
    a = ap.parse_args()
    if a.controls:
        sys.exit(0 if run_controls() else 1)
    if a.spool:
        # 제출 엔드포인트(/api/eval/pairs)가 쌓아 둔 스풀을 소화한다 — 야간 배치용.
        import glob
        from core.eval_pairs import load_pairs
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "eval_spool")
        files = sorted(glob.glob(os.path.join(base, "*.jsonl")))
        if not files:
            print("스풀 비어 있음 — 채점할 것 없음")
            sys.exit(0)
        for path in files:
            src = os.path.basename(path).rsplit("-", 3)[0]
            recs, errs = load_pairs(path)
            for e in errs:
                print("  ⚠️ %s: %s" % (os.path.basename(path), e))
            if recs:
                run_pairs(recs, source=src, cross=not a.no_cross_check)
            os.rename(path, path + ".done")          # 재채점 방지(원본은 보존)
        sys.exit(0)
    if a.pairs:
        from core.eval_pairs import load_pairs
        recs, errs = load_pairs(a.pairs)
        for e in errs:                      # 무성 스킵 금지 — 몇 건이 왜 빠졌는지 알린다
            print("  ⚠️ %s" % e)
        if not recs:
            print("채점할 레코드가 없습니다.")
            sys.exit(1)
        run_pairs(recs, source=a.source, cross=not a.no_cross_check)
        sys.exit(0)
    if a.questions:
        qs = [l.strip() for l in open(a.questions, encoding="utf-8") if l.strip()]
    elif a.all:
        p = load_pool()
        qs = p["core"] + p["pool"]
    elif a.core_only:
        qs = DEFAULT_QUESTIONS
    else:
        from datetime import date
        qs = questions_for_day(date.today().toordinal())   # core+일자 회전(러너 2종 동일 세트)
    run_real(qs, cross=not a.no_cross_check)
