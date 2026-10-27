#!/usr/bin/env python3
"""qwen3-vl:8b 재PoC — "빈 응답"의 정체가 사고 예산이라면 어느 설정이 그걸 닫는가 (2026-08-19).

v2.125.9 §6: qwen3-vl:8b는 `think:false`를 무시(사고 302~1,286자)해 사고가 `num_predict`(300)를 먹고 사진 1건 빈 응답.
OCR·다중 이미지 구분은 최고였다. 여기서는 설정 4종을 같은 3과제(명함 OCR·사진·두 장)에 건다:
  A) generate · think:false · num_predict 300  (지난 PoC 재현 = 통제군)
  B) generate · think:false · num_predict 2048 (사고를 예산으로 흡수)
  C) chat     · think:false · num_predict 2048 (엔드포인트 차이)
  D) generate · 프롬프트 `/no_think` · num_predict 2048 (Qwen3 소프트 스위치)
📌 사전 등록 채택 기준(일반 비전 교체 후보 자격): 어떤 설정이든 ① 빈 응답 0/3 ② 명함 5필드 중 ≥4 정확 ③ 두 장 구분 ✅
  ④ 한국어 비율 ≥ 0.66(현행 minicpm-v 명함 값) ⑤ 과제당 지연 ≤ 16초(현행 최대 7.6초의 ~2배). 전부 충족 시 후보 → 그 다음
  라이선스·VRAM 공존·force_korean 후처리 포함 2차 판정. ⚠️ 판정은 원문을 읽고 한다.
"""
import base64
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from poc_vision_minicpm45 import CARD, card_png, pick_photo, ko_ratio  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen3-vl:8b"
CONFIGS = {
    "A_gen_think0_np300": {"ep": "generate", "think": False, "np": 300, "suffix": ""},
    "B_gen_think0_np2048": {"ep": "generate", "think": False, "np": 2048, "suffix": ""},
    "C_chat_think0_np2048": {"ep": "chat", "think": False, "np": 2048, "suffix": ""},
    "D_gen_nothink_np2048": {"ep": "generate", "think": None, "np": 2048, "suffix": " /no_think"},
}


def ask(cfg, prompt, images):
    b64 = [base64.b64encode(i).decode() for i in images]
    opts = {"temperature": 0, "num_predict": cfg["np"]}
    t = time.time()
    if cfg["ep"] == "generate":
        payload = {"model": MODEL, "prompt": prompt + cfg["suffix"], "stream": False, "keep_alive": "0",
                   "images": b64, "options": opts}
        if cfg["think"] is not None:
            payload["think"] = cfg["think"]
        r = requests.post(OLLAMA + "/api/generate", json=payload, timeout=900).json()
        resp, think = r.get("response") or "", r.get("thinking") or ""
    else:
        payload = {"model": MODEL, "stream": False, "keep_alive": "0", "options": opts,
                   "messages": [{"role": "user", "content": prompt + cfg["suffix"], "images": b64}]}
        if cfg["think"] is not None:
            payload["think"] = cfg["think"]
        r = requests.post(OLLAMA + "/api/chat", json=payload, timeout=900).json()
        m = r.get("message") or {}
        resp, think = m.get("content") or "", m.get("thinking") or ""
    return {"resp": resp.strip(), "sec": round(time.time() - t, 1), "thinking_len": len(think),
            "done_reason": r.get("done_reason"), "err": r.get("error")}


def main():
    card = card_png(); photo, ppath = pick_photo()
    print("photo:", ppath)
    out = {}
    for name, cfg in CONFIGS.items():
        print("\n=====", name, cfg)
        res = {}
        r = ask(cfg, "이 명함에서 이름, 직함, 회사명, 전화번호, 이메일을 그대로 적어 주세요. 한국어로.", [card])
        r["ocr_hits"] = {k: (v in r["resp"]) for k, v in CARD.items()}; res["card"] = r
        print("[card] %.1fs think=%d dr=%s empty=%s hits=%d/5 ko=%s\n  %s" % (
            r["sec"], r["thinking_len"], r["done_reason"], not r["resp"], sum(r["ocr_hits"].values()),
            ko_ratio(r["resp"]), r["resp"][:240].replace("\n", " | ")))
        r = ask(cfg, "이 이미지를 한국어로 두 문장으로 설명해 주세요.", [photo]); res["photo"] = r
        print("[photo] %.1fs think=%d dr=%s empty=%s ko=%s\n  %s" % (
            r["sec"], r["thinking_len"], r["done_reason"], not r["resp"], ko_ratio(r["resp"]),
            r["resp"][:240].replace("\n", " | ")))
        r = ask(cfg, "두 장의 이미지가 주어졌습니다. 첫 번째 이미지와 두 번째 이미지가 각각 무엇인지 따로 설명해 주세요. 한국어로.",
                [card, photo]); res["multi"] = r
        print("[multi] %.1fs think=%d dr=%s empty=%s ko=%s\n  %s" % (
            r["sec"], r["thinking_len"], r["done_reason"], not r["resp"], ko_ratio(r["resp"]),
            r["resp"][:400].replace("\n", " | ")))
        out[name] = res
    path = os.path.expanduser("~/WeRUBLLMManager/logs/poc_vision_qwen3vl_think.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    print("\n원자료:", path)


if __name__ == "__main__":
    main()
