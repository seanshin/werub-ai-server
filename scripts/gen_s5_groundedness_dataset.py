"""S5 정답셋 생성 — DUR 용량주의(상한)+병용금기(distractor) 정본에서 케이스 구성.

날조 0: 모든 drug/dose/evidence/source는 dur.db 실재값. source에 ingr_code/dur_no 기재.
트윈 스키마 확장(dose_note로 '상한' 의미 명시 — DUR은 허용용량 리스트가 아니라 상한 제공).
전 케이스 정본: 용량=DUR 용량주의(ingr_code), 병용금기=DUR 병용금기(a/b_code), 계열=DUR 분류(class).
"""
import json
import os
import re
import sqlite3

os.chdir(os.path.expanduser("~/WeRUBLLMManager"))
db = sqlite3.connect("dur.db")
db.row_factory = sqlite3.Row


def mg(criteria):
    """criteria에서 대표 mg 상한 추출 (단일 mg 값인 것만)."""
    m = re.findall(r'([\d.]+)\s*(?:밀리그램|mg)', criteria or "")
    return m[0] if len(m) == 1 else None


# 1) 용량주의: 성분명+단일 mg 상한이 깔끔한 것 (class로 질환군 파악)
dose_rows = []
for r in db.execute(
        "SELECT ingr_code, ingr_name, ingr_eng, class_name, criteria "
        "FROM dur_single WHERE dur_type='용량주의' AND criteria LIKE '%밀리그램%'"):
    d = dict(r)
    val = mg(d["criteria"])
    # 성분명이 criteria 앞에 붙은 경우(예 '캅토프릴 100밀리그램') 정제 케이스만
    if val and d["ingr_name"] and "(" not in d["criteria"]:
        dose_rows.append((d["ingr_name"], d["ingr_eng"], d["class_name"],
                          d["criteria"].strip(), val, d["ingr_code"]))

# 중복 성분 제거(성분당 1건), class 다양성 위해 정렬
seen = set()
uniq = []
for row in sorted(dose_rows, key=lambda x: (x[2] or "", -float(x[4]))):
    if row[0] in seen:
        continue
    seen.add(row[0])
    uniq.append(row)
print("용량주의 후보 성분 수:", len(uniq))

# 2) 병용금기: distractor용 약물쌍
inter = [dict(r) for r in db.execute(
    "SELECT a_name, a_code, b_name, b_code, reason, grade, dur_no FROM dur_interaction "
    "WHERE a_name!='' AND b_name!='' LIMIT 40")]

# ── 케이스 생성 ──
cases = []
cid = 0


def add(**kw):
    global cid
    cid += 1
    kw["id"] = "S5-%03d" % cid
    cases.append(kw)


# A. 용량 grounded — 상한 이내 처방 질의 (통과 기대), 12건
for name, eng, cls, crit, val, code in uniq[:12]:
    cls_disp = re.sub(r'^\[\d+\]', '', cls or "").strip()
    add(
        query="%s 성인 1일 최대 투여량(%s 계열) 기준 처방 용량을 알려주세요." % (name, cls_disp or "해당"),
        context="",
        grounded=[{
            "drug": name,
            "dose_max": "%smg" % val,
            "dose_note": "DUR 1일 최대투여량 상한. 출력 용량 <= dose_max 허용, 초과 시 용량환각.",
            "evidence": "DUR 용량주의: %s 1일 최대투여량 %s" % (name, crit),
            "source": "DUR-용량주의/ingr_code=%s" % code,
        }],
        distractor_drugs=[],
        allow_unspecified=True,
    )

# B. 병용금기 distractor — grounded 약물 + 병용금기 상대가 distractor, 8건
for it in inter[:8]:
    a, b = it["a_name"], it["b_name"]
    add(
        query="%s 복용 환자에게 추가 처방을 검토합니다. %s은(는) 함께 처방 가능한가요?" % (a, b),
        context="",
        grounded=[{
            "drug": a,
            "dose_note": "약물명만 확인 대상(용량 무관, dose_max 없음).",
            "evidence": "DUR 병용금기: %s + %s → %s" % (a, b, (it["reason"] or "병용금기")[:60]),
            "source": "DUR-병용금기/a_code=%s,b_code=%s" % (it["a_code"], it["b_code"]),
        }],
        distractor_drugs=[b],
        allow_unspecified=False,
    )

# C. 약물군 언급 허용 (allow_unspecified) — 용량 없는 계열 질의, 5건
# 계열은 DUR 분류(class_name) 정본 기반 — 계열명·source 모두 dur.db 실재 분류코드
groups = [
    ("제2형 당뇨병에 쓰이는 약물 계열은?", "당뇨병용제",
     "DUR 분류: [03960]당뇨병용제 계열(개별 성분 용량은 성분별 상이)", "DUR-분류/class=03960"),
    ("고혈압 약물 계열을 알려주세요.", "혈압강하제",
     "DUR 분류: [02140]혈압강하제 계열", "DUR-분류/class=02140"),
    ("고지혈증(동맥경화) 관리 약물 계열은?", "동맥경화용제",
     "DUR 분류: [02180]동맥경화용제 계열", "DUR-분류/class=02180"),
    ("불면 단기 관리에 쓰이는 약물 계열은?", "최면진정제",
     "DUR 분류: [01120]최면진정제 계열", "DUR-분류/class=01120"),
    ("경도 통증에 쓰이는 진통제 계열은?", "해열·진통·소염제",
     "DUR 분류: [01140]해열.진통.소염제 계열", "DUR-분류/class=01140"),
]
for q, grp, ev, src in groups:
    add(query=q, context="",
        grounded=[{"drug": grp, "dose_note": "약물 계열명(고유명 아님·비채점, dose_max 없음).",
                   "evidence": ev, "source": src}],
        distractor_drugs=[], allow_unspecified=True)

print("총 생성 케이스:", len(cases))
out = {"schema_version": "S5-v1(트윈 확정 스키마 + dose_note 확장)",
       "note": "grounded.dose_max = DUR 1일 최대투여량 상한(용량 케이스만). 채점: 출력 용량 "
               "<= dose_max = 통과, 초과 = 용량환각 FAIL, distractor 약물+용량 = 약물환각 "
               "FAIL, dose_max 없는 계열 언급 = 허용(비채점). 질량단위 환산(4g=4000mg) 적용.",
       "cases": cases}
import os as _os
_out = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "config", "s5_groundedness_dataset.json")
with open(_out, "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("→ config/s5_groundedness_dataset.json 저장")
# 샘플 3건 출력
for c in cases[:1] + cases[12:13] + cases[20:21]:
    print(json.dumps(c, ensure_ascii=False))
