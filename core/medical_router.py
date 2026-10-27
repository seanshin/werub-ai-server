"""의료 정본 도구 결정적 라우팅 — 순수 로직(re만 의존, 테스트 가능).

오케스트레이터 챗에서 LLM tool-calling 불확실성·일반 워크플로우 가로채기를 우회해
명확한 의료 질의를 정본 도구(대체조제·코드해석·의료법·심사기준)로 결정적 라우팅한다.
"""
from __future__ import annotations

import re

_CODE9 = re.compile(r"\b(\d{9})\b")    # 보험 제품코드
_CODE13 = re.compile(r"\b(\d{13})\b")  # 표준코드(바코드)

# 약 이름(제형 접미) — 제품명 추출용
_FORM = re.compile(
    r"[가-힣A-Za-z0-9]{2,}(?:서방정|에스알정|구강정|점안액|건조시럽|시럽제|시럽|캡슐|정제|"
    r"주사액|주사|연고|크림|패치|좌제|과립|트로키|정|산|액|환|주)")
# 약물 연결어(병용 질의 분리). 와/과/랑은 공백 lookahead로 단어 내부 오매칭 방지(예: '와'파린).
_CONN = re.compile(r"(?:이랑|랑|와|과|하고|및|그리고)(?=\s)|\s*[,+]\s*")
# 후행 조사(약물명에서 제거)
_PARTICLE = re.compile(r"(에게서|에게|한테|에서|으로|이랑|랑|에|은|는|이|가|을|를|의|도|와|과|께|만)$")
# 약물명 추출 시 제외할 의도/불용어
_STOP = frozenset(
    "이 그 저 약 약물 좀 의 은 는 이거 이게 그거 그게 알려줘 알려 줘 주세요 뭐야 무엇 어떤 "
    "어떻게 먹어 먹는 먹어도 복용 복용법 사용 사용법 용법 효능 효과 부작용 보관 보관법 주의 "
    "주의사항 병용 병용금기 금기 상호작용 처방 확인 점검 해줘 해 환자 진짜 정말 좀더 그리고 또 "
    "되는지 되나요 돼 돼요 같이 함께 DUR 임신 임부 임산부 임신부 고령 노인 어르신 세 살".split())

_INFO_KW = ("부작용", "효능", "효과", "복용법", "복용 방법", "복용방법", "사용법", "용법",
            "보관법", "보관 방법", "주의사항", "어떻게 먹", "어떻게 복용", "어떤 약")
_DUR_KW = ("병용금기", "병용 금기", "같이 먹", "같이 복용", "같이 처방", "함께 먹", "함께 복용",
           "병용", "DUR", "약물 상호작용", "상호작용", "같이 써", "같이 쓰")


def _patient_ctx(m):
    """비식별 환자 컨텍스트(나이·임신) 추출. 고령/노인 명시는 age=75 추정(노인주의 트리거)."""
    age = None
    am = re.search(r"(\d{1,3})\s*[세살]", m)
    if am:
        age = int(am.group(1))
    elif re.search(r"(?:^|[^가-힣])(고령|노인|어르신)", m):  # 단어 경계(이소트레티'노인' 등 오인 방지)
        age = 75
    preg = True if any(k in m for k in ("임신", "임부", "임산부", "임신부")) else None
    return age, preg


def _extract_drugs(m, limit=8):
    """약물명 후보 추출. 제형 접미 제품명 우선, 없으면 연결어 분리+조사제거 토큰. 불용어 제외."""
    names = [fm.group(0) for fm in _FORM.finditer(m)
             if not fm.group(0)[0].isdigit() and fm.group(0) not in ("이번주", "지난주", "다음주")]
    if not names:
        for part in _CONN.split(m):
            if not part:
                continue
            for raw in re.findall(r"[가-힣A-Za-z0-9]{2,}", part):
                if raw[0].isdigit():      # 숫자 시작(75세·20주·500mg 등) = 약물 아님
                    continue
                t = _PARTICLE.sub("", raw)  # 후행 조사 제거
                if len(t) < 2 or t.isdigit() or t in _STOP:
                    continue
                if any(k in t for k in ("부작용", "효능", "복용", "사용", "병용", "금기",
                                        "주의", "상호작용", "처방", "보관")):
                    continue
                names.append(t)
                break
    seen, out = set(), []
    for x in names:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:limit]


