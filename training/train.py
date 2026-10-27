#!/usr/bin/env python3
# Created: 2026-04-20
# Content Safety — Unsloth QLoRA Fine-tuning

import json, os, sys, signal, yaml

def main():
    # Config
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    data_dir = cfg.get("data_dir", "./training/data")
    train_path = os.path.join(data_dir, "train.jsonl")
    if not os.path.exists(train_path):
        print("[train] ERROR: train.jsonl 없음. prepare_data.py 먼저 실행")
        sys.exit(1)

    print(f"[train] 모델: {cfg['base_model']}")
    print(f"[train] QLoRA r={cfg['lora_r']}, alpha={cfg['lora_alpha']}")
    print(f"[train] epochs={cfg['num_epochs']}, batch={cfg['batch_size']}, lr={cfg['learning_rate']}")

    # 1. Unsloth 모델 로드
    from unsloth import FastLanguageModel
    import torch

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=cfg["base_model"],
        max_seq_length=cfg["max_seq_length"],
        dtype=None,  # auto
        load_in_4bit=True,
    )
    print(f"[train] 모델 로드 완료, VRAM: {torch.cuda.memory_allocated()//1024**2}MB")

    # 2. LoRA 어댑터 추가
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
    print(f"[train] 데이터 로드: {len(dataset)}건")

    # Alpaca 포맷 → 프롬프트 변환
    alpaca_prompt = """### Instruction:
{instruction}

### Input:
{input}

### Response:
{output}"""

    def format_prompts(examples):
        texts = []
        for inst, inp, out in zip(examples["instruction"], examples["input"], examples["output"]):
            texts.append(alpaca_prompt.format(instruction=inst, input=inp, output=out) + tokenizer.eos_token)
        return {"text": texts}

    dataset = dataset.map(format_prompts, batched=True)

    # 4. Trainer 설정
    from trl import SFTTrainer
    from transformers import TrainingArguments

    output_dir = os.path.join(cfg["output_dir"], "checkpoints")
    os.makedirs(output_dir, exist_ok=True)

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

    # 5. SIGTSTP 핸들링 (선점 대응)
    paused = False
    def handle_pause(signum, frame):
        nonlocal paused
        print("[train] SIGTSTP — 체크포인트 저장 중...")
        trainer.save_model(os.path.join(output_dir, "checkpoint-paused"))
        paused = True
        signal.pause()  # SIGCONT 대기

    def handle_resume(signum, frame):
        nonlocal paused
        print("[train] SIGCONT — 학습 재개")
        paused = False

    signal.signal(signal.SIGTSTP, handle_pause)
    signal.signal(signal.SIGCONT, handle_resume)

    # 6. 학습 실행
    print("[train] 학습 시작...")
    stats = trainer.train()
    print(f"[train] 학습 완료: loss={stats.training_loss:.4f}, runtime={stats.metrics.get('train_runtime', 0):.0f}s")

    # 7. LoRA 어댑터 저장
    lora_dir = os.path.join(cfg["output_dir"], "lora_adapter")
    model.save_pretrained(lora_dir)
    tokenizer.save_pretrained(lora_dir)
    print(f"[train] LoRA 어댑터 저장: {lora_dir}")

    # 8. 결과 JSON 저장
    result = {
        "status": "completed",
        "loss": stats.training_loss,
        "runtime_sec": stats.metrics.get("train_runtime", 0),
        "epochs": cfg["num_epochs"],
        "samples": len(dataset),
        "lora_dir": lora_dir,
    }
    with open(os.path.join(cfg["output_dir"], "train_result.json"), "w") as f:
        json.dump(result, f, indent=2)
    print(f"[train] 결과 저장: {json.dumps(result)}")


if __name__ == "__main__":
    main()
