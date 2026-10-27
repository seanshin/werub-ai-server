#!/usr/bin/env python3
"""계약 스모크 — **실제 모델을 호출해 "쓸 만한 답이 나오는가"를 본다.**

왜 별도인가: 회귀 1,291건은 전부 순수 로직·모킹이라 **모델 행동을 보지 않는다**.
2026-08-04에 찾은 결함 5건은 전부 "코드는 정상인데 답이 쓸모없는" 클래스였고,
단위 테스트로는 원리적으로 잡히지 않는다.

  · meditron이 분석 대신 "…과정을 알아보세요"만 반환 → JSON 파싱 실패 → 입력 에코가 나감
  · MedGemma가 `<start_of_image>` 특수 토큰을 응답에 실어 보냄
  · MedGemma가 면책 문구에서 진료과를 "방사선 종양학과"로 오안내
  · 약물 상호작용 검출이 영문 토큰만 읽어 한글 약물명을 통째로 누락
  · 의료법 RAG가 근거에 있는 벌칙 조를 인용하지 못함

⚠️ **이 스위트는 새 결함을 찾아주지 않는다.** 아는 결함의 재발만 막는다.
   새 결함은 직접 재봐야 나온다.

실행: GPU를 쓰므로 CI가 아니라 **배포 전·모델 변경 시·주 1회**.
  ./venv/bin/python scripts/smoke_contract.py            # 전체
  ./venv/bin/python scripts/smoke_contract.py --only dur # 일부
"""
import argparse
import json
import os
import re
import sys
import time

# 게이트 모드가 `core.gate_baseline`을 쓴다. 이 스크립트는 `scripts/`에서 실행되므로
# 프로젝트 루트가 sys.path에 없다(2026-08-10 첫 실전에서 `No module named 'core'`로
# 게이트가 차단됐다 — fail-closed 설계가 자기 배선 결함을 잡은 사례).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

BASE = "http://127.0.0.1:8585"
TIMEOUT = 600

# 🔴 이 스위트는 "실제 모델을 호출한다"고 선언해 놓고, 재실행하면 캐시를 읽고 있었다.
# 2026-08-12 실측: medlaw·control·predict·adverse-drug **43체크가 0.001~0.015초**로
# 지나갔다(같은 질문에서 한 글자만 바꾸면 4.3~6.4초). 배포 경로는 HUP로 워커가 새로
# 떠서 콜드라 정상이지만, **"흔들리나 보려고 단독 재실행"** 하는 순간 그 43체크는
# 판정력을 잃는다 — 지난 답을 다시 볼 뿐이다. 헤더는 로컬 요청에서만 먹는다.
NO_CACHE_HEADERS = {"X-No-Cache": "1"}

# 모델 특수 토큰 — 응답에 실려 나가면 파트너 UI에 노출된다
_TOKEN_RE = re.compile(r"<\s*/?\s*(?:start_of_image|end_of_image|image|bos|eos|pad|"
                       r"start_of_turn|end_of_turn)\s*>", re.IGNORECASE)
# 영상 판독에서 나오면 안 되는 진료과(암 방사선 치료과 ≠ 영상의학과)
_WRONG_DEPT_RE = re.compile(r"방사선\s*종양\s*학?과")

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok), detail))
    print("  %s %-46s %s" % ("✅" if ok else "🔴", name, detail[:70]), flush=True)


def post(path, payload, files=None):
    t0 = time.time()
    try:
        if files:
            r = requests.post(BASE + path, files=files, data=payload, timeout=TIMEOUT,
                              headers=NO_CACHE_HEADERS)
        else:
            r = requests.post(BASE + path, json=payload, timeout=TIMEOUT,
                              headers=NO_CACHE_HEADERS)
        return r.status_code, r.json(), time.time() - t0
    except Exception as e:
        return 0, {"_error": str(e)[:120]}, time.time() - t0


def blob(d):
    return json.dumps(d, ensure_ascii=False)


# ---------------------------------------------------------------------------
def smoke_dur():
    """식약처 DUR 정본 — 알려진 병용금기가 잡혀야 한다(LLM 미개입·수십 ms)."""
    print("\n[dur] 식약처 DUR 정본", flush=True)
    code, d, sec = post("/api/medical/dur/check", {"drugs": ["파록세틴", "셀레길린"]})
    check("dur: HTTP 200", code == 200, "code=%s" % code)
    ci = d.get("contraindications") or []
    check("dur: 알려진 병용금기 검출", len(ci) >= 1,
          "%d건 %s" % (len(ci), (ci[0].get("drug_b") if ci else "")))
    check("dur: LLM 미개입(1초 미만)", sec < 1.0, "%.3fs" % sec)
    # 무관한 조합은 잡히면 안 된다(과검출 방지)
    _, d2, _ = post("/api/medical/dur/check", {"drugs": ["타이레놀"]})
    check("dur: 단일 약물 과검출 없음", not (d2.get("contraindications") or []))


def smoke_adverse_drug():
    """meditron 무동작 재발 방지 — 구조화 필드가 있어야 하고 입력 에코면 안 된다."""
    print("\n[adverse-drug] 약물 이상반응 예측", flush=True)
    meds = ["파록세틴", "셀레길린"]
    code, d, sec = post("/api/medical/predict/adverse-drug",
                        {"medications": meds, "patient_info": {"age": 70, "sex": "M"}})
    check("adr: HTTP 200", code == 200, "code=%s %.1fs" % (code, sec))
    pred = d.get("prediction") or {}
    structured = isinstance(pred, dict) and any(
        k in pred for k in ("individual_risks", "interactions", "duplications",
                            "overall_risk", "monitoring_plan"))
    # 실패 시 원문 머리를 남긴다 — 2026-08-19 게이트가 1/5 비-JSON(키=response만)으로 차단됐는데 그 본문을 알 수 없었다.
    check("adr: 구조화 필드 존재", structured,
          "키=%s%s" % (list(pred.keys())[:5],
                      "" if structured else " 본문=%r" % str(pred.get("response") or "")[:100]))
    # 입력 에코 회귀 — response 단일 필드에 입력이 그대로 담기던 결함
    echoed = isinstance(pred, dict) and set(pred.keys()) <= {"response"}
    check("adr: 입력 에코 아님", not echoed)
    # DUR 정본 병합(한글 약물명이 읽혀야 성립)
    check("adr: 식약처DUR 정본 병합", "식약처DUR" in blob(d))


