"""후단·단서 오독 탐지 — **답변의 '제외'가 근거에서는 '허용'인 경우** (E1 · v2.125.15).

id728②(2026-08-19 판독): 시행규칙 제40조 4호 후단
    "전문과목에 '치과'가 포함된 치과병원ㆍ치과의원의 경우에는 … 종류 명칭에서
     '치과'를 **생략할 수 있다**"                        ← 허용(permissive)
를 답변이
    "… 전문과목 삽입 가능 **(치과병원·치과의원 제외)**"   ← 배제(exclusion)
로 요약했다. 방향이 반대다. 충실도 judge는 이걸 통과시켰다(주장 내용이 근거
문장 안의 단어들로 구성돼 있어 supported로 보인다) — 법령명 오귀속과 같은
구조의 사각이라 **결정적 계산**으로만 잡는다(judge=의미 / 메트릭=형식 분업).

## 규율 (기획서 §3-E1)

- **탐지만 한다.** 발생 빈도를 모르는 결함에 처치(프롬프트 규칙)부터 넣지 않는다.
  이 모듈의 소비처는 관측(빈도 집계)이고, 처치는 관측이 끝난 뒤 별건이다.
- **좁게 잡는다.** 플래그 조건 = 답변이 괄호로 "(X … 제외)"를 달았고, 근거에서
  X가 나오는 문장 중 **배제 표지가 있는 문장이 하나도 없으며** 허용 표지
  ("…할 수 있다")가 있는 문장이 존재할 때. 그 외(엔티티 부재·근거가 실제로
  배제·예외조항 "그러하지 아니하다")는 전부 fail-open — 위양성이 축을 죽인다
  (v2.125.9의 22/40 위양성 선례).
- 통제군은 실전 **패턴의 종류**를 덮는다(오독·정독-제외 정당·예외조항 정독·
  패러프레이즈·엔티티 부재·배제/허용 혼재) — `tests/test_proviso_misread.py`.
"""
from __future__ import annotations

import re

__author__ = "신현묵 <8508132+seanshin@users.noreply.github.com>"

# 답변 쪽: 괄호 배제 부기 "(치과병원·치과의원 제외)" 류.
_EXCL_PAREN_RE = re.compile(r"[(（]([^()（）]{1,60}?)\s*(?:은|는|을|를|의\s*경우(?:에)?)?\s*제외[)）]")

# 엔티티 구분자: 가운뎃점 두 종·쉼표·'및'·'또는'
_ENTITY_SPLIT_RE = re.compile(r"[·ㆍ,]|\s및\s|\s또는\s")

# 근거 문장 쪽 표지 — 공백 제거 후 검사한다.
_PERMISSIVE_RE = re.compile(r"할수있다")
_EXCLUSIONARY_RE = re.compile(r"제외|그러하지아니|아니된다|아니한다|못한다|할수없다")

_WS = re.compile(r"\s+")
_SENT_SPLIT_RE = re.compile(r"(?<=다\.)|\n")


def _norm(text: str) -> str:
    return _WS.sub("", text or "")


def _entities(span: str) -> list[str]:
    """괄호 안 배제 대상 → 엔티티 목록. 2자 미만·비한글은 버린다."""
    out = []
    for part in _ENTITY_SPLIT_RE.split(span):
        e = _norm(part)
        if len(e) >= 2 and re.fullmatch(r"[가-힣]{2,20}", e) and e not in out:
            out.append(e)
    return out


def _evidence_sentences(evidence: str) -> list[str]:
    return [_norm(s) for s in _SENT_SPLIT_RE.split(evidence or "") if s.strip()]


def misread_exclusions(answer: str, evidence: str) -> list[dict]:
    """답변의 괄호 배제 부기 중 **근거가 허용으로 말하는** 엔티티 목록.

    반환: [{"entity": …, "answer_span": 괄호 원문, "evidence_head": 허용 문장 머리 80자}]
    비었으면 오독 미검출. 근거가 없거나 엔티티가 근거에 없으면 판정하지 않는다(fail-open).
    """
    if not answer or not evidence:
        return []
    sents = _evidence_sentences(evidence)
    if not sents:
        return []
    flags: list[dict] = []
    for m in _EXCL_PAREN_RE.finditer(answer):
        span = m.group(0)
        for entity in _entities(m.group(1)):
            holding = [s for s in sents if entity in s]
            if not holding:
                continue                      # 근거에 없으면 판정 불가(fail-open)
            if any(_EXCLUSIONARY_RE.search(s) for s in holding):
                continue                      # 근거 어딘가가 실제로 배제한다 = 정독
            permissive = [s for s in holding if _PERMISSIVE_RE.search(s)]
            if not permissive:
                continue                      # 허용 표지도 없으면 방향을 판정할 수 없다
            if any(f["entity"] == entity for f in flags):
                continue
            flags.append({"entity": entity, "answer_span": _WS.sub(" ", span),
                          "evidence_head": permissive[0][:80]})
    return flags
