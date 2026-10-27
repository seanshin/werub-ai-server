#!/usr/bin/env python3
# Created: 2026-04-21
# 외부 욕설 데이터셋 → WeRUB 학습 포맷 변환

import json, os, random

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

SYSTEM_PROMPT = """콘텐츠 모더레이터. 텍스트의 유해성을 JSON으로 판정.
카테고리: profanity, hate_speech, harassment, sexual, spam, misinformation, self_harm
action: allow, warn, review, block
정치적 의견, 비판, 풍자는 유해 아님. JSON만 응답."""

# S0~S16 공식 정의 기반 매핑
S_TO_CATEGORY = {
    # S0: Innocuousness → 정상
    "S1": "harassment",      # Violent Crimes → 폭력/위협
    "S2": "harassment",      # Non-Violent Crimes → 비폭력 범죄
    "S3": "sexual",          # Sex-Related Crimes → 성범죄
    "S4": "sexual",          # Child Sexual Exploitation → 아동 성착취 (block 필수)
    "S5": "harassment",      # Defamation → 명예훼손
    "S6": "misinformation",  # Specialized Advice → 전문 조언 오용
    "S7": "harassment",      # Privacy → 프라이버시 침해
    # S8: Intellectual Property → 해당 없음
    "S9": "self_harm",       # Indiscriminate Weapons → 무차별 무기
    "S10": "hate_speech",    # Hate → 혐오
    "S11": "self_harm",      # Suicide & Self-Harm → 자살/자해
    "S12": "sexual",         # Sexual Content → 성적 콘텐츠
    "S13": "misinformation", # Elections → 선거 관련 허위
    # S14: Code Interpreter Abuse → 해당 없음
    "S15": "profanity",      # Sarcasm → 비꼬기 (경미 욕설 포함)
    "S16": "spam",           # Spam → 스팸
}

def convert_label(row):
    label = row["label"]
    toxic = row["toxic_value"]
    sensitivity = row["sensitivity_value"]
    if label == 3: return "block"
    if label == 2: return "review"
    if label == 1: return "warn"
    if toxic == 1 and sensitivity >= 6: return "warn"
    return "allow"

def convert_categories(row):
    cats = set()
    cl = row.get("llm_classifier_value", "S0")
    if cl in S_TO_CATEGORY:
        cats.add(S_TO_CATEGORY[cl])
    for i in range(1, 17):
        if row.get(f"S{i}", 0) > 0:
            k = f"S{i}"
            if k in S_TO_CATEGORY:
                cats.add(S_TO_CATEGORY[k])
            elif row.get(f"S{i}", 0) >= 4:
                cats.add("profanity")
    return list(cats)

def convert_confidence(row):
    sv = row.get("sensitivity_value", 2)
    hv = row.get("hate_value", 0)
    if sv >= 8: return 0.95
    if sv >= 6: return 0.75
    if hv > 5: return 0.80
    if sv >= 4: return 0.60
    return 0.90

def main():
    ext_path = os.path.join(DATA_DIR, "external_dataset.jsonl")
    if not os.path.exists(ext_path):
        print("[convert] ERROR: external_dataset.jsonl 없음")
        return

    # 외부 데이터 로드
    external = []
    with open(ext_path) as f:
        for line in f:
            if not line.strip(): continue
            external.append(json.loads(line))
    print(f"[convert] 외부 데이터 로드: {len(external)}건")

    # 변환
    converted = []
    stats = {"allow": 0, "warn": 0, "review": 0, "block": 0}
    for row in external:
        text = row.get("toxic_source", "").strip()
        if not text or len(text) < 3: continue

        action = convert_label(row)
        categories = convert_categories(row)
        confidence = convert_confidence(row)
        stats[action] += 1

        output = json.dumps({
            "flagged": action != "allow",
            "action": action,
            "categories": categories,
            "confidence": confidence,
            "explanation": "외부 데이터셋"
        }, ensure_ascii=False)

        converted.append({
            "instruction": SYSTEM_PROMPT,
            "input": f'텍스트: "{text}"',
            "output": output,
        })

    print(f"[convert] 변환 완료: {len(converted)}건")
    print(f"[convert] 분포: {stats}")

    # 기존 시드 로드
    seed_path = os.path.join(DATA_DIR, "raw_samples.jsonl")
    seeds = []
    if os.path.exists(seed_path):
        with open(seed_path) as f:
            for line in f:
                if not line.strip(): continue
                d = json.loads(line)
                text = d.get("text", "").strip()
                if not text: continue
                action = d.get("label", "allow")
                categories = d.get("categories", [])
                output = json.dumps({
                    "flagged": action != "allow",
                    "action": action,
                    "categories": categories,
                    "confidence": 0.95,
                    "explanation": "시드 데이터"
                }, ensure_ascii=False)
                seeds.append({
                    "instruction": SYSTEM_PROMPT,
                    "input": f'텍스트: "{text}"',
                    "output": output,
                })
        print(f"[convert] 시드 데이터: {len(seeds)}건")

    # 병합 + 셔플
    all_data = converted + seeds
    random.shuffle(all_data)
    print(f"[convert] 총 통합: {len(all_data)}건")

    # train/eval 분할 (80:20)
    split = int(len(all_data) * 0.8)
    train = all_data[:split]
    eval_data = all_data[split:]

    # 저장
    train_path = os.path.join(DATA_DIR, "train.jsonl")
    eval_path = os.path.join(DATA_DIR, "eval.jsonl")
    with open(train_path, "w") as f:
        for d in train:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(eval_path, "w") as f:
        for d in eval_data:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    print(f"[convert] train: {len(train)}건, eval: {len(eval_data)}건")
    print(f"[convert] 저장 완료")

if __name__ == "__main__":
    main()