def smoke_predict_others():
    """readmission·complication — 구조화 응답과 비어 있지 않은 본문."""
    print("\n[predict] 재입원·합병증 예측", flush=True)
    for name, path, payload, keys in (
        ("readmission", "/api/medical/predict/readmission",
         {"patient_data": {"diagnosis": "심부전 급성 악화", "past_admissions": 3,
                           "length_of_stay": 7}},
         ("estimated_readmission_rate", "risk_level", "preventive_measures")),
        ("complication", "/api/medical/predict/complication",
         {"diagnosis": "복강경 담낭절제술 후 1일차",
          "patient_info": {"age": 72, "sex": "F"}},
         ("complications", "risk_level", "monitoring")),
    ):
        code, d, sec = post(path, payload)
        pred = d.get("prediction") or {}
        check("%s: HTTP 200" % name, code == 200, "code=%s %.1fs" % (code, sec))
        check("%s: 구조화 필드 존재" % name,
              isinstance(pred, dict) and any(k in pred for k in keys),
              "키=%s" % list(pred.keys())[:5] if isinstance(pred, dict) else type(pred).__name__)


def smoke_medlaw():
    """의료법 RAG — 알려진 정답과 근거 인용. 근거 없는 창작이 아닌지 본다."""
    print("\n[medical-law] 의료법 자문 RAG", flush=True)
    code, d, sec = post("/api/medical-law/ask", {"question": "진료기록부의 보존기간은?"})
    a = d.get("answer") or ""
    check("medlaw: HTTP 200", code == 200, "code=%s %.1fs" % (code, sec))
    check("medlaw: 빈 응답 아님", len(a) > 50, "%d자" % len(a))
    check("medlaw: 근거 인용", len(d.get("citations") or []) > 0,
          "%d건" % len(d.get("citations") or []))
    check("medlaw: 알려진 정답(진료기록부 10년)", "10년" in a)
    # 근거 조문은 **본문 또는 citations**에 있으면 된다.
    # 첫 구현이 본문만 봐서 실패했는데, 답변은 오히려 정확했다 — 시행규칙 제15조 원문이
    # "법 제22조제2항에 따른 진료기록부등을…"이라 모델이 본문을 정확히 인용하면
    # 화면에는 제22조가 나온다. 조 번호를 본문에 강제하는 것은 과한 요구다.
    cited = blob(d.get("citations") or [])
    check("medlaw: 근거 조문(제15조) 제공",
          re.search(r"제\s*15\s*조", a + cited) is not None)
    # 단서 보존(폐루프 16) — 근거에 있으면 답변에도 있어야 한다
    check("medlaw: 한정 단서 반영", ("1회에 한정" in a) or ("연장" in a))
    ko = len(re.findall(r"[가-힣]", a))
    check("medlaw: 한국어 응답", ko > len(a) * 0.3, "한글 %d/%d자" % (ko, len(a)))


def _load_control_set(filename, label):
    """통제군 파일 로드. 없거나 깨졌으면 **실패로 보고**한다(조용히 건너뛰지 않는다)."""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config", filename)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        check("%s: 통제군 파일" % label, False, str(e)[:60])
        return None


def _dig(d, path):
    """`translation.en` · `matches.0.efficacy` 같은 점 경로로 값을 꺼낸다(리스트 인덱스 포함)."""
    cur = d
    for part in (path or "").split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return ""
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
    return cur if isinstance(cur, str) else ""


_SCRIPT_RE = {
    # 🔑 일본어는 **가나 비율**로 본다. 한자만 세면 중국어와 구분되지 않고,
    #    그 구분 실패가 정확히 v2.105.0 결함이었다(ja 요청에 중국어).
    "ja": re.compile(r"[\u3040-\u309f\u30a0-\u30ff]"),
    "ko": re.compile(r"[가-힣]"),
}


def _extract_fenced(text):
    """```lang … ``` 펜스에서 코드만 꺼낸다.

    ⚠️ `/api/code/generate`는 응답 `code`에 **펜스와 뒤따르는 설명문**을 그대로 싣는데
    `syntax_valid`는 **펜스를 벗긴 블록**을 검증한 값이다 — 호출자가 받은 문자열을
    그대로 파싱하면 실패한다. 통제군은 서버와 **같은 대상**을 봐야 하므로 벗겨서 판정한다.
    (계약 자체를 바꿀지는 외부 호출자 영향이 있어 별건.)
    """
    m = re.search(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text or "", re.S)
    return m.group(1) if m else (text or "")


def smoke_coding():
    """코딩 통제군 — `config/coding_control_set.json`.

    🔑 **정답 검증이 가능한 드문 도메인**이다 — 코드는 파싱된다.
    문장이 아니라 **구조**를 본다(파싱 가능·테스트 형태·에러 언급).
    """
    print("\n[coding] 코딩 통제군", flush=True)
    spec = _load_control_set("coding_control_set.json", "coding")
    if not spec:
        return

    for q in spec.get("questions", []):
        qid = q["id"]
        code_st, d, sec = post(q["endpoint"], q.get("input") or {})

        want_status = q.get("expect_status")
        if want_status:
            check("coding[%s]: HTTP %s" % (qid, want_status), code_st == want_status,
                  "실제 %s" % code_st)
            continue
        if code_st != 200:
            check("coding[%s]: 응답" % qid, False, "code=%s" % code_st)
            continue

        for k, want in (q.get("expect") or {}).items():
            check("coding[%s]: %s" % (qid, k), d.get(k) == want,
                  "기대 %s 실제 %s" % (want, d.get(k)))

        text = d.get(q.get("text_field") or "") or ""
        if q.get("min_len"):
            check("coding[%s]: 빈 응답 아님" % qid, len(text) >= q["min_len"],
                  "%d자 (하한 %d) %.0fs" % (len(text), q["min_len"], sec))

        if q.get("python_parses"):
            # v2.125.1에서 계약이 고쳐졌다 — `code`는 순수 코드다. **그대로** 파싱한다.
            # 펜스가 다시 섞이면 `forbidden: ["```"]`가 먼저 잡는다.
            body = text
            try:
                import ast as _ast
                _ast.parse(body)
                ok, detail = True, "%d자 파싱 OK" % len(body)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, str(e)[:60]
            check("coding[%s]: 파싱 가능" % qid, ok, detail)

        anys = q.get("signals_any") or []
        if anys:
            check("coding[%s]: 필수 형태" % qid, any(x in text for x in anys),
                  "기대 %s" % anys)

        floor = q.get("min_korean_ratio")
        if floor is not None:
            ko = len(re.findall(r"[가-힣]", text))
            check("coding[%s]: 한국어 비율" % qid, ko >= len(text) * floor,
                  "%.2f (하한 %.2f)" % (ko / max(1, len(text)), floor))


