"""WER/CER — 순수 Python 단어/문자 오류율 (외부 의존 0).

X10 STT 위임 검증용(x10-delegation-equivalence-gate.plan.md §B). jiwer 등 외부
라이브러리 대신 Levenshtein을 자체 구현한다(CLAUDE.md 외부 의존 0 규율).

- WER: 어절(공백) 단위 편집거리 / 참조 어절 수 (한국어 표준).
- CER: 문자 단위 편집거리 / 참조 문자 수 (어절 경계 모호한 한국어 보조 지표).
정규화: 유니코드 NFC · 문장부호 제거 · 영문 소문자화 · 연속 공백 1칸.
"""
from __future__ import annotations

import re
import unicodedata

# 제거할 문장부호(한글/영문/숫자/공백만 남긴다). 한국어 낫표·따옴표·구두점 포함.
_PUNCT = re.compile(r"[^\w\s가-힣]", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """WER/CER 공통 전처리 — NFC·문장부호 제거·소문자·공백 정규화."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", text)
    t = _PUNCT.sub(" ", t)
    t = t.lower()
    t = _WS.sub(" ", t).strip()
    return t


def _levenshtein(ref: list, hyp: list) -> int:
    """토큰 시퀀스 편집거리(삽입·삭제·치환 각 1). 행 2줄 DP — O(len)."""
    if not ref:
        return len(hyp)
    if not hyp:
        return len(ref)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cost = 0 if r == h else 1
            cur.append(min(prev[j] + 1,        # 삭제
                           cur[j - 1] + 1,      # 삽입
                           prev[j - 1] + cost)) # 치환/일치
        prev = cur
    return prev[-1]


def wer(reference: str, hypothesis: str) -> float:
    """어절 단위 WER. 참조가 비면 가설도 비었을 때만 0.0, 아니면 1.0."""
    ref = normalize(reference).split()
    hyp = normalize(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def cer(reference: str, hypothesis: str) -> float:
    """문자 단위 CER(공백 제외). 참조가 비면 가설도 비었을 때만 0.0, 아니면 1.0."""
    ref = list(normalize(reference).replace(" ", ""))
    hyp = list(normalize(hypothesis).replace(" ", ""))
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def wer_report(reference: str, hypothesis: str) -> dict:
    """WER·CER + 참조/가설 어절 수(표본 크기 판독용)."""
    return {
        "wer": round(wer(reference, hypothesis), 4),
        "cer": round(cer(reference, hypothesis), 4),
        "ref_words": len(normalize(reference).split()),
        "hyp_words": len(normalize(hypothesis).split()),
    }
