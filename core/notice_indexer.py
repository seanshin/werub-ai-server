"""공시 정규화/해시/청크 — erp-notice-rag Day 1.

설계: docs/02-design/features/erp-notice-rag.design.md §5
정규화 7항은 ERP 측과 합의된 연동 스펙(회신 B-4) — 변경 시 양측 동시 변경 + 전체 재해시 필요.

  1. 인코딩: UTF-8 (BOM 제거)          5. 문서 끝: 마지막 개행 1개로 정규화
  2. 유니코드 정규화: NFC               6. 그 외 공백·연속 공백 보존 (표 서식 보호)
  3. 개행: CRLF/CR → LF 통일           7. 해시: 정규화된 바이트열 SHA-256
  4. 행 끝 trailing whitespace 제거

테스트 벡터: tests/data/notice_hash_vectors.json (ERP와 교환하는 파일과 동일본)
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

# 청크 정책 (design §5)
CHUNK_SIZE = 512          # 슬라이딩 폴백 크기 (자)
CHUNK_OVERLAP = 64
MAX_ARTICLE_CHUNK = 1500  # 조문 청크가 이보다 크면 내부를 다시 슬라이딩 분할

# 조문/항목 경계 패턴 — 행 시작 기준
_ARTICLE_RE = re.compile(
    r"(?m)^(?="
    r"제\s?\d+\s?조(?:의\s?\d+)?"   # 제1조, 제12조의2
    r"|\[?별표\s?\d*\]?"            # 별표, [별표 1]
    r"|\[붙임\s?\d*\]"              # [붙임], [붙임 2]
    r"|부\s?칙"                     # 부칙
    r")"
)


def normalize_body(text: str) -> bytes:
    """정규화 7항 적용 → UTF-8 바이트열. (해시·저장 공통 입력)"""
    if text is None:
        return b""
    # 1. BOM 제거 (선두 U+FEFF — 다중 BOM 방어)
    while text.startswith("﻿"):
        text = text[1:]
    # 2. NFC
    text = unicodedata.normalize("NFC", text)
    # 3. 개행 통일
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 4. 행 끝 trailing whitespace 제거 (탭/스페이스 — 행 내부 공백은 보존: 6항)
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))
    # 5. 문서 끝: 마지막 개행 정확히 1개
    text = text.rstrip("\n") + "\n" if text.strip("\n") else ""
    # 7. 바이트열
    return text.encode("utf-8")


def content_hash(text: str) -> str:
    """정규화 본문의 SHA-256 — 'sha256:' 프리픽스 포함 (ERP 대조 형식)."""
    return "sha256:" + hashlib.sha256(normalize_body(text)).hexdigest()


def verify_hash(text: str, claimed: str) -> bool:
    """ERP 송신 해시와 당측 재계산 대조. 프리픽스 유무 모두 허용."""
    ours = content_hash(text)
    claimed = (claimed or "").strip().lower()
    if not claimed:
        return False
    if not claimed.startswith("sha256:"):
        claimed = "sha256:" + claimed
    return ours == claimed


@dataclass
class Chunk:
    seq: int
    text: str
    kind: str  # article | sliding


def _sliding(text: str, start_seq: int, kind: str = "sliding") -> list[Chunk]:
    """512자 슬라이딩(오버랩 64) 분할."""
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    i, seq = 0, start_seq
    while i < len(text):
        piece = text[i:i + CHUNK_SIZE].strip()
        if piece:
            chunks.append(Chunk(seq=seq, text=piece, kind=kind))
            seq += 1
        if i + CHUNK_SIZE >= len(text):
            break
        i += step
    return chunks


def chunk_notice(body: str) -> list[Chunk]:
    """공시 본문 청크 분할.

    ① 조문 패턴(제N조/별표/[붙임]/부칙) 경계 분할 시도 — 법령 검색 품질 우선
    ② 조문이 2개 미만이면(비정형 문서) 512자 슬라이딩 폴백
    ③ 과대 조문(>1500자)은 내부를 다시 슬라이딩 분할
    """
    text = normalize_body(body).decode("utf-8")
    if not text.strip():
        return []

    parts = [p for p in _ARTICLE_RE.split(text) if p and p.strip()]
    if len(parts) < 2:
        return _sliding(text, 0)

    chunks: list[Chunk] = []
    seq = 0
    for part in parts:
        part = part.strip()
        if len(part) <= MAX_ARTICLE_CHUNK:
            chunks.append(Chunk(seq=seq, text=part, kind="article"))
            seq += 1
        else:
            sub = _sliding(part, seq, kind="article")
            chunks.extend(sub)
            seq += len(sub)
    return chunks