def smoke_story():
    """스토리 통제군 — `config/story_control_set.json`.

    🔑 **'좋은 이야기'는 재지 않는다**(정답이 없다). 대신 **호출자가 준 지시가
    실제로 반영되는가**를 본다 — 인물 이름·세계관 고유명사는 결정적으로 판정된다.
    이 레포가 반복해 겪은 결함이 정확히 '호출자가 준 값이 200과 함께 버려지는 것'이다.
    """
    print("\n[story] 스토리 통제군", flush=True)
    spec = _load_control_set("story_control_set.json", "story")
    if not spec:
        return
    ep = spec.get("_endpoint") or "/api/story/generate"
    field = spec.get("_text_field") or "story"

    for q in spec.get("questions", []):
        qid = q["id"]
        code, d, sec = post(ep, q["input"])
        text = _dig(d, field) or (d.get(field) if isinstance(d.get(field), str) else "")
        if code != 200 or not text:
            check("story[%s]: 응답" % qid, False, "code=%s %d자" % (code, len(text or "")))
            continue
        if q.get("min_len"):
            ok_len = len(text) >= q["min_len"]
            # 실패 시 **본문 머리**를 남긴다 — 2026-08-19 게이트가 `40자`만 남기고 차단돼 그 40자가
            # 제목만인지·거절인지·절단인지 알 길이 없었다(재실행 3/3 통과=저율 흔들림). 다음엔 원문으로 가른다.
            check("story[%s]: 빈 응답 아님" % qid, ok_len,
                  "%d자 (하한 %d) %.0fs%s" % (len(text), q["min_len"], sec,
                                             "" if ok_len else " 본문=%r" % text[:80]))
        anys = q.get("signals_any") or []
        if anys:
            check("story[%s]: 지시 반영" % qid, any(x in text for x in anys),
                  "기대 %s" % anys)
        bad = [x for x in q.get("forbidden", []) if x in text]
        if q.get("forbidden"):
            check("story[%s]: 입력 에코 없음" % qid, not bad,
                  "출현 %s" % bad if bad else "")
        floor = q.get("min_korean_ratio")
        if floor is not None:
            ko = len(re.findall(r"[가-힣]", text))
            check("story[%s]: 한국어 비율" % qid, ko >= len(text) * floor,
                  "%.2f (하한 %.2f)" % (ko / max(1, len(text)), floor))


def smoke_medical_data():
    """의료 정본 통제군 — `config/medical_data_control_set.json`.

    🔑 **LLM이 개입하지 않는다.** 식약처·심평원 정본 DB 조회라 결과가 결정적이고,
    그래서 **모델을 갈아끼워도 불변인 가장 견고한 게이트**다.

    🔴 DUR 미검출(위음성)은 처방 사고로 이어지고, 과검출(위양성)은 경보 피로를 만들어
    결국 무시당한다 — **양방향 다 건다**.
    """
    print("\n[medical-data] 의료 정본 통제군", flush=True)
    spec = _load_control_set("medical_data_control_set.json", "medical-data")
    if not spec:
        return

    for q in spec.get("questions", []):
        qid = q["id"]
        ep = q["endpoint"]
        if q.get("method") == "GET":
            t0 = time.time()
            try:
                r = requests.get(BASE + ep, timeout=TIMEOUT)
                code, d, sec = r.status_code, r.json(), time.time() - t0
            except Exception as e:  # noqa: BLE001
                code, d, sec = 0, {"_error": str(e)[:80]}, time.time() - t0
        else:
            code, d, sec = post(ep, q.get("input") or {})
        if code != 200:
            check("meddata[%s]: 응답" % qid, False, "code=%s" % code)
            continue

        for k, want in (q.get("expect") or {}).items():
            check("meddata[%s]: %s" % (qid, k), d.get(k) == want,
                  "기대 %s 실제 %s" % (want, d.get(k)))

        for k in q.get("nonempty_lists") or []:
            v = d.get(k)
            check("meddata[%s]: %s 검출" % (qid, k),
                  isinstance(v, list) and len(v) >= 1,
                  "%d건" % (len(v) if isinstance(v, list) else -1))

        for k in q.get("empty_lists") or []:
            v = d.get(k)
            check("meddata[%s]: %s 과검출 없음" % (qid, k),
                  isinstance(v, list) and not v,
                  "%d건" % (len(v) if isinstance(v, list) else -1))

        for k, n in (q.get("list_len") or {}).items():
            v = d.get(k)
            check("meddata[%s]: %s 개수" % (qid, k),
                  isinstance(v, list) and len(v) == n,
                  "%s (기대 %d)" % (len(v) if isinstance(v, list) else v, n))

        # 🔴 **정본 데이터 손실 감시** — 손실은 조용하다. v2.124.92에서 치료재료가
        #    91,211→59,000(-35%)으로 사라졌는데 동기화는 `status=ok`였고 화면도 정상이었다.
        #    하한은 실측의 80~90%로 둬 **정상 갱신에는 안 걸리고 대량 손실만** 잡는다.
        for k, floor in (q.get("min_numbers") or {}).items():
            v = d.get(k)
            check("meddata[%s]: %s 규모" % (qid, k),
                  isinstance(v, (int, float)) and v >= floor,
                  "%s (하한 %s)" % (v, floor))
        for path, floor in (q.get("min_nested") or {}).items():
            cur = d
            for part in path.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            check("meddata[%s]: %s 규모" % (qid, path),
                  isinstance(cur, (int, float)) and cur >= floor,
                  "%s (하한 %s)" % (cur, floor))

        for k, wants in (q.get("contains_any") or {}).items():
            v = d.get(k) or []
            check("meddata[%s]: %s" % (qid, k),
                  any(w in v for w in wants), "실제 %s" % (v if v else "없음"))

        si = q.get("signals_in")
        if si:
            text = _dig(d, si["field"])
            check("meddata[%s]: %s 내용" % (qid, si["field"]),
                  any(x in text for x in si.get("any", [])),
                  "기대 %s" % si.get("any"))

        if q.get("max_sec"):
            check("meddata[%s]: LLM 미개입" % qid, sec < q["max_sec"],
                  "%.3fs (상한 %.1fs)" % (sec, q["max_sec"]))


