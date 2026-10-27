#!/usr/bin/env python3
"""파인튜닝 모델 정량 평가 — werub-medical:7b vs qwen2.5:7b

카테고리:
  1. SOAP 요약 — 구조화 품질 (필수 필드, ICD 코드, 한국어)
  2. 감별진단 — 정확도 (골드 진단 포함 여부, 순위)
  3. 약물 정보 — 정확도 (용량, 금기, 상호작용)
  4. 의료 용어 — 한국어 설명 품질

출력: JSON 리포트 + 요약 테이블
"""
import json
import os
import sys
import time
import requests

BASE_URL = "http://localhost:8585"

# ---------------------------------------------------------------------------
# 테스트 케이스
# ---------------------------------------------------------------------------

SOAP_CASES = [
    {
        "id": "soap-1",
        "input": "45세 남성, 3일 전부터 흉부 압박감, 운동 시 악화, 안정 시 호전. 흡연 20갑년. BP 150/95, HR 88, SpO2 97%. ECG ST depression V4-V6. Troponin-I 0.08ng/mL.",
        "expected_icd": ["I20"],
        "expected_urgency": "high",
        "specialty": "내과",
    },
    {
        "id": "soap-2",
        "input": "32세 여성, 2주 전부터 양측 손가락 관절 통증, 아침 강직 1시간 이상. ESR 45, CRP 2.8, RF 양성, Anti-CCP 양성.",
        "expected_icd": ["M05", "M06"],
        "expected_urgency": "medium",
        "specialty": "내과",
    },
    {
        "id": "soap-3",
        "input": "68세 남성, 1주 전부터 호흡곤란 악화, 야간 기좌호흡. 하지 부종(+). 심부전 과거력. BNP 1200pg/mL. 흉부 X-ray 양측 흉수.",
        "expected_icd": ["I50"],
        "expected_urgency": "high",
        "specialty": "내과",
    },
    {
        "id": "soap-4",
        "input": "5세 남아, 3일 전부터 발열 39도, 기침, 콧물. 인후 발적(+), 양측 편도 비대. WBC 15000, CRP 5.2.",
        "expected_icd": ["J02", "J03", "J06"],
        "expected_urgency": "medium",
        "specialty": "소아과",
    },
]

DIAGNOSIS_CASES = [
    {
        "id": "dx-1",
        "input": "55세 남성, 갑작스러운 좌측 편마비, 구음장애, 발병 2시간 전. BP 180/100, NIHSS 12점.",
        "gold_diagnosis": ["뇌경색", "뇌졸중", "cerebral infarction", "stroke"],
        "gold_icd": ["I63", "I64"],
    },
    {
        "id": "dx-2",
        "input": "28세 여성, 2개월간 체중 감소 5kg, 손떨림, 안구돌출, 빈맥 110bpm. TSH<0.01, FT4 4.5.",
        "gold_diagnosis": ["갑상선기능항진증", "그레이브스", "Graves", "thyrotoxicosis"],
        "gold_icd": ["E05"],
    },
    {
        "id": "dx-3",
        "input": "50세 남성, 새벽 갑작스러운 우측 엄지발가락 통증, 발적, 부종. 요산 9.2mg/dL.",
        "gold_diagnosis": ["통풍", "gout"],
        "gold_icd": ["M10"],
    },
    {
        "id": "dx-4",
        "input": "35세 여성, 반복 복통, 혈변, 체중감소 3kg/1개월. 대장내시경: 회맹부 종주 궤양, 조약돌 점막.",
        "gold_diagnosis": ["크론병", "Crohn"],
        "gold_icd": ["K50"],
    },
]

DRUG_CASES = [
    {
        "id": "drug-1",
        "question": "Metformin 500mg의 주요 부작용과 금기사항을 설명하세요.",
        "expected_keywords": ["위장관", "설사", "구역", "젖산산증", "신부전", "eGFR"],
    },
    {
        "id": "drug-2",
        "question": "Warfarin 복용 중인 환자가 피해야 할 음식과 약물은?",
        "expected_keywords": ["비타민K", "녹색채소", "시금치", "브로콜리", "INR", "NSAID", "아스피린"],
    },
    {
        "id": "drug-3",
        "question": "고혈압 환자에서 ACE inhibitor와 ARB를 동시에 쓰면 안 되는 이유는?",
        "expected_keywords": ["고칼륨", "신기능", "저하", "이중 차단", "RAS"],
    },
]

