#!/usr/bin/env python3
"""Medical AI Fine-tuning — QLoRA Training + GGUF Export + Ollama Deploy

Usage:
  1. python training/prepare_medical_data.py   # 데이터 준비
  2. python training/train_medical.py           # 학습 + 배포
  3. python training/eval_medical.py            # 평가 (선택)

Requirements: unsloth, torch, transformers, peft, trl, datasets, bitsandbytes
GPU: RTX 5080 16GB (QLoRA 4bit → ~6GB VRAM)
"""
import json
import os
import signal
import subprocess
import sys
import time
import yaml

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SCRIPT_DIR)


def load_config():
    with open(os.path.join(SCRIPT_DIR, "medical_config.yaml")) as f:
        return yaml.safe_load(f)


def train(cfg):
    """QLoRA 학습"""
    data_dir = cfg["data_dir"]
    train_path = os.path.join(data_dir, "train.jsonl")
    if not os.path.exists(train_path):
        print("[train] ERROR: train.jsonl 없음. prepare_medical_data.py 먼저 실행")
        sys.exit(1)

    print(f"[train] 모델: {cfg['base_model']}")
    print(f"[train] QLoRA r={cfg['lora_r']}, alpha={cfg['lora_alpha']}")
    print(f"[train] epochs={cfg['num_epochs']}, batch={cfg['batch_size']}, lr={cfg['learning_rate']}")

    # 0. VRAM 확보 — Ollama + ComfyUI 프로세스 중지
    print("[train] VRAM 확보: Ollama + ComfyUI 중지...")
    try:
        subprocess.run(["sudo", "systemctl", "stop", "comfyui"],
                       capture_output=True, timeout=10)
        print("  ✓ ComfyUI 중지")
    except Exception as e:
        print(f"  ⚠ ComfyUI 중지 실패: {e}")

    try:
        subprocess.run(["sudo", "systemctl", "stop", "ollama"],
                       capture_output=True, timeout=10)
        print("  ✓ Ollama 중지")
    except Exception as e:
        print(f"  ⚠ Ollama 중지 실패: {e}")

    time.sleep(3)
    print("  VRAM 확보 완료")

    # 1. 모델 로드
    from unsloth import FastLanguageModel
    import torch

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=cfg["base_model"],
        max_seq_length=cfg["max_seq_length"],
        dtype=None,
        load_in_4bit=True,
    )
    print(f"[train] 모델 로드 완료, VRAM: {torch.cuda.memory_allocated() // 1024**2}MB")

    # 2. LoRA 어댑터
    model = FastLanguageModel.get_peft_model(
        model,
        r=cfg["lora_r"],
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=cfg["lora_alpha"],
        lora_dropout=cfg.get("lora_dropout", 0.05),
        bias="none",
        use_gradient_checkpointing="unsloth",
    )
    print("[train] LoRA 어댑터 추가 완료")

    # 3. 데이터 로드
    from datasets import load_dataset
    dataset = load_dataset("json", data_files={"train": train_path}, split="train")
    print(f"[train] 데이터: {len(dataset)}건")

    # ChatML 포맷 (Qwen2.5 네이티브)
    def format_prompts(examples):
        texts = []
        for inst, inp, out in zip(examples["instruction"], examples["input"], examples["output"]):
            text = (
                f"<|im_start|>system\n{inst}<|im_end|>\n"
                f"<|im_start|>user\n{inp}<|im_end|>\n"
                f"<|im_start|>assistant\n{out}<|im_end|>"
            )
            texts.append(text)
        return {"text": texts}

    dataset = dataset.map(format_prompts, batched=True)

    # 4. Trainer
    from trl import SFTTrainer
    from transformers import TrainingArguments

    output_dir = os.path.join(cfg["output_dir"], "checkpoints")
    os.makedirs(output_dir, exist_ok=True)

    import torch
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=cfg["max_seq_length"],
        dataset_num_proc=2,
        packing=True,
        args=TrainingArguments(
            output_dir=output_dir,
            per_device_train_batch_size=cfg["batch_size"],
            gradient_accumulation_steps=cfg["gradient_accumulation_steps"],
            num_train_epochs=cfg["num_epochs"],
            learning_rate=cfg["learning_rate"],
            warmup_ratio=cfg.get("warmup_ratio", 0.1),
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=5,
            save_strategy="epoch",
            seed=42,
            report_to="none",
        ),
    )

    # 5. GPU 선점 대응
    paused = False

    def handle_pause(signum, frame):
        nonlocal paused
        print("[train] SIGTSTP — 체크포인트 저장...")
        trainer.save_model(os.path.join(output_dir, "checkpoint-paused"))
        paused = True
        signal.pause()

    def handle_resume(signum, frame):
        nonlocal paused
        print("[train] SIGCONT — 학습 재개")
        paused = False

    signal.signal(signal.SIGTSTP, handle_pause)
    signal.signal(signal.SIGCONT, handle_resume)

    # 6. 학습
    print("[train] 학습 시작...")
    start = time.time()
    stats = trainer.train()
    elapsed = time.time() - start
    print(f"[train] 완료: loss={stats.training_loss:.4f}, {elapsed:.0f}초")

    # 7. LoRA 저장
    lora_dir = os.path.join(cfg["output_dir"], "lora_adapter")
    model.save_pretrained(lora_dir)
    tokenizer.save_pretrained(lora_dir)
    print(f"[train] LoRA 저장: {lora_dir}")

    return {
        "loss": stats.training_loss,
        "runtime_sec": elapsed,
        "epochs": cfg["num_epochs"],
        "samples": len(dataset),
        "lora_dir": lora_dir,
    }