def smoke_writing():
    """글쓰기 통제군 — `config/writing_control_set.json`.

    🔑 대부분이 **과거에 실제로 깨졌던 것**이다(v2.105.0 번역 가드 3종).
    특히 캐시 충돌은 **두 요청을 비교해야만** 잡힌다 — 단건으로는 각각 그럴듯하다.
    """
    print("\n[writing] 글쓰기 통제군", flush=True)
    spec = _load_control_set("writing_control_set.json", "writing")
    if not spec:
        return
    default_ep = spec.get("_endpoint") or "/api/translate"
    default_field = spec.get("_text_field") or "translation.en"

    for q in spec.get("questions", []):
        qid = q["id"]
        ep = q.get("endpoint") or default_ep
        field = q.get("text_field") or default_field
        code, d, sec = post(ep, q["input"])
        text = _dig(d, field)
        if code != 200 or not text:
            check("writing[%s]: 응답" % qid, False, "code=%s %d자" % (code, len(text)))
            continue

        if q.get("min_len"):
            check("writing[%s]: 빈 응답 아님" % qid, len(text) >= q["min_len"],
                  "%d자 (하한 %d)" % (len(text), q["min_len"]))

        # 🔴 캐시 충돌 — 다른 원문이 같은 번역을 받으면 안 된다
        if q.get("compare_with") is not None and q.get("must_differ"):
            _c, d2, _s = post(ep, q["compare_with"])
            other = _dig(d2, field)
            check("writing[%s]: 다른 원문=다른 결과" % qid,
                  bool(other) and other != text,
                  "동일 응답 반환" if other == text else "OK")

        miss = [x for x in q.get("signals", []) if x not in text]
        if q.get("signals"):
            check("writing[%s]: 필수 신호" % qid, not miss,
                  "누락 %s" % miss if miss else "%.1fs" % sec)
        anys = q.get("signals_any") or []
        if anys:
            check("writing[%s]: 택일 신호" % qid, any(x in text for x in anys),
                  "기대 %s" % anys)
        bad = [x for x in q.get("forbidden", []) if x in text]
        if q.get("forbidden"):
            check("writing[%s]: 금지 표현 없음" % qid, not bad,
                  "출현 %s" % bad if bad else "")

        floor = q.get("min_korean_ratio")
        if floor is not None:
            ko = len(re.findall(r"[가-힣]", text))
            check("writing[%s]: 한국어 비율" % qid, ko >= len(text) * floor,
                  "%.2f (하한 %.2f)" % (ko / max(1, len(text)), floor))

        sr = q.get("min_script_ratio")
        if sr:
            rex = _SCRIPT_RE.get(sr.get("script"))
            hit = len(rex.findall(text)) if rex else 0
            check("writing[%s]: 표기 충실도(%s)" % (qid, sr.get("script")),
                  bool(rex) and hit >= len(text) * sr.get("min", 0.3),
                  "%.2f (하한 %.2f)" % (hit / max(1, len(text)), sr.get("min", 0.3)))


def smoke_korean_quality():
    """한국어 통제군 — `config/korean_control_set.json`.

    🔑 **정답이 있는 드문 도메인**이다(맞춤법은 규칙). 다만 LLM 교정이라 문장 전체가
    매번 같지는 않아 **고쳐야 할 낱말이 고쳐졌는가**만 본다 — 전체 일치를 요구하면
    통제군이 흔들리고, 흔들리는 게이트는 꺼지는 게이트다.
    """
    print("\n[korean] 한국어 통제군", flush=True)
    spec = _load_control_set("korean_control_set.json", "korean")
    if not spec:
        return
    endpoint = spec.get("_endpoint") or "/api/korean/spellcheck"
    field = spec.get("_text_field") or "checked"

    for q in spec.get("questions", []):
        qid = q["id"]
        code, d, sec = post(endpoint, q["input"])
        text = d.get(field) or ""
        if code != 200 or not text:
            check("korean[%s]: 응답" % qid, False, "code=%s %d자" % (code, len(text)))
            continue
        # 🔴 spellcheck는 LLM 타임아웃·예외 때 **원문을 200으로** 돌려준다(`success:false`
        #    +`error_detail`). 그걸 안 보면 "답을 못 받음"이 "교정 결과 누락"으로 **위장**된다.
        #    2026-08-18 실증: 게이트 도중 외부 /api/generate(27초)와 상주 예열이 7b를 축출해
        #    30초 타임아웃 → 폴백 원문 `됬다` → "누락 ['됐다']"로 오분류 → 게이트 차단.
        #    모델도 통제군도 멀쩡했다(정확한 입력 44/44). **실패의 종류를 갈라서 적는다.**
        if d.get("success") is False or d.get("error_detail"):
            check("korean[%s]: 응답" % qid, False,
                  "LLM 실패 폴백(원문 반환) — %s" % str(d.get("error_detail") or "")[:60])
            continue
        miss = [x for x in q.get("signals", []) if x not in text]
        if q.get("signals"):
            check("korean[%s]: 교정 결과" % qid, not miss,
                  "누락 %s" % miss if miss else "%.1fs" % sec)
        anys = q.get("signals_any") or []
        if anys:
            check("korean[%s]: 뜻 보존" % qid, any(x in text for x in anys),
                  "기대 %s" % anys)
        bad = [x for x in q.get("forbidden", []) if x in text]
        if q.get("forbidden"):
            check("korean[%s]: 오류 잔존 없음" % qid, not bad,
                  "출현 %s" % bad if bad else "")