TERMINOLOGY_CASES = [
    {
        "id": "term-1",
        "question": "HbA1c가 무엇인지 환자에게 쉽게 설명해주세요.",
        "expected_keywords": ["당화혈색소", "평균", "혈당", "2-3개월", "적혈구"],
    },
    {
        "id": "term-2",
        "question": "eGFR이 무엇이고, 수치가 낮으면 어떤 의미인지 설명해주세요.",
        "expected_keywords": ["사구체", "여과율", "신장", "기능", "만성", "신장병"],
    },
    {
        "id": "term-3",
        "question": "MRI와 CT의 차이점을 환자에게 설명해주세요.",
        "expected_keywords": ["자기", "공명", "방사선", "X선", "연부조직", "뼈"],
    },
]


# ---------------------------------------------------------------------------
# 평가 함수
# ---------------------------------------------------------------------------

def call_api(endpoint, payload, timeout=120):
    """API 호출"""
    try:
        r = requests.post(f"{BASE_URL}{endpoint}",
                          json=payload, timeout=timeout)
        return r.json(), r.status_code
    except Exception as e:
        return {"error": str(e)}, 500


OLLAMA_DIRECT = "http://localhost:11434"  # Ollama 직접 (프록시 우회)


def call_ollama(model, prompt, system="", timeout=120):
    """Ollama 직접 호출 (프록시 우회, 인증 불필요)"""
    try:
        r = requests.post(f"{OLLAMA_DIRECT}/api/generate", json={
            "model": model, "prompt": prompt, "system": system,
            "stream": False, "options": {"temperature": 0.3},
        }, timeout=timeout)
        d = r.json()
        tokens = d.get("eval_count", 0)
        dur_ns = d.get("eval_duration", 0)
        duration_ms = dur_ns // 1000000 if dur_ns else 0
        return d.get("response", ""), tokens, duration_ms
    except Exception as e:
        return f"Error: {e}", 0, 0


SOAP_SYSTEM = """당신은 {specialty} 전문의를 보조하는 의료 AI입니다. 반드시 한국어로 답변하세요.
진료 기록을 SOAP 형식으로 구조화하세요.
반드시 JSON 형식으로만 출력하세요.
{{"summary": {{"subjective":"...","objective":"...","assessment":"...","plan":"..."}}, "icd_codes":["..."], "key_findings":["..."], "urgency":"low/medium/high/critical"}}"""


def evaluate_soap(case, model):
    """SOAP 평가 — 구조화 품질 점수"""
    system = SOAP_SYSTEM.format(specialty=case.get("specialty", "내과"))
    response_text, tokens, duration_ms = call_ollama(model, case["input"], system)

    score = 0
    details = {}

    # JSON 파싱
    try:
        import re
        match = re.search(r'\{[\s\S]*\}', response_text)
        resp = json.loads(match.group(0)) if match else {}
    except Exception:
        resp = {}

    # 1. SOAP 필드 존재 (40점)
    summary = resp.get("summary", {})
    if isinstance(summary, dict):
        for field in ["subjective", "objective", "assessment", "plan"]:
            if summary.get(field) and len(str(summary[field])) > 10:
                score += 10
                details[f"has_{field}"] = True
            else:
                details[f"has_{field}"] = False

    # 2. ICD 코드 포함 (20점)
    icd_codes = resp.get("icd_codes", [])
    full_text = response_text
    for expected in case.get("expected_icd", []):
        if any(expected in str(c) for c in icd_codes) or expected in full_text:
            score += 20
            details["icd_match"] = True
            break
    else:
        details["icd_match"] = False

    # 3. 긴급도 (10점)
    urgency = resp.get("urgency", "")
    if urgency == case.get("expected_urgency"):
        score += 10
        details["urgency_match"] = True
    else:
        details["urgency_match"] = False
        details["urgency_got"] = urgency

    # 4. 한국어 품질 (20점)
    korean_chars = sum(1 for c in full_text if '\uac00' <= c <= '\ud7a3')
    total_alpha = sum(1 for c in full_text if c.isalpha())
    ko_ratio = korean_chars / max(total_alpha, 1)
    ko_score = min(20, int(ko_ratio * 25))
    score += ko_score
    details["korean_ratio"] = round(ko_ratio, 2)

    # 5. 응답 길이 적절성 (10점)
    if 200 < len(full_text) < 3000:
        score += 10
        details["length_ok"] = True
    else:
        details["length_ok"] = False

    return {"score": score, "details": details, "tokens": tokens, "duration_ms": duration_ms}


