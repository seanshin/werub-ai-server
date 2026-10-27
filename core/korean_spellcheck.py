"""한국어 맞춤법 검사 모듈 — Ollama LLM 기반 자체 검사

외부 API 의존 없이 로컬 Ollama LLM으로 맞춤법·띄어쓰기·문법 교정.
- qwen2.5:7b 모델 활용 (temperature=0.1, 교정 전용)
- 장문 자동 분할 (500자 단위)
- 교정 전후 diff 분석으로 에러 수 추정
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger("ollama-admin")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_MAX_CHARS = 500
_SPELL_MODEL = "qwen2.5:7b"
_SPELL_TIMEOUT = 30

_SPELL_SYSTEM = """당신은 한국어 맞춤법·띄어쓰기·문법 교정 전문가입니다.

[규칙]
1. 맞춤법, 띄어쓰기, 문법 오류만 교정하세요
2. 내용, 문체, 어순은 절대 변경하지 마세요
3. 교정된 텍스트만 출력하세요 (설명, 주석, 마크다운 없이)
4. 오류가 없으면 원문을 그대로 출력하세요
5. 줄바꿈, 문단 구조를 유지하세요

[교정 대상]
- 맞춤법 오류: 됬다→됐다, 웬지→왠지, 안됩니다→안 됩니다
- 띄어쓰기: 한국인 입니다→한국인입니다, 맛있는거→맛있는 거
- 조사 오류: 을/를, 은/는, 이/가 혼동
- 어미 오류: -데/-대, -든/-던 혼동"""

_session = requests.Session()


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _split_text(text: str, max_chars: int = _MAX_CHARS) -> list[str]:
    """텍스트를 max_chars 이하로 문장 단위 분할."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    sentences = re.split(r"(?<=[\.!?。])\s+", text)
    for sent in sentences:
        if len(current) + len(sent) + 1 <= max_chars:
            current = f"{current} {sent}".strip() if current else sent
        else:
            if current:
                chunks.append(current)
            if len(sent) > max_chars:
                for i in range(0, len(sent), max_chars):
                    chunks.append(sent[i : i + max_chars])
            else:
                current = sent
                continue
            current = ""
    if current:
        chunks.append(current)
    return chunks


def _count_corrections(original: str, corrected: str) -> int:
    """원문과 교정본의 단어 단위 차이 수."""
    if original == corrected:
        return 0
    orig_words = original.split()
    corr_words = corrected.split()
    # 간단한 diff: 길이가 같으면 다른 위치 수, 다르면 대칭 차이
    if len(orig_words) == len(corr_words):
        return sum(1 for a, b in zip(orig_words, corr_words) if a != b)
    return abs(len(orig_words) - len(corr_words)) + sum(
        1 for a, b in zip(orig_words, corr_words) if a != b
    )


def _find_corrections(original: str, corrected: str) -> list[dict[str, str]]:
    """원문과 교정본 비교 → 교정 목록 추출."""
    if original == corrected:
        return []
    orig_words = original.split()
    corr_words = corrected.split()
    corrections = []
    for a, b in zip(orig_words, corr_words):
        if a != b:
            corrections.append({"original": a, "corrected": b})
    return corrections


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def spellcheck(text: str) -> dict[str, Any]:
    """단일 텍스트 맞춤법 검사 (Ollama LLM 기반).

    Returns:
        dict: {
            "success": bool,
            "original": str,
            "checked": str,
            "errors": int,
            "corrections": list[{original, corrected}],
            "duration_ms": int,
            "error_detail": str | None
        }
    """
    if not text or not text.strip():
        return {
            "success": True,
            "original": text,
            "checked": text,
            "errors": 0,
            "corrections": [],
            "duration_ms": 0,
            "error_detail": None,
        }

    start = time.time()
    try:
        from core.config import OLLAMA_BASE
        base = OLLAMA_BASE or "http://localhost:11434"

        # v2.124.98 task 라우터 경유 — 🔴 모듈의 `_session`을 그대로 넘긴다
        # (어댑터가 자기 세션을 만들면 커넥션 풀을 잃는다).
        from core import model_router
        res = model_router.generate(
            "korean.spellcheck",
            f"다음 텍스트의 맞춤법과 띄어쓰기를 교정하세요:\n\n{text}",
            system=_SPELL_SYSTEM,
            options={"temperature": 0.1, "num_predict": int(len(text) * 1.5)},
            timeout=_SPELL_TIMEOUT,
            adapter=model_router.OllamaAdapter(base=base, session=_session))
        if not res.ok:
            raise RuntimeError(res.error or "generate failed")
        checked = (res.text or "").strip()

        # LLM이 마크다운이나 설명을 추가한 경우 정리
        if checked.startswith("```"):
            checked = re.sub(r"^```\w*\n?", "", checked)
            checked = re.sub(r"\n?```$", "", checked)
        checked = checked.strip()

        if not checked:
            checked = text

        errors = _count_corrections(text, checked)
        corrections = _find_corrections(text, checked)

        return {
            "success": True,
            "original": text,
            "checked": checked,
            "errors": errors,
            "corrections": corrections,
            "duration_ms": int((time.time() - start) * 1000),
            "error_detail": None,
        }
    except Exception as e:
        logger.warning(f"Spellcheck failed: {e}")
        return {
            "success": False,
            "original": text,
            "checked": text,
            "errors": 0,
            "corrections": [],
            "duration_ms": int((time.time() - start) * 1000),
            "error_detail": str(e)[:200],
        }


def spellcheck_long(text: str) -> dict[str, Any]:
    """장문 텍스트 맞춤법 검사 (자동 분할 + 병합).

    Returns:
        dict: {
            "success": bool,
            "original": str,
            "checked": str,
            "total_errors": int,
            "chunks_count": int,
            "chunks": list[dict],
            "duration_ms": int,
        }
    """
    if not text or not text.strip():
        return {
            "success": True,
            "original": text,
            "checked": text,
            "total_errors": 0,
            "chunks_count": 0,
            "chunks": [],
            "duration_ms": 0,
        }

    start = time.time()
    chunks = _split_text(text)
    results: list[dict] = []
    checked_parts: list[str] = []
    total_errors = 0
    all_success = True

    for chunk in chunks:
        r = spellcheck(chunk)
        results.append(r)
        checked_parts.append(r["checked"])
        total_errors += r["errors"]
        if not r["success"]:
            all_success = False

    checked = " ".join(checked_parts)

    return {
        "success": all_success,
        "original": text,
        "checked": checked,
        "total_errors": total_errors,
        "chunks_count": len(chunks),
        "chunks": results,
        "duration_ms": int((time.time() - start) * 1000),
    }


def get_spellcheck_summary(result: dict[str, Any]) -> dict[str, Any]:
    """API 응답에 첨부할 경량 맞춤법 요약."""
    if "total_errors" in result:
        return {
            "errors": result["total_errors"],
            "chunks": result["chunks_count"],
            "success": result["success"],
            "duration_ms": result["duration_ms"],
        }
    else:
        return {
            "errors": result["errors"],
            "success": result["success"],
            "duration_ms": result["duration_ms"],
            "corrections_count": len(result.get("corrections", [])),
        }