def smoke_counseling():
    """상담 통제군 — `config/counseling_control_set.json`.

    🔑 **의료법과 성격이 다르다.** 상담은 정답이 없으므로 `signals`는 안전 계약에만
    걸고 나머지는 `forbidden`으로 잡는다(기획서 §2-c). 그래서 이 스위트가 지키는 것은
    "좋은 상담인가"가 아니라 **"하면 안 되는 것을 하지 않는가"**다.

    위기 안내 부착은 정규식+문자열이라 **결정적**이다 — 모델을 갈아끼워도 성립해야 한다.
    """
    print("\n[counseling] 상담 통제군", flush=True)
    spec = _load_control_set("counseling_control_set.json", "counseling")
    if not spec:
        return
    endpoint = spec.get("_endpoint") or "/api/counseling/chat"
    field = spec.get("_text_field") or "message"

    for q in spec.get("questions", []):
        qid = q["id"]
        code, d, sec = post(endpoint, q["input"])
        text = d.get(field) or ""
        if code != 200 or not text:
            check("counseling[%s]: 응답" % qid, False, "code=%s %d자" % (code, len(text)))
            continue

        # 구조화 필드 — is_crisis 같은 결정적 플래그
        for k, want in (q.get("expect") or {}).items():
            check("counseling[%s]: %s" % (qid, k), d.get(k) == want,
                  "기대 %s 실제 %s" % (want, d.get(k)))

        miss = [x for x in q.get("signals", []) if x not in text]
        if q.get("signals"):
            check("counseling[%s]: 필수 신호" % qid, not miss,
                  "누락 %s" % miss if miss else "%.0fs" % sec)
        anys = q.get("signals_any") or []
        if anys:
            check("counseling[%s]: 택일 신호" % qid, any(x in text for x in anys),
                  "기대 %s" % anys)
        bad = [x for x in q.get("forbidden", []) if x in text]
        if q.get("forbidden"):
            check("counseling[%s]: 금지 표현 없음" % qid, not bad,
                  "출현 %s" % bad if bad else "")

        # 한국어 비율 — 상담 모델이 영어 중심이라 자연화 단계가 있다. 그 단계가
        # 빠지거나 모델이 바뀌면 **영어 누출**이 가장 먼저 나타난다.
        floor = q.get("min_korean_ratio")
        if floor is not None:
            kr = ((d.get("pipeline") or {}).get("korean_ratio"))
            check("counseling[%s]: 한국어 비율" % qid,
                  isinstance(kr, (int, float)) and kr >= floor,
                  "%.2f (하한 %.2f)" % (kr, floor) if isinstance(kr, (int, float))
                  else "korean_ratio 없음")


def smoke_control():
    """통제군 — `config/medlaw_control_set.json`에 고정한 '지금 맞는 답'.

    검색·생성을 바꿀 때 **하나라도 깨지면 즉시 되돌린다**는 기준선이다.
    기억이나 즉석 판단으로 하면 변경 후에 기준이 흔들리므로 파일로 둔다.
    """
    print("\n[control] 의료법 통제군", flush=True)
    spec = _load_control_set("medlaw_control_set.json", "control")
    if not spec:
        return

    for q in spec.get("questions", []):
        qid = q["id"]
        code, d, sec = post("/api/medical-law/ask", {"question": q["question"]})
        a = d.get("answer") or ""
        cited = blob(d.get("citations") or [])
        hay = a + " " + cited
        if code != 200 or not a:
            check("control[%s]: 응답" % qid, False, "code=%s %d자" % (code, len(a))); continue
        # 필수 신호 — 전부 있어야 한다
        miss = [x for x in q.get("signals", []) if x not in a]
        check("control[%s]: 필수 신호" % qid, not miss, "누락 %s" % miss if miss else "%.0fs" % sec)
        # 택일 신호 — 하나라도 있으면 된다(조문 번호는 본문/citations 어디든)
        anys = q.get("signals_any") or []
        if anys:
            check("control[%s]: 근거 조문" % qid, any(x in hay for x in anys),
                  "기대 %s" % anys)
        # 한정 단서 — 근거에 있으면 답변에도 있어야 한다(폐루프 16).
        # 각 항목은 **개념 하나**이고 `|`로 표현 변형을 나열한다. 개념은 전부 필요하다
        # ("연장"만 남고 "1회 한정"이 빠지는 것이 바로 폐루프 16이 고친 탈락 형태라,
        #  any로 두면 그 결함을 통과시킨다). 모델이 "1회에 한해"로 바꿔 쓰므로 변형 필수.
        cl = q.get("clause") or []
        if cl:
            missing = [g for g in cl if not any(v in a for v in g.split("|"))]
            check("control[%s]: 한정 단서" % qid, not missing,
                  "누락 개념 %s" % [g.split("|")[0] for g in missing] if missing else "")
        # 과거 관측된 오류 — 재발 감시
        bad = [x for x in q.get("forbidden", []) if x in a]
        check("control[%s]: 과거 오류 재발 없음" % qid, not bad, "출현 %s" % bad if bad else "")


def _draw_xray_png():
    """흉부 X-ray풍 합성 PNG — PIL로 결정적으로 그린다(폰트 불요).

    v2.125.15: 종전엔 `--image` 기본값 `/tmp/xray.png`에 의존했는데 **재부팅이 /tmp를
    비우면 게이트마다 스킵**됐다(08-19 재부팅 후 실측 — 스킵은 명시적이었으나 커버리지가
    조용히 177→173으로 줄었다). 체크가 보는 건 200·응답 길이·특수 토큰·진료과 오안내라
    실제 영상이 필요 없다 — vision-general(PIL 한글 명함)과 같은 결정적 생성으로 고정한다.
    """
    try:
        import io
        from PIL import Image, ImageDraw
    except Exception as e:  # noqa: BLE001
        return None, "PIL 없음: %s" % e
    im = Image.new("L", (900, 1100), 12)
    d = ImageDraw.Draw(im)
    d.rectangle((400, 60, 500, 1040), fill=70)                      # 종격동/척추 음영
    d.ellipse((90, 180, 430, 900), fill=120)                        # 좌폐야
    d.ellipse((470, 180, 810, 900), fill=118)                       # 우폐야
    for i in range(6):                                              # 늑골 호
        y = 220 + i * 110
        d.arc((60, y, 440, y + 260), 200, 340, fill=60, width=6)
        d.arc((460, y, 840, y + 260), 200, 340, fill=60, width=6)
    d.ellipse((350, 620, 620, 900), fill=95)                        # 심장 음영
    b = io.BytesIO(); im.convert("RGB").save(b, "PNG")
    return b.getvalue(), None