ANALYZE_SYSTEM = """당신은 임상 전문의를 보조하는 감별진단 AI입니다. 반드시 한국어로 답변하세요.
주어진 임상 데이터를 분석하여 감별진단 상위 3개를 확률순으로 제시하세요.
반드시 아래 JSON 배열 형식으로만 출력하세요.
[{{"diagnosis":"영문 진단명","diagnosis_ko":"한국어 진단명","icd10":"ICD-10","probability":"high/medium/low","supporting":["근거"]}}]"""


def evaluate_diagnosis(case, model):
    """감별진단 평가 — 골드 진단 포함 여부"""
    response_text, tokens, duration_ms = call_ollama(model, case["input"], ANALYZE_SYSTEM)

    score = 0
    details = {}
    lower_text = response_text.lower()

    # 1. 골드 진단 포함 (40점)
    found = False
    for gold in case["gold_diagnosis"]:
        if gold.lower() in lower_text:
            found = True
            details["gold_found"] = gold
            break
    score += 40 if found else 0

    # 2. 골드 ICD 코드 포함 (20점)
    icd_found = False
    for icd in case.get("gold_icd", []):
        if icd.lower() in lower_text:
            icd_found = True
            break
    score += 20 if icd_found else 0
    details["icd_found"] = icd_found

    # 3. 3개 이상 감별진단 (20점)
    try:
        import re
        match = re.search(r'\[[\s\S]*\]', response_text)
        diagnoses = json.loads(match.group(0)) if match else []
    except Exception:
        diagnoses = []
    if isinstance(diagnoses, list) and len(diagnoses) >= 3:
        score += 20
    details["diagnosis_count"] = len(diagnoses) if isinstance(diagnoses, list) else 0

    # 4. 한국어 (20점)
    korean_chars = sum(1 for c in response_text if '\uac00' <= c <= '\ud7a3')
    total_alpha = sum(1 for c in response_text if c.isalpha())
    ko_ratio = korean_chars / max(total_alpha, 1)
    score += min(20, int(ko_ratio * 25))

    return {"score": score, "details": details, "tokens": tokens, "duration_ms": duration_ms}


def evaluate_drug(case, model):
    """약물 정보 평가 — 키워드 포함률"""
    response_text, tokens, duration_ms = call_ollama(
        model, case["question"],
        "당신은 임상 약학 전문가입니다. 반드시 한국어로 답변하세요.")

    expected = case["expected_keywords"]
    found = [kw for kw in expected if kw.lower() in response_text.lower()]
    keyword_score = int(len(found) / max(len(expected), 1) * 80)

    korean_chars = sum(1 for c in response_text if '\uac00' <= c <= '\ud7a3')
    total_alpha = sum(1 for c in response_text if c.isalpha())
    ko_ratio = korean_chars / max(total_alpha, 1)
    ko_score = min(20, int(ko_ratio * 25))

    return {
        "score": keyword_score + ko_score,
        "details": {"found_keywords": found, "total_keywords": len(expected), "match_rate": round(len(found)/len(expected), 2)},
        "tokens": tokens,
        "duration_ms": duration_ms,
    }


