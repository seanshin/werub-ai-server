#!/usr/bin/env python3
"""범용 모델 평가 스크립트 — 한국어 / 의료 / 코딩 / 추론 정량 평가

- 외부 의존 0건 (requests + Ollama 직접 호출만 사용, 프록시/인증 우회)
- 카테고리: korean(한국어 품질) · medical(의료 정확도) · coding(코드 생성) · reasoning(추론)
- 케이스 선언형 정의 → 동일 채점 엔진으로 0~100점 산출
- 출력: 콘솔 요약 테이블 + JSON 리포트(docs/eval_model.json)

사용법:
  python3 scripts/eval_model.py                          # 기본 모델셋 전체 평가
  python3 scripts/eval_model.py qwen2.5:14b qwen3:14b    # 모델 지정
  python3 scripts/eval_model.py --cat korean,coding qwen3:14b
  OLLAMA_HOST=http://localhost:11434 python3 scripts/eval_model.py

채점 공통 가중치 (각 케이스 100점 만점):
  keywords  — expected_keywords 포함률 (any/all 모드)
  answer    — expected_answer 부분일치 (정답 포함 여부)
  korean    — 한글 비율 (한국어 자연성)
  code      — 코드 블록(``` ```) + 필수 토큰 존재
  length    — 응답 길이 적절성
케이스마다 사용하는 항목의 배점 합이 100이 되도록 weights로 지정한다.
"""
import argparse
import json
import os
import re
import sys
import time

import requests

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# ---------------------------------------------------------------------------
# 테스트 케이스 (카테고리별)
#   check 항목: keywords(+match) / answer / code(+tokens) / korean / length
#   weights   : 사용할 항목별 배점 (합 100)
# ---------------------------------------------------------------------------