def smoke_vision(image_path):
    """의료 영상 판독 — 특수 토큰·진료과 오역 재발 방지.

    `--image`가 있으면 그 파일을, 없으면 합성 X-ray(`_draw_xray_png`)를 쓴다 —
    파일 부재가 무성 커버리지 손실이 되지 않게 한다(v2.125.15).
    """
    print("\n[vision] 의료 영상 판독", flush=True)
    try:
        if image_path:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
        else:
            img_bytes, why = _draw_xray_png()
            if img_bytes is None:
                check("vision: 합성 영상 생성", False, why)   # 스킵이 아니라 명시 실패
                return
            print("  (합성 X-ray 사용 — --image 미지정)", flush=True)
        import io as _io
        code, d, sec = post("/api/vision/medical/analyze",
                            {"modality": "xray"},
                            files={"image": ("xray.png", _io.BytesIO(img_bytes), "image/png")})
    except OSError as e:
        check("vision: 영상 로드", False, str(e)[:60]); return
    raw = (d.get("raw_response") or "") + " " + (d.get("recommendations") or "")
    check("vision: HTTP 200", code == 200, "code=%s %.1fs" % (code, sec))
    check("vision: 빈 응답 아님", len(d.get("raw_response") or "") > 100,
          "%d자" % len(d.get("raw_response") or ""))
    check("vision: 특수 토큰 없음", not _TOKEN_RE.search(raw),
          str(_TOKEN_RE.findall(raw))[:50])
    check("vision: 진료과 오안내 없음", not _WRONG_DEPT_RE.search(raw),
          str(_WRONG_DEPT_RE.findall(raw))[:40])


def _draw_card_png(spec):
    """한글 명함 PNG — PIL로 결정적으로 그린다. 폰트가 없으면 None(호출자가 **명시 실패**로 보고)."""
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont
    except Exception:  # noqa: BLE001
        return None, "PIL 없음"
    import os
    font = next((f for f in spec.get("font_candidates", []) if os.path.exists(f)), None)
    if not font:
        return None, "한글 폰트 없음 %s" % spec.get("font_candidates")
    c = spec["card"]
    im = Image.new("RGB", (900, 520), "white"); d = ImageDraw.Draw(im)
    fb, fm, fs = ImageFont.truetype(font, 64), ImageFont.truetype(font, 40), ImageFont.truetype(font, 34)
    d.text((60, 60), c["name"], fill="black", font=fb); d.text((300, 80), c["title"], fill="#333", font=fm)
    d.text((60, 200), c["company"], fill="#0a4", font=fm)
    d.text((60, 300), "Tel " + c["phone"], fill="black", font=fs); d.text((60, 360), c["email"], fill="black", font=fs)
    d.rectangle((0, 470, 900, 520), fill="#0a4")
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue(), None


def smoke_vision_general():
    """일반 비전 통제군 — `config/vision_control_set.json`.

    v2.125.10이 `/api/vision/describe` 기본 모델을 qwen3-vl:8b로 바꿨는데 스모크는 의료 비전만 봤다.
    이 경로의 결함 클래스는 **200+빈 응답**(사고 예산)·**폴백 조용히 발동**·502다 — 셋 다 여기서 잡는다.
    차단 조건은 200·기본 모델·이름/전화 중 1 이상. 한국어 비율·지연은 **관측**(check 아님)으로 시작한다
    (흔들리는 신호를 게이트에 두지 않는다 — 5회 연속 안정 후 승격).
    """
    print("\n[vision-general] 일반 비전 통제군(한글 명함)", flush=True)
    spec = _load_control_set("vision_control_set.json", "vision-general")
    if not spec:
        return
    png, err = _draw_card_png(spec)
    if not png:
        check("vision-general: 통제군 이미지 생성", False, err or "")
        return
    import io
    code, d, sec = post(spec.get("_endpoint") or "/api/vision/describe", {"prompt": spec["prompt"]},
                        files={"image": ("card.png", io.BytesIO(png), "image/png")})
    desc = (d.get("description") or "") if isinstance(d, dict) else ""
    check("vision-general: HTTP 200", code == 200, "code=%s %.1fs %s" % (code, sec, blob(d)[:60] if code != 200 else ""))
    check("vision-general: 기본 모델 응답(폴백 아님)", d.get("model") == spec["expected_model"],
          "model=%s (기대 %s)" % (d.get("model"), spec["expected_model"]))
    c = spec["card"]
    hits = {k: (v in desc) for k, v in c.items()}
    check("vision-general: 빈 응답 아님", len(desc.strip()) > 0, "%d자" % len(desc))
    check("vision-general: 명함 OCR(이름/전화 중 1)", hits["name"] or hits["phone"],
          "hits=%s 본문=%r" % (sum(hits.values()), desc[:60]))
    # 관측(차단 아님) — 승격 전 안정성 데이터
    ko = len(re.findall(r"[가-힣]", desc)); ratio = ko / max(1, len(re.findall(r"[가-힣A-Za-z]", desc)))
    obs = spec.get("observe") or {}
    print("  👁  관측: OCR %d/5 · 한국어 비율 %.2f(관측 하한 %.2f) · %.1fs(관측 상한 %ss)" % (
        sum(hits.values()), ratio, obs.get("min_korean_ratio", 0), sec, obs.get("max_sec", "-")), flush=True)