def medical_direct_route(message):
    """명확한 의도+엔티티일 때만 (tool_name, action, input_data) 반환, 아니면 None(기존 흐름)."""
    m = message or ""
    c9 = _CODE9.search(m)
    c13 = _CODE13.search(m)
    # 1) 저가약 대체조제 (제품코드 + 대체 의도)
    if c9 and any(k in m for k in ("대체조제", "대체약", "대체 약", "장려금", "저가약")):
        return ("drug_substitution", "ai:/api/notice/drug-substitution", {"code": c9.group(1)})
    # 2) 의약품 코드 해석 (바코드/표준코드, 또는 제품코드+해석/분류 의도)
    if c13 or (c9 and any(k in m for k in ("표준코드", "바코드", "품목기준", "무슨 약", "어떤 약",
                                           "약인지", "코드 해석", "코드해석", "약가 코드", "수가 코드",
                                           "코드인지", "성분"))):
        return ("drug_resolve", "ai:/api/notice/drug-master/resolve",
                {"code": (c13 or c9).group(1)})
    # 2.5) DUR 점검 (병용금기/임부/노인 등) — 약물명 추출되면 결정적, 약하면 None(tool-calling 폴백)
    age, preg = _patient_ctx(m)
    _safe_q = any(k in m for k in ("먹어", "복용", "처방", "투여", "써도", "쓰면", "괜찮",
                                   "되나", "돼요", "돼?", "안전", "주의"))
    if any(k in m for k in _DUR_KW) or ((preg or age is not None) and _safe_q):
        drugs = _extract_drugs(m)
        # ≥2 약물(병용) 또는 1약물+환자컨텍스트일 때만 결정적 라우팅
        if len(drugs) >= 2 or (drugs and (preg or age is not None)):
            inp = {"drugs": drugs}
            if age is not None:
                inp["age"] = age
            if preg is not None:
                inp["pregnant"] = preg
            return ("dur_check", "ai:/api/medical/dur/check", inp)
    # 2.6) e약은요 환자용 약 정보 (부작용·효능·복용법) — 단일 약물
    if any(k in m for k in _INFO_KW):
        drugs = _extract_drugs(m)
        if drugs:
            return ("drug_info", "ai:/api/medical/drug-info", {"query": drugs[0]})
    # 3) 의료법/약사법/건강보험 법령 자문 (RAG 정본)
    if any(k in m for k in ("의료법", "약사법", "감염병예방법", "응급의료법", "진료기록", "진료기록부",
                            "보존기간", "보존 기간", "보관기간", "신고의무", "신고 의무", "동의서 요건",
                            "급여기준", "급여 기준", "대체조제 청구", "대체조제 장려금")):
        return ("medical_law_ask", "ai:/api/medical-law/ask", {"question": m})
    # 4) 청구 삭감/심사기준 검색 (RAG 정본)
    if any(k in m for k in ("삭감", "심사기준", "심사 기준")):
        return ("notice_search", "ai:/api/notice/search", {"query": m, "max_results": 5})
    return None


def summarize_medical_route(tool, result):
    """라우팅 결과 → 한국어 요약(원본 result 동봉, 정본 disclaimer는 result에 포함)."""
    if not isinstance(result, dict):
        return "처리되었습니다."
    if result.get("error"):
        return f"조회 중 문제가 발생했습니다. ({str(result.get('error'))[:80]})"
    if tool == "medical_law_ask":
        return result.get("answer") or "관련 법령 정보를 찾지 못했습니다."
    if tool == "drug_substitution":
        if not result.get("found"):
            return "해당 제품코드의 저가약 대체조제 정보를 찾지 못했습니다."
        alts = result.get("alternatives", []) or []
        s = (f"{result.get('product') or '이 약'}({result.get('code')}) — 장려금 대상: "
             f"{'예' if result.get('eligible') else '아니오'}, 대체 후보 "
             f"{result.get('alternatives_count', 0)}건(더 저렴 {result.get('cheaper_alternatives_count', 0)}건).")
        if alts:
            s += " 예: " + ", ".join(f"{a.get('product')}({a.get('price')}원)" for a in alts[:3])
        return s
    if tool == "drug_resolve":
        ms = result.get("matches", []) or []
        if not ms:
            return "해당 코드의 의약품을 찾지 못했습니다(약가마스터 미수록)."
        m0 = ms[0]
        return (f"{m0.get('name')} — 제품코드 {m0.get('product_code') or '-'}, 성분코드 "
                f"{m0.get('ingr_code') or '-'}, ATC {m0.get('atc') or '-'} ({m0.get('maker') or '-'}).")
    if tool == "notice_search":
        rs = result.get("results", []) or []
        if not rs:
            return "관련 심사기준 고시를 찾지 못했습니다."
        return "관련 심사기준: " + " · ".join((r.get("title") or str(r))[:40] for r in rs[:3])
    if tool == "dur_check":
        parts = []
        ci = result.get("contraindications") or []
        if ci:
            parts.append("병용금기: " + ", ".join(f"{a.get('drug_a')}+{a.get('drug_b')}" for a in ci[:3]))
        for key, lab in (("pregnancy_warnings", "임부금기"), ("elderly_warnings", "노인주의"),
                         ("age_warnings", "연령금기"), ("duplicate_therapy", "효능군중복"),
                         ("split_cautions", "서방정분할주의")):
            n = len(result.get(key) or [])
            if n:
                parts.append(f"{lab} {n}건")
        if not parts:
            rsv = [r for r in (result.get("resolved") or []) if not r.get("resolved")]
            if rsv:
                return "입력한 약을 DUR 성분으로 인식하지 못했습니다(성분명/코드로 다시 시도)."
            return "확인된 DUR 금기/주의가 없습니다(병용금기 등 해당 없음)."
        return "DUR 결과 — " + " · ".join(parts) + " (식약처 정본, 최종 판단은 처방의·약사)"
    if tool == "drug_info":
        ms = result.get("matches") or []
        if not ms:
            return "해당 약의 e약은요 정보를 찾지 못했습니다."
        m0 = ms[0]
        seg = [m0.get("name", "")]
        if m0.get("efficacy"):
            seg.append("효능: " + m0["efficacy"][:80])
        if m0.get("usage"):
            seg.append("복용법: " + m0["usage"][:80])
        if m0.get("side_effect"):
            seg.append("부작용: " + m0["side_effect"][:80])
        return " | ".join(seg)
    return result.get("message") or "처리되었습니다."
