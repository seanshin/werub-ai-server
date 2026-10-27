#!/usr/bin/env python3
# Created: 2026-04-20
# Content Safety — 학습 데이터 준비 (DB → JSONL → Alpaca 포맷)

import json, os, sys, random

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DATA_DIR, exist_ok=True)

SYSTEM_PROMPT = """콘텐츠 모더레이터. 텍스트의 유해성을 JSON으로 판정.
카테고리: profanity, hate_speech, harassment, sexual, spam, misinformation, self_harm
action: allow, warn, review, block
정치적 의견, 비판, 풍자는 유해 아님. JSON만 응답."""


def load_from_db():
    """WeRUBLLMManager training.db에서 학습 데이터 로드"""
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), "..", "training.db")
    if not os.path.exists(db_path):
        print(f"[prepare] training.db 없음: {db_path}")
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 외부 DB(PostgreSQL)의 training_samples는 직접 접근 불가
    # 대신 JSONL 파일에서 로드하는 방식도 지원
    conn.close()
    return []


def load_from_jsonl(path):
    """JSONL 파일에서 로드"""
    samples = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                samples.append(d)
            except json.JSONDecodeError:
                continue
    return samples


def to_alpaca(samples, train_split=0.8):
    """Alpaca 포맷으로 변환 + train/eval 분할"""
    alpaca = []
    for s in samples:
        text = s.get("text", "")
        label = s.get("label", s.get("admin_action", s.get("ai_action", "allow")))
        categories = s.get("categories", [])
        if isinstance(categories, str):
            try:
                categories = json.loads(categories)
            except (json.JSONDecodeError, TypeError):
                categories = []

        output = json.dumps({
            "flagged": label != "allow",
            "action": label,
            "categories": categories,
            "confidence": 0.95,
            "explanation": "학습 데이터"
        }, ensure_ascii=False)

        alpaca.append({
            "instruction": SYSTEM_PROMPT,
            "input": f'텍스트: "{text[:500]}"',
            "output": output,
        })

    random.shuffle(alpaca)
    split = int(len(alpaca) * train_split)
    return alpaca[:split], alpaca[split:]


def main():
    # 1. 데이터 소스 확인
    jsonl_path = os.path.join(DATA_DIR, "raw_samples.jsonl")
    if os.path.exists(jsonl_path):
        samples = load_from_jsonl(jsonl_path)
        print(f"[prepare] JSONL 로드: {len(samples)}건")
    else:
        samples = load_from_db()
        print(f"[prepare] DB 로드: {len(samples)}건")

    if len(samples) == 0:
        # 시드 데이터 생성 (데모용)
        print("[prepare] 데이터 없음 — 시드 데이터 생성")
        seeds = [
            {"text": "ㅅㅂ 이거 뭐야 진짜", "label": "block", "categories": ["profanity"]},
            {"text": "시1발 짜증나네", "label": "block", "categories": ["profanity"]},
            {"text": "개새끼 진짜", "label": "block", "categories": ["profanity"]},
            {"text": "병신 같은 놈", "label": "block", "categories": ["profanity"]},
            {"text": "죽여버리겠다", "label": "block", "categories": ["harassment"]},
            {"text": "여자는 그래서 안 돼", "label": "block", "categories": ["hate_speech"]},
            {"text": "카톡으로 문의주세요 010-1234-5678", "label": "block", "categories": ["spam"]},
            {"text": "좋은 글 감사합니다", "label": "allow", "categories": []},
            {"text": "정치인들 비리를 고발합니다", "label": "allow", "categories": []},
            {"text": "오늘 날씨가 좋네요", "label": "allow", "categories": []},
            {"text": "이 정책에 반대합니다", "label": "allow", "categories": []},
            {"text": "코드 리뷰 부탁드립니다", "label": "allow", "categories": []},
            {"text": "맛집 추천 부탁해요", "label": "allow", "categories": []},
            {"text": "이번 선거 결과가 마음에 안 드네", "label": "allow", "categories": []},
            {"text": "화가 나지만 참겠습니다", "label": "allow", "categories": []},
            {"text": "존나 맛있다 이거", "label": "warn", "categories": ["profanity"]},
            {"text": "미친 가격이네 ㅋㅋ", "label": "allow", "categories": []},
            {"text": "지랄하고 있네", "label": "warn", "categories": ["profanity"]},
            {"text": "성인 사이트 가입하세요", "label": "block", "categories": ["spam", "sexual"]},
            {"text": "백신 맞으면 죽는다", "label": "block", "categories": ["misinformation"]},
        ]
        # 시드 x5 반복 + 변형
        for _ in range(5):
            samples.extend(seeds)
        with open(jsonl_path, "w") as f:
            for s in seeds:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"[prepare] 시드 데이터 {len(seeds)}건 → raw_samples.jsonl 저장")

    # 2. Alpaca 변환 + 분할
    train_data, eval_data = to_alpaca(samples)
    print(f"[prepare] train: {len(train_data)}건, eval: {len(eval_data)}건")

    # 3. 저장
    train_path = os.path.join(DATA_DIR, "train.jsonl")
    eval_path = os.path.join(DATA_DIR, "eval.jsonl")
    with open(train_path, "w") as f:
        for d in train_data:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(eval_path, "w") as f:
        for d in eval_data:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    print(f"[prepare] 저장 완료: {train_path}, {eval_path}")
    return {"train": len(train_data), "eval": len(eval_data)}


if __name__ == "__main__":
    main()
