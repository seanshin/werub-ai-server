#!/usr/bin/env python3
"""Medical AI Fine-tuning — Data Preparation

Sources:
1. medical.db consultations (서버 축적 데이터)
2. medical_gold.jsonl (수동 작성 골드 데이터)
3. Generated synthetic data (LLM-generated training pairs)

Output: training/data/medical/train.jsonl, eval.jsonl
"""
import json
import os
import random
import sqlite3
import sys
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)


def load_config():
    with open(os.path.join(SCRIPT_DIR, "medical_config.yaml")) as f:
        return yaml.safe_load(f)


def extract_db_samples(cfg):
    """medical.db에서 고품질 consultation 추출"""
    db_path = os.path.join(BASE_DIR, "medical.db")
    if not os.path.exists(db_path):
        print("[data] medical.db not found, skipping DB extraction")
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT action, input_text, output_text, model
        FROM medical_consultations
        WHERE output_text IS NOT NULL AND length(output_text) > 100
        ORDER BY created_at DESC
        LIMIT ?
    """, (cfg.get("max_samples", 50000),)).fetchall()
    conn.close()

    samples = []
    task_systems = cfg.get("tasks", {})
    for row in rows:
        action = row["action"]
        task_cfg = task_systems.get(action, {})
        system = task_cfg.get("system", "당신은 한국어 의료 AI입니다.").format(specialty="내과")

        samples.append({
            "instruction": system,
            "input": row["input_text"][:2000] if row["input_text"] else "",
            "output": row["output_text"][:3000] if row["output_text"] else "",
            "task": action,
            "source": "db",
        })
    print(f"[data] DB에서 {len(samples)}건 추출")
    return samples


def load_gold_samples(cfg):
    """수동 작성 골드 데이터 로드"""
    gold_path = os.path.join(cfg["data_dir"], "medical_gold.jsonl")
    if not os.path.exists(gold_path):
        print(f"[data] {gold_path} not found, skipping gold data")
        return []

    samples = []
    with open(gold_path) as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                samples.append({
                    "instruction": d.get("instruction", d.get("system", "")),
                    "input": d.get("input", d.get("question", "")),
                    "output": d.get("output", d.get("answer", "")),
                    "task": d.get("task", "general"),
                    "source": "gold",
                })
    print(f"[data] 골드 데이터 {len(samples)}건 로드")
    return samples


def generate_synthetic_samples():
    """SOAP 형식 합성 데이터 생성 (의료 기록 템플릿 기반)"""
    templates = [
        {
            "instruction": "당신은 내과 전문의를 보조하는 한국어 의료 AI입니다. 진료 기록을 SOAP 형식으로 요약하세요.",
            "input": "45세 남성, 주소: 흉통. 2일 전부터 좌측 흉부 통증 호소. 심호흡 시 악화. 활력징후: BP 130/85, HR 78, BT 36.8. 심전도 정상. Troponin 음성. 흉부 X-ray 정상.",
            "output": json.dumps({
                "subjective": "2일 전부터 좌측 흉부 통증. 심호흡 시 악화.",
                "objective": "BP 130/85, HR 78, BT 36.8. ECG 정상, Troponin(-), CXR 정상.",
                "assessment": "흉벽 통증 의심 (R07.4). 감별: 늑연골염, 근골격계 통증.",
                "plan": "NSAIDs 처방, 1주 후 추적, 증상 악화 시 심장 CT 고려.",
                "icd_codes": ["R07.4"],
                "key_findings": ["좌측 흉부 통증", "심호흡 시 악화", "심전도/Troponin 정상"],
                "urgency": "low"
            }, ensure_ascii=False),
            "task": "summarize",
        },
        {
            "instruction": "당신은 내과 전문의를 보조하는 한국어 의료 AI입니다. 증상/검사 결과를 분석하여 감별진단을 제시하세요.",
            "input": "62세 여성, 3일간 발열(38.5도), 기침, 가래. WBC 14,000, CRP 8.5, CXR 우하엽 경화.",
            "output": json.dumps({
                "differential_diagnosis": [
                    {"diagnosis": "지역사회획득 폐렴", "icd_code": "J18.9", "probability": "high", "reasoning": "발열+기침+CXR 경화+WBC/CRP 상승"},
                    {"diagnosis": "급성 기관지염", "icd_code": "J20.9", "probability": "medium", "reasoning": "기침+가래, 그러나 CXR 경화로 폐렴 가능성 높음"},
                    {"diagnosis": "폐결핵", "icd_code": "A15.0", "probability": "low", "reasoning": "급성 경과, 객혈 없음"}
                ],
                "recommended_tests": ["혈액배양", "객담 그람염색+배양", "Procalcitonin"],
                "urgency": "medium"
            }, ensure_ascii=False),
            "task": "analyze",
        },
        {
            "instruction": "당신은 환자에게 의학 정보를 쉽게 설명하는 한국어 의료 AI입니다.",
            "input": "진단: 제2형 당뇨병 (E11). 환자에게 질환과 관리 방법을 설명해주세요.",
            "output": "제2형 당뇨병은 우리 몸이 인슐린을 제대로 사용하지 못해서 혈당(피 속의 설탕)이 높아지는 병입니다.\n\n혈당이 오래 높으면 눈, 콩팥, 신경, 혈관에 문제가 생길 수 있어요. 하지만 잘 관리하면 합병증을 예방할 수 있습니다.\n\n관리 방법:\n1. 식이요법: 탄수화물을 줄이고, 채소와 단백질 위주로 드세요.\n2. 운동: 하루 30분 이상 걷기나 가벼운 운동을 하세요.\n3. 약물: 처방받은 약을 꼬박꼬박 드세요.\n4. 정기 검사: 3개월마다 당화혈색소(HbA1c) 검사를 받으세요.",
            "task": "explain",
        },
    ]

    # 템플릿 변형으로 추가 데이터 생성
    specialties = ["내과", "외과", "소아과", "산부인과", "정형외과"]
    samples = []
    for t in templates:
        for spec in specialties:
            sample = dict(t)
            sample["instruction"] = sample["instruction"].replace("내과", spec)
            sample["source"] = "synthetic"
            samples.append(sample)

    print(f"[data] 합성 데이터 {len(samples)}건 생성")
    return samples


def main():
    cfg = load_config()
    data_dir = cfg["data_dir"]
    os.makedirs(data_dir, exist_ok=True)

    # 데이터 수집
    all_samples = []
    all_samples.extend(extract_db_samples(cfg))
    all_samples.extend(load_gold_samples(cfg))
    all_samples.extend(generate_synthetic_samples())

    if not all_samples:
        print("[data] ERROR: 학습 데이터 없음")
        sys.exit(1)

    # 셔플 + 분할
    random.seed(42)
    random.shuffle(all_samples)

    split = int(len(all_samples) * cfg.get("train_split", 0.85))
    train = all_samples[:split]
    eval_data = all_samples[split:]

    # 저장
    train_path = os.path.join(data_dir, "train.jsonl")
    eval_path = os.path.join(data_dir, "eval.jsonl")

    with open(train_path, "w") as f:
        for s in train:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    with open(eval_path, "w") as f:
        for s in eval_data:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    # 통계
    task_counts = {}
    source_counts = {}
    for s in all_samples:
        task_counts[s.get("task", "unknown")] = task_counts.get(s.get("task", "unknown"), 0) + 1
        source_counts[s.get("source", "unknown")] = source_counts.get(s.get("source", "unknown"), 0) + 1

    print(f"\n[data] 총 {len(all_samples)}건 → train {len(train)} + eval {len(eval_data)}")
    print(f"[data] 태스크별: {json.dumps(task_counts, ensure_ascii=False)}")
    print(f"[data] 소스별: {json.dumps(source_counts, ensure_ascii=False)}")
    print(f"[data] 저장: {train_path}, {eval_path}")


if __name__ == "__main__":
    main()