# ── 파트너 계약 (2026-08-06 합류) ──────────────────────────────────────────
# 야간 평가 cron이 08-04에 중단돼 **자동 회귀 감지가 없다.** 파트너 경로가 깨지면
# 사람이 제보해야 알게 되는데, 07-28 edu 위임 100% 실패가 정확히 그렇게 며칠 갔다.
# 아래는 전부 **과거에 실제로 깨졌던 계약**이다(추측한 계약이 아니다).

_TMP_COLLECTION = "smoke_contract_tmp"


def _delete_doc(doc_id):
    try:
        requests.delete("%s/api/rag/document/%s?collection=%s" % (BASE, doc_id, _TMP_COLLECTION),
                        timeout=TIMEOUT)
    except Exception:  # noqa: BLE001 — 정리는 비차단
        pass


def smoke_rag():
    """Cerno·edu RAG 계약 — 메타 격리(보안)·collection 전달·confidence·삭제 건수.

    · `v2.124.65` 🔴 hybrid가 `filter`를 무시해 **다른 owner 문서가 반환**됐다(보안 제보)
    · `v2.124.37` `/api/rag/*`가 `collection`을 무시해 edu가 쓸 수 없었다
    · `v2.124.65` score는 RRF 순위라 임계 불가 → `confidence`(코사인) 노출
    · `v2.124.67` 삭제가 무효인데 `ok:true`라 문서가 조용히 잔존했다
    """
    print("\n[rag] 파트너 RAG 계약 (Cerno·edu)", flush=True)
    for doc_id, owner, word in (("smoke-a", "AAA", "알파"), ("smoke-b", "BBB", "베타")):
        code, d, _ = post("/api/rag/index", {
            "id": doc_id, "text": "스모크 계약 검사용 문서 %s zzqqx" % word,
            "metadata": {"owner": owner}, "collection": _TMP_COLLECTION})
        if code != 200 or not d.get("ok"):
            check("rag: 색인(%s)" % doc_id, False, "code=%s %s" % (code, blob(d)[:60]))
            return
    try:
        code, d, _ = post("/api/rag/search", {
            "query": "zzqqx", "mode": "hybrid", "collection": _TMP_COLLECTION,
            "filter": {"owner": "AAA"}})
        res = d.get("results") or []
        owners = {(r.get("metadata") or {}).get("owner") for r in res}
        check("rag: hybrid HTTP 200", code == 200, "code=%s" % code)
        # 🔴 보안 회귀 — 다른 owner가 하나라도 섞이면 격리가 깨진 것이다
        check("rag: hybrid 메타 필터 격리", bool(res) and owners == {"AAA"},
              "%d건 owners=%s" % (len(res), owners))
        check("rag: confidence(코사인) 노출",
              all(isinstance(r.get("confidence"), (int, float)) for r in res),
              str([r.get("confidence") for r in res])[:40])

        _, d2, _ = post("/api/rag/search", {
            "query": "zzqqx", "mode": "hybrid", "collection": _TMP_COLLECTION,
            "filter": {"owner": "NOBODY"}})
        check("rag: 없는 owner는 0건", (d2.get("count") or 0) == 0, "count=%s" % d2.get("count"))

        # collection 미지정이면 기본 컬렉션 → 임시 문서가 보이면 안 된다
        _, d3, _ = post("/api/rag/search", {"query": "zzqqx", "mode": "hybrid"})
        leaked = [r for r in (d3.get("results") or [])
                  if str(r.get("id", "")).startswith("smoke-")]
        check("rag: collection 격리(기본 컬렉션 미오염)", not leaked, "%d건 유출" % len(leaked))
    finally:
        try:
            r = requests.delete("%s/api/rag/document/smoke-a?collection=%s" % (BASE, _TMP_COLLECTION),
                                timeout=TIMEOUT)
            dd = r.json()
            check("rag: 삭제 건수 반환", dd.get("deleted") == 1,
                  "deleted=%s fts=%s" % (dd.get("deleted"), dd.get("fts_deleted")))
        except Exception as e:  # noqa: BLE001
            check("rag: 삭제 건수 반환", False, str(e)[:60])
        _delete_doc("smoke-b")


def smoke_partner_status():
    """edu·HIS가 폴링하는 상태 계약 — 필드가 사라지면 호출자가 조용히 오동작한다.

    · `v2.124.40` 화자분리 미실행 원인이 옵트인 미지정이었는데 가용 여부를 알 방법이 없었다
    · `v2.124.42` 버스트 대기열을 넣고도 접수 게이트가 막아 절반만 동작했다(가시화 필요)
    · `v2.124.57` 무음 환각 판별 신호가 없어 HIS가 에코를 걸러낼 수 없었다
    """
    print("\n[partner-status] edu·HIS 상태 계약", flush=True)
    code, d, _ = post_get("/api/meeting/status")
    check("meeting: HTTP 200", code == 200, "code=%s" % code)
    for key, kind in (("diarize_available", bool), ("whisper_available", bool),
                      ("queued_jobs", int), ("max_queue", int)):
        check("meeting: %s 노출" % key, isinstance(d.get(key), kind), "값=%r" % d.get(key))
    opts = d.get("supported_options")
    check("meeting: supported_options 목록", isinstance(opts, list) and len(opts) >= 5,
          "%s개" % (len(opts) if isinstance(opts, list) else "?"))
    # 옵트인 키가 목록에서 빠지면 edu가 화자분리를 못 켠다
    check("meeting: diarize 옵션 명시",
          isinstance(opts, list) and "diarize" in opts, str(opts)[:60])

    code, d, _ = post_get("/api/stt/chunk/status")
    check("stt: HTTP 200", code == 200, "code=%s" % code)
    check("stt: 상태 필드 존재", isinstance(d, dict) and len(d) > 0, blob(d)[:60])


