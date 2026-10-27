#!/usr/bin/env python3
# Created: 2026-04-20
# Content Safety — LoRA merge → GGUF 변환 → Ollama 배포

import json, os, sys, subprocess, yaml


def main():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    output_dir = cfg["output_dir"]
    lora_dir = os.path.join(output_dir, "lora_adapter")
    if not os.path.exists(lora_dir):
        print("[deploy] ERROR: LoRA 어댑터 없음. train.py 먼저 실행")
        sys.exit(1)

    # 버전 결정
    import sqlite3
    db_path = os.path.join(os.path.dirname(__file__), "..", "training.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE IF NOT EXISTS training_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, base_model TEXT, status TEXT DEFAULT 'pending',
        f1_score REAL, created_at TEXT DEFAULT (datetime('now')), model_path TEXT,
        sample_count INTEGER DEFAULT 0, priority TEXT DEFAULT 'P2', trigger_type TEXT,
        started_at TEXT, completed_at TEXT, deployed_at TEXT, error_message TEXT,
        precision_score REAL, recall_score REAL, prev_f1_score REAL,
        correction_count INTEGER DEFAULT 0, synthetic_count INTEGER DEFAULT 0, clean_count INTEGER DEFAULT 0
    )""")
    version = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM training_runs").fetchone()[0]
    conn.close()

    model_name = f"{cfg['model_name_prefix']}:v{version}"
    print(f"[deploy] 모델: {model_name}")

    # 1. LoRA merge + GGUF 변환 (Unsloth 내장)
    print("[deploy] LoRA merge + GGUF 변환 시작...")
    gguf_dir = os.path.join(output_dir, f"gguf-v{version}")
    os.makedirs(gguf_dir, exist_ok=True)

    try:
        from unsloth import FastLanguageModel
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=lora_dir,
            max_seq_length=cfg["max_seq_length"],
            dtype=None,
            load_in_4bit=True,
        )
        # GGUF 저장 (Unsloth 내장 변환)
        model.save_pretrained_gguf(
            gguf_dir,
            tokenizer,
            quantization_method=cfg.get("quantization", "q4_k_m"),
        )
        print(f"[deploy] GGUF 변환 완료: {gguf_dir}")
    except Exception as e:
        print(f"[deploy] GGUF 변환 실패: {e}")
        sys.exit(1)

    # 2. GGUF 파일 찾기
    gguf_files = [f for f in os.listdir(gguf_dir) if f.endswith(".gguf")]
    if not gguf_files:
        print("[deploy] ERROR: GGUF 파일 없음")
        sys.exit(1)
    gguf_path = os.path.join(gguf_dir, gguf_files[0])
    print(f"[deploy] GGUF: {gguf_path} ({os.path.getsize(gguf_path) // 1024 // 1024}MB)")

    # 3. Modelfile 생성
    modelfile_path = os.path.join(gguf_dir, "Modelfile")
    with open(modelfile_path, "w") as f:
        f.write(f"FROM {gguf_path}\n")
        f.write('SYSTEM """콘텐츠 모더레이터. 텍스트의 유해성을 JSON으로 판정.\n')
        f.write("카테고리: profanity, hate_speech, harassment, sexual, spam, misinformation, self_harm\n")
        f.write("action: allow, warn, review, block\n")
        f.write("정치적 의견, 비판, 풍자는 유해 아님. JSON만 응답.\"\"\"\n")
        f.write("PARAMETER temperature 0.1\n")
        f.write("PARAMETER num_predict 200\n")

    # 4. Ollama 등록
    print(f"[deploy] Ollama 등록: {model_name}")
    r = subprocess.run(["ollama", "create", model_name, "-f", modelfile_path],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"[deploy] Ollama 등록 실패: {r.stderr}")
        sys.exit(1)
    print(f"[deploy] ✅ {model_name} 등록 완료")

    # latest 태그
    subprocess.run(["ollama", "create", f"{cfg['model_name_prefix']}:latest", "-f", modelfile_path],
                   capture_output=True, timeout=300)

    # 5. 결과 저장
    result = {"model_name": model_name, "gguf_path": gguf_path, "version": version}
    with open(os.path.join(output_dir, "deploy_result.json"), "w") as f:
        json.dump(result, f, indent=2)
    print(f"[deploy] 배포 완료: {json.dumps(result)}")


if __name__ == "__main__":
    main()
