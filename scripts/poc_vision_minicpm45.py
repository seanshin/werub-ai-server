#!/usr/bin/env python3
"""minicpm-v4.5 일반 비전 PoC — 현행 minicpm-v(2.6)·qwen3-vl:8b와 실이미지 비교 (2026-08-19).

📌 사전 등록 채택 기준(전부 충족 시에만 VISION_MODEL 교체 후보):
  1. 빈 응답 0/N  (qwen3-vl:8b는 v2.118.2에서 빈 응답으로 탈락한 이력 — 새 엔진에서 재확인)
  2. 한글 명함 OCR: 이름·전화번호 **정확 일치**가 현행 이상 (그룹웨어 실측 결함: 이수경→이성奀)
  3. 다중 이미지 2장: 두 장을 **구분**해 서술(같은 설명 반복이면 실패 — 그룹웨어 "다중 이미지 붕괴")
  4. 한국어 비율 ≥ 현행 · 지연 ≤ 현행 1.5배
  ⚠️ 판정은 사람이 원문을 읽는다 — 이 스크립트는 수치와 원문을 나란히 놓을 뿐이다.
이미지: PIL로 그린 한글 명함(정답 알려짐) + 그림 1장(sana_out/characters 중 하나) — 실이미지 요구
(feedback_qwen3vl_vision_broken: 새 비전 모델은 실이미지로 response 확인 필수).
"""
import base64
import glob
import io
import json
import os
import sys
import time

import requests
from PIL import Image, ImageDraw, ImageFont

OLLAMA = "http://127.0.0.1:11434"
MODELS = ["minicpm-v:latest", "minicpm-v4.5:8b", "qwen3-vl:8b"]
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
CARD = {"name": "이수경", "title": "영업팀장", "company": "위럽 주식회사", "phone": "010-4271-3856",
        "email": "user@example.org"}


def card_png():
    im = Image.new("RGB", (900, 520), "white")
    d = ImageDraw.Draw(im)
    f_big, f_mid, f_sm = ImageFont.truetype(FONT, 64), ImageFont.truetype(FONT, 40), ImageFont.truetype(FONT, 34)
    d.text((60, 60), CARD["name"], fill="black", font=f_big)
    d.text((300, 80), CARD["title"], fill="#333", font=f_mid)
    d.text((60, 200), CARD["company"], fill="#0a4", font=f_mid)
    d.text((60, 300), "Tel " + CARD["phone"], fill="black", font=f_sm)
    d.text((60, 360), CARD["email"], fill="black", font=f_sm)
    d.rectangle((0, 470, 900, 520), fill="#0a4")
    b = io.BytesIO(); im.save(b, "PNG"); return b.getvalue()


def pick_photo():
    for pat in (os.path.expanduser("~/sana_out/*.png"), os.path.expanduser("~/WeRUBLLMManager/characters/*.png"),
                os.path.expanduser("~/ComfyUI/output/*.png")):
        fs = sorted(glob.glob(pat))
        if fs:
            return open(fs[-1], "rb").read(), fs[-1]
    return None, None


def ask(model, prompt, images, num_predict=300):
    payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": "0",
               "images": [base64.b64encode(i).decode() for i in images],
               "options": {"temperature": 0, "num_predict": num_predict}}
    if model.startswith("qwen3-vl"):
        payload["think"] = False
    t = time.time()
    r = requests.post(OLLAMA + "/api/generate", json=payload, timeout=600)
    j = r.json() if r.status_code == 200 else {"error": r.text[:200]}
    return {"resp": (j.get("response") or "").strip(), "sec": round(time.time() - t, 1),
            "thinking_len": len(j.get("thinking") or ""), "err": j.get("error")}


def ko_ratio(s):
    ko = sum(1 for c in s if "가" <= c <= "힣"); al = sum(1 for c in s if c.isalpha())
    return round(ko / al, 2) if al else 0.0


def main():
    card = card_png()
    photo, photo_path = pick_photo()
    print("photo:", photo_path)
    out = {}
    for m in MODELS:
        print("\n=====", m)
        res = {}
        r = ask(m, "이 명함에서 이름, 직함, 회사명, 전화번호, 이메일을 그대로 적어 주세요. 한국어로.", [card])
        hit = {k: (v in r["resp"]) for k, v in CARD.items()}
        r["ocr_hits"] = hit; res["card"] = r
        print("[card] %.1fs empty=%s hits=%s ko=%s\n  %s" % (r["sec"], not r["resp"], hit, ko_ratio(r["resp"]), r["resp"][:300].replace("\n", " | ")))
        if photo:
            r = ask(m, "이 이미지를 한국어로 두 문장으로 설명해 주세요.", [photo])
            res["photo"] = r
            print("[photo] %.1fs empty=%s ko=%s\n  %s" % (r["sec"], not r["resp"], ko_ratio(r["resp"]), r["resp"][:300].replace("\n", " | ")))
            r = ask(m, "두 장의 이미지가 주어졌습니다. 첫 번째 이미지와 두 번째 이미지가 각각 무엇인지 따로 설명해 주세요. 한국어로.", [card, photo], 400)
            res["multi"] = r
            print("[multi] %.1fs empty=%s ko=%s\n  %s" % (r["sec"], not r["resp"], ko_ratio(r["resp"]), r["resp"][:400].replace("\n", " | ")))
        out[m] = res
    path = os.path.expanduser("~/WeRUBLLMManager/logs/poc_vision_minicpm45.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    print("\n원자료:", path)


if __name__ == "__main__":
    main()
