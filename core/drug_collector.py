"""Drug Data Collector — 수집 어댑터

외부 소스에서 약물 데이터를 수집하여 staging_drug에 저장.
Phase A (MVP): LLMSeedAdapter — LLM 자체 지식으로 부족 카테고리 약물 생성
Phase B: MFDSAdapter — 식약처 nedrug API
"""
import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("ollama-admin")

# 부족 카테고리 시드 (LLM에게 요청할 목록)
SEED_CATEGORIES = [
    "항생제(카바페넴)", "항생제(세팔로스포린)", "항생제(페니실린)", "항진균제",
    "항바이러스제", "항결핵제", "항암제(표적)", "항암제(세포독성)",
    "면역억제제", "류마티스약물", "당뇨약", "고혈압약",
    "고지혈증약", "항혈소판제", "항응고제", "항부정맥제",
    "호흡기약", "소화기약", "정신과약", "마취제", "응급약물",
    "비뇨기약", "산부인과약", "안과약", "이비인후과약",
    "피부과약", "영양제/전해질", "해독제",
]


class DrugSourceAdapter(ABC):
    """수집 어댑터 공통 인터페이스."""

    @abstractmethod
    def fetch(self, conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
        """원시 데이터 수집. staging_drug에 삽입할 dict 리스트 반환."""
        ...

    @abstractmethod
    def source_name(self) -> str:
        ...

    @abstractmethod
    def source_tier(self) -> str:
        ...


class LLMSeedAdapter(DrugSourceAdapter):
    """LLM 자체 지식 기반 약물 생성 (Phase A MVP).

    DB에서 부족한 카테고리를 분석하고, 해당 카테고리의 약물을
    LLM에게 요청하기 위한 시드 프롬프트를 생성.
    """

    def source_name(self) -> str:
        return "LLM_SEED"

    def source_tier(self) -> str:
        return "T4"

    def fetch(self, conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
        # 현재 카테고리별 건수
        rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM drug_codes GROUP BY category"
        ).fetchall()
        existing = {r[0]: r[1] for r in rows}

        # 부족한 카테고리 찾기 (3건 미만)
        thin_cats = []
        for cat in SEED_CATEGORIES:
            # 부분 매칭
            count = sum(v for k, v in existing.items() if cat.split("(")[0] in (k or ""))
            if count < 3:
                thin_cats.append(cat)

        if not thin_cats:
            logger.info("[drug-collector] LLMSeed: no thin categories found")
            return []

        # 시드 레코드 생성
        records = []
        for cat in thin_cats[:limit]:
            raw = json.dumps({
                "request": "generate_drug",
                "category": cat,
                "instruction": f"'{cat}' 카테고리에서 한국 임상에서 자주 사용되는 약물 1개를 생성하세요. "
                               f"기존 DB에 없는 새로운 약물이어야 합니다.",
            }, ensure_ascii=False)

            records.append({
                "source": self.source_name(),
                "source_tier": self.source_tier(),
                "source_url": None,
                "raw_data": raw,
                "target_table": "drug_codes",
            })

        logger.info(f"[drug-collector] LLMSeed: {len(records)} seed records for {len(thin_cats)} thin categories")
        return records


class ManualAdapter(DrugSourceAdapter):
    """수동 JSON 업로드 어댑터."""

    def source_name(self) -> str:
        return "MANUAL"

    def source_tier(self) -> str:
        return "T3"

    def fetch(self, conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
        # 수동 업로드는 API를 통해 직접 staging에 삽입하므로
        # fetch에서는 빈 리스트 반환
        return []


# 어댑터 레지스트리
ADAPTERS: dict[str, DrugSourceAdapter] = {
    "LLM_SEED": LLMSeedAdapter(),
    "MANUAL": ManualAdapter(),
}


def get_adapter(name: str) -> Optional[DrugSourceAdapter]:
    return ADAPTERS.get(name)


def insert_to_staging(conn: sqlite3.Connection, records: list[dict], batch_run_id: int) -> int:
    """수집된 레코드를 staging_drug에 삽입."""
    inserted = 0
    for rec in records:
        conn.execute(
            """INSERT INTO staging_drug
               (source, source_tier, source_url, raw_data, target_table, batch_run_id, status)
               VALUES (?, ?, ?, ?, ?, ?, 'fetched')""",
            [
                rec["source"],
                rec["source_tier"],
                rec.get("source_url"),
                rec["raw_data"],
                rec["target_table"],
                batch_run_id,
            ],
        )
        inserted += 1
    conn.commit()
    return inserted
