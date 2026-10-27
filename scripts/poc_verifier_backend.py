#!/usr/bin/env python3
"""교차검증 백엔드 대체 PoC — bespoke-minicheck(CC BY-NC 4.0) → Apache-2.0 대체.

v2.120.0 한국어 변별 PoC 클래스(수치 스왑·한정조건 드롭·호 오배치·패러프레이즈)
+ 이후 폐루프에서 실증된 난문 클래스(id236 간접 함의=bespoke 위음성·id240 항 구분·
id253 반대 요약·id257 결합 조건)로 12페어 구성 — 원 PoC보다 엄격.

백엔드:
  - minicheck    : bespoke-minicheck (참고 기준선 — NC 라이선스로 대체 대상)
  - qwen-entail  : qwen2.5 계열 + format 제약 {"supported": bool} (Apache-2.0)

사용: ./venv/bin/python scripts/poc_verifier_backend.py [--models qwen2.5:7b,qwen2.5:14b]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# (라벨, 근거, 주장, 기대값)
PAIRS = [
    ("supported-기본",
     "제22조 진료기록부는 10년, 처방전은 2년 보존한다.",
     "진료기록부 보존기간은 10년이다.", True),
    ("수치-스왑",
     "제22조 진료기록부는 10년, 처방전은 2년 보존한다.",
     "진료기록부 보존기간은 5년이다.", False),
    ("한정조건-드롭(Q5)",
     "환자의 형제·자매(배우자·직계존비속·배우자의 직계존속이 모두 없는 경우에 한정한다)도 기록 열람을 요청할 수 있다.",
     "형제·자매는 조건 없이 기록 열람을 요청할 수 있다.", False),
    ("한정조건-보존(Q5)",
     "환자의 형제·자매(배우자·직계존비속·배우자의 직계존속이 모두 없는 경우에 한정한다)도 기록 열람을 요청할 수 있다.",
     "배우자 등이 모두 없는 경우에 한정하여 형제·자매가 기록 열람을 요청할 수 있다.", True),
    ("호-오배치",
     "결격사유 각 호: 1. 정신질환자 2. 마약·대마 중독자 3. 피성년후견인",
     "결격사유 제1호는 마약 중독자이다.", False),
    ("패러프레이즈",
     "제27조 의료인이 아니면 누구든지 의료행위를 할 수 없다.",
     "무면허자의 의료행위는 금지된다.", True),
    ("반대-요약(id253)",
     "제21조③ 제2항에도 불구하고 다음 각 호에 해당하면 열람하게 할 수 있다. 1. 친족이 환자 본인의 동의서와 친족관계 증명서류를 첨부하여 요청한 경우",
     "친족은 환자의 동의 없이도 기록을 열람할 수 있다.", False),
    ("항-구분(id240)",
     "제90조(벌칙) 제17조제3항, 제27조제2항, 제33조제1항을 위반한 자는 500만원 이하의 벌금에 처한다.",
     "제27조제1항을 위반한 무면허 의료행위는 500만원 이하의 벌금에 처한다.", False),
    ("간접-함의(id236·bespoke 위음성 난문)",
     "제87조(벌칙) 제33조제2항을 위반하여 의료기관을 개설하거나 운영하는 자는 10년 이하의 징역이나 1억원 이하의 벌금에 처한다. "
     "제33조제2항: 의사·치과의사·한의사·조산사, 의료법인 등이 아니면 의료기관을 개설할 수 없다.",
     "의료기관 개설자가 될 수 없는 자가 의료기관을 개설하면 10년 이하의 징역이나 1억원 이하의 벌금에 처한다.", True),
    ("부재-주장",
     "제22조 진료기록부는 10년, 처방전은 2년 보존한다.",
     "의료광고는 자율심의기구의 사전심의를 받아야 한다.", False),
    ("결합-조건(id257)",
     "제21조의2① 다만, 해당 환자의 의식이 없거나 응급환자인 경우 또는 환자의 보호자가 없어 동의를 받을 수 없는 경우에는 동의 없이 송부할 수 있다.",
     "환자가 의식이 없는 경우에는 동의 없이 진료기록을 송부할 수 있다.", True),
    ("형량-오배치",
     "제88조(벌칙) 다음 각 호의 자는 3년 이하의 징역이나 3천만원 이하의 벌금에 처한다.",
     "해당 위반자는 5년 이하의 징역에 처한다.", False),
]


def check_minicheck(evidence, claim, model="bespoke-minicheck"):
    import requests
    r = requests.post(f"{OLLAMA}/api/generate", json={
        "model": model, "prompt": f"Document: {evidence}\nClaim: {claim}",
        "stream": False, "options": {"temperature": 0, "num_predict": 4},
    }, timeout=120)
    r.raise_for_status()
    ans = (r.json().get("response") or "").strip().lower()
    if ans.startswith("yes"):
        return True
    if ans.startswith("no"):
        return False
    raise ValueError(f"비정형: {ans[:20]}")


def check_qwen_entail(evidence, claim, model="qwen2.5:7b"):
    import requests
    r = requests.post(f"{OLLAMA}/api/generate", json={
        "model": model,
        "system": ("당신은 주장이 근거 문서만으로 지지되는지 판정하는 엄격한 검증관입니다. "
                   "근거에 명시되었거나 근거의 조항들을 결합해 직접 도출되는 경우에만 supported=true. "
                   "근거에 없거나, 조·항 번호가 다르거나, 근거의 요건·한정조건을 빠뜨린 주장은 supported=false."),
        "prompt": f"[근거 문서]\n{evidence}\n\n[주장]\n{claim}\n\n이 주장이 근거 문서만으로 지지됩니까?",
        "stream": False,
        "format": {"type": "object",
                   "properties": {"supported": {"type": "boolean"}},
                   "required": ["supported"]},
        "options": {"temperature": 0, "num_predict": 32},
    }, timeout=120)
    r.raise_for_status()
    return bool(json.loads(r.json().get("response") or "{}").get("supported"))


def run(backend_name, fn, model):
    correct = 0
    fails = []
    for label, ev, claim, expect in PAIRS:
        try:
            got = fn(ev, claim, model=model)
        except Exception as e:  # noqa: BLE001
            got = None
            fails.append((label, "ERR:" + str(e)[:40]))
            continue
        if got == expect:
            correct += 1
        else:
            fails.append((label, f"기대 {expect} → {got}"))
    print(f"[{backend_name} / {model}] {correct}/{len(PAIRS)}")
    for label, why in fails:
        print(f"   ✗ {label}: {why}")
    return correct


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="qwen2.5:7b,qwen2.5:14b")
    ap.add_argument("--with-minicheck", action="store_true",
                    help="bespoke-minicheck 참고 기준선도 실행(스왑 발생)")
    a = ap.parse_args()
    for m in a.models.split(","):
        run("qwen-entail", check_qwen_entail, m.strip())
    if a.with_minicheck:
        run("minicheck", check_minicheck, "bespoke-minicheck")