def smoke_queue_signal():
    """Cerno 계약 — 큐 대기가 조용히 삼켜지지 않는다(`v2.124.66`).

    대기열이 429를 흡수해 호출자에겐 "그냥 느린 서버"로만 보였다. 헤더가 사라지면
    같은 상태로 되돌아간다.
    """
    print("\n[queue] 큐 대기 신호 헤더 (Cerno)", flush=True)
    try:
        r = requests.post(BASE + "/api/chat", timeout=TIMEOUT, json={
            "model": "qwen2.5:14b", "stream": False,
            "messages": [{"role": "user", "content": "1+1은?"}]})
    except Exception as e:  # noqa: BLE001
        check("queue: 요청", False, str(e)[:60]); return
    check("queue: HTTP 200", r.status_code == 200, "code=%s" % r.status_code)
    # 0이어도 **명시**되어야 한다 — 없으면 호출자가 대기 여부를 구분할 수 없다
    check("queue: X-Queue-Waited-Ms 존재", "X-Queue-Waited-Ms" in r.headers,
          r.headers.get("X-Queue-Waited-Ms"))
    check("queue: X-Queue-Active 존재", "X-Queue-Active" in r.headers,
          r.headers.get("X-Queue-Active"))
    # Position은 **대기했을 때만** 실린다(설계) — 존재 자체를 요구하지 않는다


def smoke_openai_compat():
    """OpenPencil 계약 — `/v1` OpenAI 호환(`v2.110.0`).

    Ollama 네이티브 `/v1` 패스스루가 아니라 변환 프록시라, 변환이 깨지면 조용히 어긋난다.
    """
    print("\n[v1] OpenAI 호환 (OpenPencil)", flush=True)
    code, d, _ = post_get("/v1/models")
    check("v1: models HTTP 200", code == 200, "code=%s" % code)
    check("v1: models 배열", isinstance(d.get("data"), list) and d["data"],
          "%s개" % len(d.get("data") or []))

    code, d, sec = post("/v1/chat/completions", {
        "model": "qwen2.5:14b", "max_tokens": 16, "stream": False,
        "messages": [{"role": "user", "content": "1+1은? 숫자만."}]})
    check("v1: 비스트리밍 HTTP 200", code == 200, "code=%s %.1fs" % (code, sec))
    ch = (d.get("choices") or [{}])[0]
    check("v1: choices[0].message.content",
          bool(((ch.get("message") or {}).get("content") or "").strip()),
          blob(ch)[:60])
    check("v1: usage 필드", isinstance(d.get("usage"), dict), blob(d.get("usage"))[:50])

    # 스트리밍 = OpenAI SSE 규격(`data: ` 접두 + [DONE])
    try:
        r = requests.post(BASE + "/v1/chat/completions", stream=True, timeout=TIMEOUT, json={
            "model": "qwen2.5:14b", "max_tokens": 16, "stream": True,
            "messages": [{"role": "user", "content": "1+1은? 숫자만."}]})
        lines = [ln.decode("utf-8", "replace") for ln in r.iter_lines() if ln]
    except Exception as e:  # noqa: BLE001
        check("v1: 스트리밍", False, str(e)[:60]); return
    check("v1: SSE data: 접두", any(ln.startswith("data: ") for ln in lines),
          (lines[0][:40] if lines else "빈 스트림"))
    check("v1: SSE [DONE] 종료", any(ln.strip() == "data: [DONE]" for ln in lines),
          (lines[-1][:30] if lines else ""))


def post_get(path):
    """GET 헬퍼 — post()와 같은 (code, json, sec) 계약."""
    t0 = time.time()
    try:
        r = requests.get(BASE + path, timeout=TIMEOUT, headers=NO_CACHE_HEADERS)
        return r.status_code, r.json(), time.time() - t0
    except Exception as e:  # noqa: BLE001
        return 0, {"_error": str(e)[:120]}, time.time() - t0


# ---------------------------------------------------------------------------
SUITES = {"dur": smoke_dur, "adverse-drug": smoke_adverse_drug,
          "predict": smoke_predict_others, "medlaw": smoke_medlaw,
          "control": smoke_control, "counseling": smoke_counseling,
          "korean-quality": smoke_korean_quality, "writing": smoke_writing,
          "medical-data": smoke_medical_data, "story": smoke_story,
          "coding": smoke_coding,
          "rag": smoke_rag, "partner-status": smoke_partner_status,
          "vision-general": smoke_vision_general,
          "queue": smoke_queue_signal, "v1": smoke_openai_compat}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="일부만 실행 (%s, vision)" % ",".join(SUITES))
    ap.add_argument("--image", default="/tmp/xray.png", help="의료 영상 경로")
    ap.add_argument("--gate", action="store_true",
                    help="게이트 모드 — **신규 실패만** 비-0 종료(알려진 실패는 보고만). "
                         "미지정 시 기존 동작(실패가 하나라도 있으면 비-0)")
    ap.add_argument("--baseline", default="config/gate_baseline.json",
                    help="알려진 실패 목록(게이트 모드 전용)")
    args = ap.parse_args()

    print("=== 계약 스모크 (실제 모델 호출) ===", flush=True)
    names = [args.only] if args.only else list(SUITES) + ["vision"]
    for n in names:
        if n == "vision":
            import os
            smoke_vision(args.image if os.path.exists(args.image) else None)
        elif n in SUITES:
            SUITES[n]()
        else:
            print("  알 수 없는 스위트: %s" % n, flush=True)

    ok = sum(1 for _, p, _ in _results if p)
    bad = [(n, d) for n, p, d in _results if not p]
    print("\n=== 결과: %d/%d 통과 ===" % (ok, len(_results)), flush=True)
    for n, d in bad:
        print("  🔴 %s  %s" % (n, d[:80]), flush=True)

    if not args.gate:
        return 1 if bad else 0

    # 게이트 모드 — 알려진 실패는 배포를 막지 않는다(기획서 §8-3b).
    # ⚠️ 베이스라인 파일이 깨졌으면 조용히 통과시키지 않고 **차단**한다.
    #    빈 베이스라인으로 떨어지면 기존 실패가 전부 신규가 되고, 그러면 사람이 게이트를 끈다.
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    bpath = args.baseline if _os.path.isabs(args.baseline) else _os.path.join(root, args.baseline)
    try:
        from core import gate_baseline
        base = gate_baseline.load(bpath)
    except Exception as e:  # noqa: BLE001
        print("\n🔴 베이스라인을 읽지 못했습니다(%s): %s" % (bpath, e), flush=True)
        return 2
    verdict = gate_baseline.classify(_results, base)
    print("\n" + gate_baseline.format_report(verdict), flush=True)
    return 1 if gate_baseline.should_block(verdict) else 0


if __name__ == "__main__":
    sys.exit(main())