def export_gguf(cfg, version):
    """LoRA merge → GGUF 변환"""
    lora_dir = os.path.join(cfg["output_dir"], "lora_adapter")
    gguf_dir = os.path.join(cfg["output_dir"], f"gguf-v{version}")
    os.makedirs(gguf_dir, exist_ok=True)

    print("[export] LoRA merge + GGUF 변환...")
    from unsloth import FastLanguageModel
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=lora_dir,
        max_seq_length=cfg["max_seq_length"],
        dtype=None,
        load_in_4bit=True,
    )
    model.save_pretrained_gguf(
        gguf_dir, tokenizer,
        quantization_method=cfg.get("quantization", "q4_k_m"),
    )
    print(f"[export] GGUF: {gguf_dir}")
    return gguf_dir


def deploy_ollama(cfg, gguf_dir, version):
    """Ollama에 모델 등록"""
    model_name = f"{cfg['model_name_prefix']}:v{version}"
    gguf_files = [f for f in os.listdir(gguf_dir) if f.endswith(".gguf")]
    if not gguf_files:
        print("[deploy] ERROR: GGUF 파일 없음")
        return None

    gguf_path = os.path.join(gguf_dir, gguf_files[0])

    # Modelfile
    modelfile = os.path.join(gguf_dir, "Modelfile")
    with open(modelfile, "w") as f:
        f.write(f"FROM {gguf_path}\n")
        f.write('SYSTEM """당신은 한국어 의료 AI 어시스턴트입니다.\n')
        f.write("진료 기록 요약(SOAP), 감별진단, 의학 참고, 환자 설명문, 의무기록 초안을 작성합니다.\n")
        f.write("반드시 한국어로 답변하세요. 의학 용어는 정확하게 사용하세요.\n")
        f.write('면책: AI 참고용이며 최종 판단은 담당 의사가 수행합니다."""\n')
        f.write("PARAMETER temperature 0.2\n")
        f.write("PARAMETER num_predict 2000\n")

    # 등록
    print(f"[deploy] Ollama 등록: {model_name}")
    r = subprocess.run(["ollama", "create", model_name, "-f", modelfile],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print(f"[deploy] 실패: {r.stderr}")
        return None

    # latest 태그
    subprocess.run(["ollama", "create", f"{cfg['model_name_prefix']}:latest", "-f", modelfile],
                   capture_output=True, timeout=300)

    print(f"[deploy] ✅ {model_name} 배포 완료")
    return model_name


def main():
    cfg = load_config()

    # 버전 결정
    output_dir = cfg["output_dir"]
    os.makedirs(output_dir, exist_ok=True)
    existing = [d for d in os.listdir(output_dir) if d.startswith("gguf-v")]
    version = len(existing) + 1

    print(f"{'='*50}")
    print(f" Medical AI Fine-tuning v{version}")
    print(f" Base: {cfg['base_model']}")
    print(f" Target: {cfg['model_name_prefix']}:v{version}")
    print(f"{'='*50}")

    # 1. 학습
    train_result = train(cfg)

    # 2. GGUF 변환
    gguf_dir = export_gguf(cfg, version)

    # 3. Ollama 재시작 + 배포
    print("[deploy] Ollama 재시작...")
    subprocess.run(["sudo", "systemctl", "start", "ollama"], capture_output=True, timeout=15)
    time.sleep(5)
    model_name = deploy_ollama(cfg, gguf_dir, version)

    # ComfyUI는 on-demand이므로 재시작 불필요

    # 4. 결과 저장
    result = {
        "version": version,
        "model_name": model_name,
        "train": train_result,
        "gguf_dir": gguf_dir,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    result_path = os.path.join(output_dir, f"result-v{version}.json")
    with open(result_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f" 완료: {model_name}")
    print(f" Loss: {train_result['loss']:.4f}")
    print(f" 소요: {train_result['runtime_sec']:.0f}초")
    print(f" 결과: {result_path}")
    print(f"{'='*50}")

    # medical_config.yaml의 모델을 업데이트하라고 안내
    if model_name:
        print(f"\n[INFO] routes/medical.py에서 모델 교체:")
        print(f'  HEAVY_MODEL = "{model_name}"  # 또는 "{cfg["model_name_prefix"]}:latest"')


if __name__ == "__main__":
    main()