CASES = [
    # --- 한국어: 자연성·번역·요약 ------------------------------------------
    {
        "id": "ko-translate", "category": "korean",
        "system": "당신은 전문 번역가입니다. 반드시 자연스러운 한국어로만 답하세요.",
        "prompt": "다음 문장을 한국어로 번역하세요: The quick brown fox jumps over the lazy dog.",
        "keywords": ["여우", "개"], "keyword_match": "any",
        "weights": {"keywords": 50, "korean": 40, "length": 10},
    },
    {
        "id": "ko-summary", "category": "korean",
        "system": "당신은 한국어 요약 전문가입니다. 핵심만 2문장으로 한국어로 요약하세요.",
        "prompt": "인공지능은 의료, 금융, 제조 등 다양한 산업에서 활용되고 있다. "
                  "특히 의료 분야에서는 영상 판독과 신약 개발에 큰 기여를 하고 있다.",
        "keywords": ["인공지능", "의료"], "keyword_match": "any",
        "weights": {"keywords": 40, "korean": 50, "length": 10},
    },
    {
        "id": "ko-explain", "category": "korean",
        "system": "당신은 친절한 한국어 강사입니다. 반드시 한국어로 설명하세요.",
        "prompt": "광합성이 무엇인지 초등학생도 이해할 수 있게 한국어로 설명하세요.",
        "keywords": ["햇빛", "빛", "산소", "이산화탄소", "에너지", "식물"], "keyword_match": "any",
        "weights": {"keywords": 40, "korean": 50, "length": 10},
    },

    {
        "id": "ko-honorific", "category": "korean",
        "system": "당신은 한국어 어문 전문가입니다. 반드시 한국어로 답하세요.",
        "prompt": "다음 문장을 정중한 존댓말로 바꾸세요: 야, 밥 먹었어?",
        "keywords": ["드셨", "잡수", "식사", "셨어요", "셨습니까", "하셨"], "keyword_match": "any",
        "weights": {"keywords": 50, "korean": 50},
    },
    {
        "id": "ko-email", "category": "korean",
        "system": "당신은 비즈니스 한국어 작가입니다. 반드시 한국어로 답하세요.",
        "prompt": "회의 일정 변경을 알리는 정중한 비즈니스 이메일을 한국어로 작성하세요.",
        "keywords": ["안녕", "회의", "일정", "감사", "드립니다", "변경"], "keyword_match": "any",
        "weights": {"keywords": 40, "korean": 50, "length": 10},
    },
    {
        "id": "ko-proverb", "category": "korean",
        "system": "당신은 한국어 강사입니다. 반드시 한국어로 답하세요.",
        "prompt": "'가는 말이 고와야 오는 말이 곱다' 속담의 의미를 설명하세요.",
        "keywords": ["말", "상대", "태도", "대접", "예의", "곱게"], "keyword_match": "any",
        "weights": {"keywords": 40, "korean": 50, "length": 10},
    },
    {
        "id": "ko-define", "category": "korean",
        "system": "당신은 사전 편찬자입니다. 반드시 한국어로 답하세요.",
        "prompt": "'민주주의'를 한 문장으로 정의하세요.",
        "keywords": ["국민", "주권", "권력", "정치", "다수"], "keyword_match": "any",
        "weights": {"keywords": 50, "korean": 50},
    },
    {
        "id": "ko-rewrite", "category": "korean",
        "system": "당신은 한국어 교정 전문가입니다. 반드시 한국어로 답하세요.",
        "prompt": "다음을 더 자연스러운 한국어로 고치세요: 나는 어제 학교에 가서 공부를 했다 그리고 집에 왔다.",
        "keywords": ["어제", "학교", "공부", "집"], "keyword_match": "any",
        "weights": {"keywords": 40, "korean": 50, "length": 10},
    },
    {
        "id": "ko-recommend", "category": "korean",
        "system": "당신은 음악 큐레이터입니다. 반드시 한국어로 답하세요.",
        "prompt": "비 오는 날 듣기 좋은 음악 장르를 한국어로 추천하고 이유를 설명하세요.",
        "keywords": ["비", "음악", "추천", "장르", "분위기"], "keyword_match": "any",
        "weights": {"keywords": 40, "korean": 50, "length": 10},
    },
    {
        "id": "ko-classify", "category": "korean",
        "system": "당신은 감성 분류기입니다. 반드시 한국어로 '긍정' 또는 '부정' 한 단어로 답하세요.",
        "prompt": "'드디어 합격했어, 정말 기뻐!' 이 문장의 감정은 긍정인가 부정인가?",
        "answer": "긍정", "weights": {"answer": 100},
    },

    # --- 의료: 약물·감별진단 ------------------------------------------------
    {
        "id": "med-drug", "category": "medical",
        "system": "당신은 임상 약학 전문가입니다. 반드시 한국어로 답하세요.",
        "prompt": "Metformin의 주요 부작용과 금기사항을 설명하세요.",
        "keywords": ["위장관", "설사", "구역", "젖산산증", "신부전", "eGFR"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },
    {
        "id": "med-diagnosis", "category": "medical",
        "system": "당신은 감별진단 보조 AI입니다. 반드시 한국어로 답하세요.",
        "prompt": "50세 남성, 새벽 갑작스러운 우측 엄지발가락 통증·발적·부종. 요산 9.2mg/dL. "
                  "가장 가능성 높은 진단은?",
        "keywords": ["통풍", "gout"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },
    {
        "id": "med-term", "category": "medical",
        "system": "당신은 의료 정보를 쉽게 설명하는 AI입니다. 반드시 한국어로 답하세요.",
        "prompt": "HbA1c(당화혈색소)가 무엇인지 환자에게 쉽게 설명하세요.",
        "keywords": ["당화혈색소", "평균", "혈당", "2-3개월", "적혈구"], "keyword_match": "any",
        "weights": {"keywords": 60, "korean": 40},
    },

    {
        "id": "med-thyroid", "category": "medical",
        "system": "당신은 감별진단 보조 AI입니다. 반드시 한국어로 답하세요.",
        "prompt": "28세 여성, 체중감소·손떨림·안구돌출·빈맥 110bpm, TSH<0.01, FT4 상승. 가장 가능성 높은 진단은?",
        "keywords": ["갑상선", "항진", "그레이브스", "Graves", "thyrotox"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },
    {
        "id": "med-stroke", "category": "medical",
        "system": "당신은 감별진단 보조 AI입니다. 반드시 한국어로 답하세요.",
        "prompt": "55세 남성, 갑작스러운 좌측 편마비·구음장애, 발병 2시간 전, BP 180/100. 가장 가능성 높은 진단은?",
        "keywords": ["뇌경색", "뇌졸중", "stroke", "경색", "허혈"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },
    {
        "id": "med-warfarin", "category": "medical",
        "system": "당신은 임상 약학 전문가입니다. 반드시 한국어로 답하세요.",
        "prompt": "Warfarin 복용 중인 환자가 피해야 할 음식과 약물은?",
        "keywords": ["비타민K", "녹색채소", "시금치", "INR", "NSAID", "아스피린"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },
    {
        "id": "med-egfr", "category": "medical",
        "system": "당신은 의료 정보를 쉽게 설명하는 AI입니다. 반드시 한국어로 답하세요.",
        "prompt": "eGFR이 무엇이고, 수치가 낮으면 어떤 의미인지 설명하세요.",
        "keywords": ["사구체", "여과", "신장", "기능", "만성"], "keyword_match": "any",
        "weights": {"keywords": 60, "korean": 40},
    },
    {
        "id": "med-mi", "category": "medical",
        "system": "당신은 응급 감별진단 AI입니다. 반드시 한국어로 답하세요.",
        "prompt": "60세 남성, 30분간 지속되는 흉통, 식은땀, 좌측 팔로 방사. 응급으로 의심할 진단은?",
        "keywords": ["심근경색", "심장", "경색", "협심증", "MI", "ACS"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },
    {
        "id": "med-ras", "category": "medical",
        "system": "당신은 임상 약학 전문가입니다. 반드시 한국어로 답하세요.",
        "prompt": "고혈압 환자에서 ACE inhibitor와 ARB를 동시에 쓰면 안 되는 이유는?",
        "keywords": ["고칼륨", "신기능", "이중", "RAS", "저하"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },
    {
        "id": "med-crohn", "category": "medical",
        "system": "당신은 감별진단 보조 AI입니다. 반드시 한국어로 답하세요.",
        "prompt": "35세 여성, 반복 복통·혈변·체중감소, 대장내시경에서 회맹부 종주 궤양과 조약돌 점막. 진단은?",
        "keywords": ["크론", "Crohn", "염증성 장질환", "IBD"], "keyword_match": "any",
        "weights": {"keywords": 70, "korean": 30},
    },

    # --- 코딩: 코드 생성 ----------------------------------------------------
    {
        "id": "code-fib", "category": "coding",
        "system": "You are an expert Python developer. Output runnable code.",
        "prompt": "Write a Python function `fib(n)` that returns the n-th Fibonacci number.",
        "code_tokens": ["def", "fib", "return"],
        "weights": {"code": 70, "keywords": 30},
        "keywords": ["fib"], "keyword_match": "any",
    },
    {
        "id": "code-sql", "category": "coding",
        "system": "You are a database expert. Output a single SQL query.",
        "prompt": "Write a SQL query to select the top 5 users by total order amount "
                  "from tables `users(id, name)` and `orders(user_id, amount)`.",
        "keywords": ["select", "join", "sum", "group by", "order by", "limit"],
        "keyword_match": "all_soft", "weights": {"keywords": 100},
    },
    {
        "id": "code-bug", "category": "coding",
        "system": "You are a senior code reviewer. Output corrected Python code.",
        "prompt": "Fix the bug in this Python code:\n"
                  "def avg(nums):\n    return sum(nums) / len(num)",
        "code_tokens": ["def", "avg", "nums", "return"],
        "weights": {"code": 60, "keywords": 40},
        "keywords": ["len(nums)", "len(num"], "keyword_match": "any",
    },

    {
        "id": "code-reverse", "category": "coding",
        "system": "You are an expert Python developer. Output runnable code.",
        "prompt": "Write a Python function reverse_string(s) that returns the reversed string.",
        "code_tokens": ["def", "reverse_string", "return"],
        "weights": {"code": 70, "keywords": 30},
        "keywords": ["reverse_string"], "keyword_match": "any",
    },
    {
        "id": "code-palindrome", "category": "coding",
        "system": "You are an expert Python developer. Output runnable code.",
        "prompt": "Write a Python function is_palindrome(s) that returns True if s is a palindrome.",
        "code_tokens": ["def", "is_palindrome", "return"],
        "weights": {"code": 70, "keywords": 30},
        "keywords": ["is_palindrome"], "keyword_match": "any",
    },
    {
        "id": "code-class", "category": "coding",
        "system": "You are an expert Python developer. Output runnable code.",
        "prompt": "Write a Python class Stack with push() and pop() methods.",
        "code_tokens": ["class", "Stack", "def", "push", "pop"],
        "weights": {"code": 100},
    },
    {
        "id": "code-json", "category": "coding",
        "system": "You are a Python expert. Output a short code snippet.",
        "prompt": "In Python, parse a JSON string and print the value of its 'name' field.",
        "keywords": ["json.loads", "import json", "name"], "keyword_match": "all_soft",
        "weights": {"keywords": 100},
    },
    {
        "id": "code-comprehension", "category": "coding",
        "system": "You are a Python expert. Output a short code snippet.",
        "prompt": "Write Python that sums even numbers in a list using a list comprehension.",
        "keywords": ["sum", "for", "% 2", "if"], "keyword_match": "all_soft",
        "weights": {"keywords": 100},
    },
    {
        "id": "code-async", "category": "coding",
        "system": "You are an expert Python developer. Output runnable code.",
        "prompt": "Write an async Python function fetch() that uses async/await.",
        "code_tokens": ["async", "def", "await", "fetch"],
        "weights": {"code": 100},
    },
    {
        "id": "code-recursion", "category": "coding",
        "system": "You are an expert Python developer. Output runnable code.",
        "prompt": "Write a recursive Python function factorial(n) returning n!.",
        "code_tokens": ["def", "factorial", "return"],
        "weights": {"code": 70, "keywords": 30},
        "keywords": ["factorial"], "keyword_match": "any",
    },

    # --- 추론: 수학·논리 ----------------------------------------------------
    {
        "id": "reason-math", "category": "reasoning",
        "system": "You are a precise math solver. Show the final numeric answer.",
        "prompt": "A train travels 60 km in 45 minutes. What is its speed in km/h? "
                  "Give the final number.",
        "answer": "80", "weights": {"answer": 100},
    },
    {
        "id": "reason-logic", "category": "reasoning",
        "system": "You are a logic puzzle solver. State the final answer clearly.",
        "prompt": "All roses are flowers. Some flowers fade quickly. "
                  "Can we conclude that some roses fade quickly? Answer yes or no.",
        "answer": "no", "weights": {"answer": 100},
    },
    {
        "id": "reason-count", "category": "reasoning",
        "system": "You count carefully. Give only the final number.",
        "prompt": "How many times does the letter 'r' appear in the word 'strawberry'? "
                  "Give the final number.",
        "answer": "3", "weights": {"answer": 100},
    },
    {
        "id": "reason-proportion", "category": "reasoning",
        "system": "You are a precise math solver. Give only the final number.",
        "prompt": "If 3 apples cost 6 dollars, how much do 7 apples cost in dollars? Final number only.",
        "answer": "14", "weights": {"answer": 100},
    },
    {
        "id": "reason-percent", "category": "reasoning",
        "system": "You are a precise math solver. Give only the final number.",
        "prompt": "What is 15 percent of 200? Final number only.",
        "answer": "30", "weights": {"answer": 100},
    },
    {
        "id": "reason-sequence", "category": "reasoning",
        "system": "You are a pattern solver. Give only the final number.",
        "prompt": "What is the next number in the sequence 2, 4, 8, 16, ...? Final number only.",
        "answer": "32", "weights": {"answer": 100},
    },
    {
        "id": "reason-age", "category": "reasoning",
        "system": "You are a careful solver. Give only the final number.",
        "prompt": "Tom is 5 years older than Jerry. Jerry is 10 years old. How old is Tom? Number only.",
        "answer": "15", "weights": {"answer": 100},
    },
    {
        "id": "reason-syllogism", "category": "reasoning",
        "system": "You are a logic solver. Answer yes or no.",
        "prompt": "All cats are animals. Felix is a cat. Is Felix an animal? Answer yes or no.",
        "answer": "yes", "weights": {"answer": 100},
    },
    {
        "id": "reason-vowels", "category": "reasoning",
        "system": "You count carefully. Give only the final number.",
        "prompt": "How many vowels (a, e, i, o, u) are in the word 'education'? Final number only.",
        "answer": "5", "weights": {"answer": 100},
    },
    {
        "id": "reason-time", "category": "reasoning",
        "system": "You are a careful solver. Give the clock time only.",
        "prompt": "A meeting starts at 2:00 PM and lasts 90 minutes. What time does it end? Answer like 3:30 PM.",
        "answer": "3:30", "weights": {"answer": 100},
    },
]


# ---------------------------------------------------------------------------
# Ollama 호출
# ---------------------------------------------------------------------------

def call_ollama(model, prompt, system="", timeout=180):
    """Ollama 직접 호출 (프록시 우회, 인증 불필요). 반환: (text, tokens, ms)"""
    try:
        r = requests.post(f"{OLLAMA_HOST}/api/generate", json={
            "model": model, "prompt": prompt, "system": system,
            "stream": False, "options": {"temperature": 0.2},
        }, timeout=timeout)
        d = r.json()
        dur_ns = d.get("eval_duration", 0) or 0
        return d.get("response", ""), d.get("eval_count", 0), dur_ns // 1_000_000
    except Exception as e:
        return f"__ERROR__: {e}", 0, 0


# ---------------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------------

def korean_ratio(text):
    ko = sum(1 for c in text if "가" <= c <= "힣")
    alpha = sum(1 for c in text if c.isalpha())
    return ko / max(alpha, 1)


def score_keywords(text, case):
    expected = case.get("keywords", [])
    if not expected:
        return 0.0
    low = text.lower()
    hits = [kw for kw in expected if kw.lower() in low]
    mode = case.get("keyword_match", "any")
    if mode == "any":
        return 1.0 if hits else 0.0
    if mode == "all":
        return 1.0 if len(hits) == len(expected) else 0.0
    # all_soft: 포함률 비례
    return len(hits) / len(expected)


def score_code(text, case):
    tokens = case.get("code_tokens", [])
    has_block = "```" in text or any(t in text for t in tokens)
    token_hit = sum(1 for t in tokens if t in text) / max(len(tokens), 1) if tokens else 0
    # 코드 블록 0.4 + 필수 토큰 0.6
    return (0.4 if has_block else 0.0) + 0.6 * token_hit


def evaluate_case(case, model):
    """단일 케이스 평가 → {score, details, tokens, duration_ms}"""
    text, tokens, ms = call_ollama(model, case["prompt"], case.get("system", ""))
    if text.startswith("__ERROR__"):
        return {"score": 0, "details": {"error": text}, "tokens": 0, "duration_ms": 0}

    weights = case["weights"]
    score, details = 0.0, {}

    if "keywords" in weights:
        frac = score_keywords(text, case)
        score += weights["keywords"] * frac
        details["keyword_frac"] = round(frac, 2)
    if "answer" in weights:
        ok = str(case["answer"]).lower() in text.lower()
        score += weights["answer"] * (1.0 if ok else 0.0)
        details["answer_ok"] = ok
    if "korean" in weights:
        kr = korean_ratio(text)
        score += weights["korean"] * min(1.0, kr / 0.8)  # 0.8 이상이면 만점
        details["korean_ratio"] = round(kr, 2)
    if "code" in weights:
        frac = score_code(text, case)
        score += weights["code"] * frac
        details["code_frac"] = round(frac, 2)
    if "length" in weights:
        ok = 20 < len(text) < 4000
        score += weights["length"] * (1.0 if ok else 0.0)
        details["length"] = len(text)

    return {"score": round(min(100, score)), "details": details,
            "tokens": tokens, "duration_ms": ms}


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------

def run(models, categories):
    cases = [c for c in CASES if c["category"] in categories]
    results = {}

    for model in models:
        print(f"\n{'='*64}\nModel: {model}\n{'='*64}")
        per_cat = {cat: [] for cat in categories}
        tot_tok = tot_ms = 0

        for case in cases:
            r = evaluate_case(case, model)
            per_cat[case["category"]].append(r["score"])
            tot_tok += r["tokens"]
            tot_ms += r["duration_ms"]
            flag = "" if r["score"] >= 60 else "  ⚠"
            print(f"  [{case['category']:<9}] {case['id']:<16} "
                  f"{r['score']:>3}/100  {r['tokens']:>4}tok {r['duration_ms']:>6}ms{flag}")

        cat_avg = {cat: round(sum(s) / max(len(s), 1), 1) for cat, s in per_cat.items()}
        overall = round(sum(cat_avg.values()) / max(len(cat_avg), 1), 1)
        results[model] = {
            "category_avg": cat_avg, "overall": overall,
            "total_tokens": tot_tok, "total_duration_ms": tot_ms,
            "tok_per_sec": round(tot_tok / (tot_ms / 1000), 1) if tot_ms else 0,
        }
    return results


def print_summary(results, categories):
    print("\n" + "=" * 78)
    print("평가 결과 요약")
    print("=" * 78)
    head = f"{'모델':<22}"
    for cat in categories:
        head += f"{cat[:6]:>8}"
    head += f"{'평균':>8}{'tok/s':>8}{'시간(s)':>9}"
    print(head)
    print("-" * 78)
    for model, r in sorted(results.items(), key=lambda x: -x[1]["overall"]):
        row = f"{model:<22}"
        for cat in categories:
            row += f"{r['category_avg'].get(cat, 0):>8.1f}"
        row += f"{r['overall']:>8.1f}{r['tok_per_sec']:>8.1f}{r['total_duration_ms']/1000:>9.1f}"
        print(row)
    print("=" * 78)
    best = max(results.items(), key=lambda x: x[1]["overall"]) if results else None
    if best:
        print(f"  🏆 종합 1위: {best[0]} ({best[1]['overall']}점)")


def main():
    ap = argparse.ArgumentParser(description="범용 모델 평가 (한국어/의료/코딩/추론)")
    ap.add_argument("models", nargs="*",
                    default=["qwen2.5:14b", "qwen3:14b"],
                    help="평가할 모델명 (기본: qwen2.5:14b qwen3:14b)")
    ap.add_argument("--cat", default="korean,medical,coding,reasoning",
                    help="평가 카테고리 (쉼표 구분)")
    ap.add_argument("--out", default=None, help="JSON 리포트 경로")
    args = ap.parse_args()

    categories = [c.strip() for c in args.cat.split(",") if c.strip()]
    all_cats = sorted({c["category"] for c in CASES})
    bad = [c for c in categories if c not in all_cats]
    if bad:
        print(f"알 수 없는 카테고리: {bad} (가능: {all_cats})")
        sys.exit(1)

    # Ollama 접근 확인
    try:
        requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5).raise_for_status()
    except Exception as e:
        print(f"❌ Ollama 미접근 ({OLLAMA_HOST}): {e}")
        print("   GPU 서버에서 실행하거나 OLLAMA_HOST 환경변수를 지정하세요.")
        sys.exit(2)

    n_cases = len([c for c in CASES if c["category"] in categories])
    print(f"평가 시작: {args.models}")
    print(f"카테고리: {categories} | 케이스 {n_cases}건 | Ollama {OLLAMA_HOST}")
    t0 = time.time()

    results = run(args.models, categories)
    print_summary(results, categories)
    print(f"\n총 소요: {time.time() - t0:.1f}s")

    out_path = args.out or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "eval_model.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"categories": categories, "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"결과 저장: {out_path}")


if __name__ == "__main__":
    main()