def evaluate_terminology(case, model):
    """의료 용어 설명 평가"""
    return evaluate_drug(case, model)  # 동일 평가 로직


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def run_benchmark(models):
    """전체 벤치마크 실행"""
    results = {}

    for model in models:
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        print(f"{'='*60}")

        model_results = {
            "soap": [], "diagnosis": [], "drug": [], "terminology": [],
            "total_tokens": 0, "total_duration_ms": 0,
        }

        # SOAP
        print("\n[SOAP 요약]")
        for case in SOAP_CASES:
            r = evaluate_soap(case, model)
            model_results["soap"].append({"id": case["id"], **r})
            model_results["total_tokens"] += r.get("tokens", 0)
            model_results["total_duration_ms"] += r.get("duration_ms", 0)
            print(f"  {case['id']}: {r['score']}/100  tokens={r.get('tokens',0)}  {r.get('duration_ms',0)}ms")

        # 감별진단
        print("\n[감별진단]")
        for case in DIAGNOSIS_CASES:
            r = evaluate_diagnosis(case, model)
            model_results["diagnosis"].append({"id": case["id"], **r})
            model_results["total_tokens"] += r.get("tokens", 0)
            model_results["total_duration_ms"] += r.get("duration_ms", 0)
            print(f"  {case['id']}: {r['score']}/100  tokens={r.get('tokens',0)}  {r.get('duration_ms',0)}ms")

        # 약물 정보
        print("\n[약물 정보]")
        for case in DRUG_CASES:
            r = evaluate_drug(case, model)
            model_results["drug"].append({"id": case["id"], **r})
            model_results["total_tokens"] += r.get("tokens", 0)
            model_results["total_duration_ms"] += r.get("duration_ms", 0)
            print(f"  {case['id']}: {r['score']}/100  tokens={r.get('tokens',0)}  {r.get('duration_ms',0)}ms")

        # 의료 용어
        print("\n[의료 용어]")
        for case in TERMINOLOGY_CASES:
            r = evaluate_terminology(case, model)
            model_results["terminology"].append({"id": case["id"], **r})
            model_results["total_tokens"] += r.get("tokens", 0)
            model_results["total_duration_ms"] += r.get("duration_ms", 0)
            print(f"  {case['id']}: {r['score']}/100  tokens={r.get('tokens',0)}  {r.get('duration_ms',0)}ms")

        # 카테고리별 평균
        for cat in ["soap", "diagnosis", "drug", "terminology"]:
            scores = [r["score"] for r in model_results[cat]]
            model_results[f"{cat}_avg"] = round(sum(scores) / max(len(scores), 1), 1)

        model_results["overall_avg"] = round(
            (model_results["soap_avg"] + model_results["diagnosis_avg"] +
             model_results["drug_avg"] + model_results["terminology_avg"]) / 4, 1)

        results[model] = model_results

    return results


def print_summary(results):
    """결과 요약 테이블"""
    print("\n" + "=" * 70)
    print("평가 결과 요약")
    print("=" * 70)
    print(f"{'모델':<25} {'SOAP':>6} {'감별':>6} {'약물':>6} {'용어':>6} {'평균':>6} {'토큰':>8} {'시간(s)':>8}")
    print("-" * 70)
    for model, r in results.items():
        print(f"{model:<25} {r['soap_avg']:>6.1f} {r['diagnosis_avg']:>6.1f} "
              f"{r['drug_avg']:>6.1f} {r['terminology_avg']:>6.1f} {r['overall_avg']:>6.1f} "
              f"{r['total_tokens']:>8} {r['total_duration_ms']/1000:>8.1f}")
    print("=" * 70)

    # 승자 판단
    models = list(results.keys())
    if len(models) == 2:
        a, b = models
        for cat in ["soap", "diagnosis", "drug", "terminology", "overall"]:
            key = f"{cat}_avg"
            diff = results[a][key] - results[b][key]
            winner = a if diff > 0 else b
            print(f"  {cat:>12}: {winner} 우세 ({abs(diff):+.1f}점)")


if __name__ == "__main__":
    models = ["werub-medical:7b", "qwen2.5:7b"]
    print(f"벤치마크 시작: {models}")
    print(f"테스트: SOAP {len(SOAP_CASES)}건 + 감별 {len(DIAGNOSIS_CASES)}건 + "
          f"약물 {len(DRUG_CASES)}건 + 용어 {len(TERMINOLOGY_CASES)}건 = {len(SOAP_CASES)+len(DIAGNOSIS_CASES)+len(DRUG_CASES)+len(TERMINOLOGY_CASES)}건")

    results = run_benchmark(models)
    print_summary(results)

    # JSON 저장
    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "docs", "benchmark_medical_models.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장: {out_path}")
