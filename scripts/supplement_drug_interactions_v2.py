#!/usr/bin/env python3
"""약물 상호작용 DB 추가 보충 — 97건→200건+ (한국 임상 빈출 약물)"""
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "clinic_codes.db")

# 추가 약물 상호작용 (103건 목표)
# (drug_a, drug_b, severity, description, mechanism, clinical_effect, management, evidence_level)
INTERACTIONS = [
    # === Major (40건) ===
    ("Warfarin", "Rifampin", "major", "항응고 효과 대폭 감소", "CYP 유도 (1A2,2C9,3A4)", "혈전 위험", "INR 주 2회 모니터링, 용량 2-3배 증량", "A"),
    ("Warfarin", "Phenytoin", "major", "INR 불안정", "CYP2C9 경쟁", "출혈 또는 혈전", "INR 밀착 모니터링", "A"),
    ("Warfarin", "Sulfamethoxazole", "major", "INR 급상승", "CYP2C9 억제 + 비타민K 합성 억제", "출혈 위험", "INR 모니터링, 대체 항생제 고려", "A"),
    ("Clopidogrel", "Esomeprazole", "major", "항혈소판 효과 감소", "CYP2C19 억제", "심혈관 이벤트 위험", "판토프라졸로 대체", "A"),
    ("Methotrexate", "Trimethoprim", "major", "MTX 독성 증가", "신세관 분비 경쟁", "골수억제, 범혈구감소증", "병용 금기", "A"),
    ("Cisapride", "Ketoconazole", "major", "QT 연장", "CYP3A4 억제", "Torsades de Pointes", "절대 병용 금기", "A"),
    ("Ergotamine", "Clarithromycin", "major", "에르고타민 독성", "CYP3A4 억제", "말초혈관 허혈, 괴저", "병용 금기", "A"),
    ("Pimozide", "Clarithromycin", "major", "QT 연장", "CYP3A4 억제", "Torsades de Pointes", "병용 금기", "A"),
    ("Colchicine", "Clarithromycin", "major", "콜히친 독성 증가", "P-gp + CYP3A4 억제", "다장기 부전", "신/간장애 시 병용 금기", "A"),
    ("Dabigatran", "Dronedarone", "major", "출혈 위험 증가", "P-gp 억제", "위장관/두개내 출혈", "병용 금기", "A"),
    ("Rivaroxaban", "Ketoconazole", "major", "항응고 효과 과다", "CYP3A4 + P-gp 동시 억제", "출혈", "병용 금기", "A"),
    ("Simvastatin", "Cyclosporine", "major", "횡문근융해증", "CYP3A4 억제 + OATP 억제", "횡문근융해증, 신부전", "심바스타틴 금기, 프라바스타틴 대체", "A"),
    ("Atorvastatin", "Cyclosporine", "major", "스타틴 독성 증가", "CYP3A4 + OATP 억제", "횡문근융해증", "아토르바스타틴 10mg 이하", "A"),
    ("Rosuvastatin", "Cyclosporine", "major", "로수바스타틴 농도 7배 상승", "OATP + BCRP 억제", "횡문근융해증", "로수바스타틴 5mg 이하", "A"),
    ("Dapoxetine", "MAO inhibitor", "major", "세로토닌 증후군", "세로토닌 과다", "고열, 경련", "절대 병용 금기", "A"),
    ("Linezolid", "SSRI", "major", "세로토닌 증후군", "MAO-A 억제", "고열, 근경직, 경련", "SSRI 중단 또는 대체", "A"),
    ("Fentanyl", "CYP3A4 inhibitor", "major", "호흡 억제 위험", "펜타닐 대사 감소", "과다 진정, 호흡 정지", "용량 감소, 모니터링", "A"),
    ("Oxycodone", "CYP3A4 inhibitor", "major", "호흡 억제", "옥시코돈 대사 감소", "과다 진정", "용량 50% 감소", "A"),
    ("Methadone", "CYP3A4 inhibitor", "major", "QT 연장 + 호흡 억제", "대사 감소", "부정맥, 호흡 정지", "ECG 모니터링, 용량 감소", "A"),
    ("Carbamazepine", "Voriconazole", "major", "보리코나졸 효과 소실", "CYP3A4 강력 유도", "항진균 치료 실패", "병용 금기", "A"),
    ("Fluconazole", "Terfenadine", "major", "QT 연장", "CYP3A4 억제", "심정지", "병용 금기", "A"),
    ("Quinidine", "Amiodarone", "major", "QT 연장 시너지", "Class III 항부정맥 중복", "Torsades de Pointes", "병용 금기", "A"),
    ("Sotalol", "Amiodarone", "major", "과도한 QT 연장", "Class III 중복", "심정지", "병용 금기", "A"),
    ("Clozapine", "Carbamazepine", "major", "무과립구증 위험 증가", "골수 독성 중복", "골수억제", "병용 금기", "A"),
    ("Itraconazole", "Lovastatin", "major", "횡문근융해증", "CYP3A4 강력 억제", "CK 상승, 신부전", "병용 금기", "A"),
    ("Nifedipine", "Rifampin", "major", "혈압 강하 효과 소실", "CYP3A4 유도", "혈압 상승", "대체 항결핵제 또는 대체 CCB 고려", "B"),
    ("Tamoxifen", "Fluoxetine", "major", "타목시펜 효과 감소", "CYP2D6 강력 억제", "유방암 재발 위험", "시탈로프람으로 대체", "A"),
    ("Tamoxifen", "Paroxetine", "major", "타목시펜 효과 감소", "CYP2D6 강력 억제", "유방암 재발 위험", "벤라팍신 또는 시탈로프람 대체", "A"),
    ("Theophylline", "Fluvoxamine", "major", "테오필린 농도 3배 상승", "CYP1A2 강력 억제", "구역, 부정맥, 경련", "병용 시 테오필린 1/3로 감량", "A"),
    ("Azathioprine", "Febuxostat", "major", "아자치오프린 독성", "XO 억제", "골수억제", "병용 금기 (알로퓨리놀보다 강력)", "A"),
    ("Mercaptopurine", "Allopurinol", "major", "6-MP 독성 증가", "XO 억제", "골수억제", "6-MP 75% 감량", "A"),
    ("Ivabradine", "Verapamil", "major", "과도한 서맥", "CYP3A4 억제 + 심박수 감소 중복", "심정지", "병용 금기", "A"),
    ("Ranolazine", "Ketoconazole", "major", "QT 연장", "CYP3A4 강력 억제", "부정맥", "병용 금기", "A"),
    ("Dofetilide", "Verapamil", "major", "QT 연장", "신세관 분비 억제", "Torsades de Pointes", "병용 금기", "A"),
    ("Dronedarone", "Cyclosporine", "major", "드로네다론 농도 상승", "CYP3A4 + P-gp 억제", "간독성, QT 연장", "병용 금기", "A"),
    ("Everolimus", "Ketoconazole", "major", "에베로리무스 농도 15배 상승", "CYP3A4 + P-gp 동시 억제", "면역억제 과다", "병용 금기", "A"),
    ("Sirolimus", "Voriconazole", "major", "시롤리무스 농도 급상승", "CYP3A4 억제", "면역억제 과다, 감염", "병용 시 시롤리무스 90% 감량", "A"),
    ("Tolvaptan", "Ketoconazole", "major", "톨밥탄 농도 5배 상승", "CYP3A4 억제", "간독성", "용량 감소 또는 병용 금기", "A"),
    ("Ticagrelor", "Ketoconazole", "major", "출혈 위험 증가", "CYP3A4 억제", "위장관 출혈", "병용 회피", "B"),
    ("Apixaban", "Ketoconazole", "major", "항응고 효과 과다", "CYP3A4 + P-gp 동시 억제", "출혈", "아픽사반 50% 감량", "A"),

    # === Moderate (63건) ===
    ("Metformin", "Cimetidine", "moderate", "메트포르민 농도 상승", "신세관 분비 경쟁", "젖산산증 위험", "라니티딘으로 대체", "B"),
    ("Metformin", "Topiramate", "moderate", "대사산증 위험", "탄산탈수효소 억제", "산증 악화", "중탄산 모니터링", "B"),
    ("Amlodipine", "Cyclosporine", "moderate", "사이클로스포린 농도 상승", "CYP3A4 경쟁", "신독성", "농도 모니터링", "B"),
    ("Lisinopril", "Potassium", "moderate", "고칼륨혈증", "RAAS 억제", "부정맥", "칼륨 모니터링", "B"),
    ("Losartan", "Potassium", "moderate", "고칼륨혈증", "ARB + 칼륨 보충", "부정맥", "칼륨 모니터링", "B"),
    ("Enalapril", "Spironolactone", "moderate", "고칼륨혈증", "RAAS 억제 + 칼륨 보존", "부정맥", "칼륨 주 1회 모니터링", "A"),
    ("Furosemide", "Gentamicin", "moderate", "이독성 증가", "이독성 중복", "청력 손실", "청력 모니터링", "B"),
    ("Furosemide", "Lithium", "moderate", "리튬 독성 증가", "나트륨 배설 증가", "리튬 재흡수 증가", "리튬 농도 모니터링", "B"),
    ("Hydrochlorothiazide", "Lithium", "moderate", "리튬 독성", "근위 세관 재흡수 증가", "진전, 설사", "리튬 농도 모니터링", "A"),
    ("Prednisolone", "Fluoroquinolone", "moderate", "건 파열 위험 증가", "건 취약성 중복", "아킬레스건 파열", "건 통증 시 즉시 중단", "B"),
    ("Dexamethasone", "Phenytoin", "moderate", "덱사메타손 효과 감소", "CYP3A4 유도", "스테로이드 효과 불충분", "용량 증가 고려", "B"),
    ("Clarithromycin", "Digoxin", "moderate", "디곡신 농도 상승", "P-gp 억제", "부정맥", "디곡신 농도 모니터링", "A"),
    ("Azithromycin", "Warfarin", "moderate", "INR 상승 가능", "간접 영향", "출혈 위험", "INR 모니터링", "B"),
    ("Doxycycline", "Warfarin", "moderate", "INR 상승", "장내 비타민K 합성 억제", "출혈", "INR 모니터링", "B"),
    ("Ciprofloxacin", "Warfarin", "moderate", "INR 상승", "CYP1A2 억제", "출혈", "INR 모니터링", "B"),
    ("Omeprazole", "Clopidogrel", "moderate", "클로피도그렐 활성화 감소", "CYP2C19 약한 억제", "혈전 위험", "판토프라졸 대체 권장", "B"),
    ("Pantoprazole", "Methotrexate", "moderate", "MTX 배설 감소", "H+/K+-ATPase 영향", "MTX 독성", "고용량 MTX 시 PPI 중단", "B"),
    ("Sertraline", "Tramadol", "moderate", "세로토닌 증후군 + 경련", "세로토닌 중복", "초조, 발한, 경련", "대체 진통제 고려", "B"),
    ("Paroxetine", "Tamsulosin", "moderate", "탐수로신 농도 상승", "CYP2D6 억제", "기립성 저혈압", "탐수로신 0.2mg으로 시작", "B"),
    ("Fluoxetine", "Metoprolol", "moderate", "베타차단제 효과 증가", "CYP2D6 억제", "서맥, 저혈압", "메토프롤롤 감량", "B"),
    ("Escitalopram", "Tramadol", "moderate", "세로토닌 증후군 위험", "세로토닌 중복", "진전, 발한", "주의 관찰", "B"),
    ("Venlafaxine", "Tramadol", "moderate", "경련 역치 저하", "세로토닌 + 노르에피네프린 중복", "경련", "대체 진통제", "B"),
    ("Duloxetine", "Warfarin", "moderate", "출혈 위험 증가", "세로토닌 + 항응고 중복", "위장관 출혈", "INR 모니터링", "B"),
    ("Mirtazapine", "Alcohol", "moderate", "과도한 진정", "CNS 억제 중복", "졸음, 운동실조", "음주 제한", "B"),
    ("Gabapentin", "Opioid", "moderate", "호흡 억제 위험", "CNS 억제 중복", "과다 진정", "저용량 시작", "B"),
    ("Pregabalin", "Opioid", "moderate", "호흡 억제 위험", "CNS 억제 중복", "과다 진정, 사망 위험", "저용량, 밀착 모니터링", "A"),
    ("Benzodiazepine", "Opioid", "moderate", "호흡 억제", "CNS 억제 시너지", "과다 진정, 사망", "최소 용량, 최단 기간", "A"),
    ("Zolpidem", "Alcohol", "moderate", "과도한 진정", "CNS 억제 중복", "졸음보행, 기억상실", "병용 금지", "A"),
    ("Insulin", "ACE inhibitor", "moderate", "저혈당 위험 증가", "인슐린 감수성 증가", "저혈당", "혈당 모니터링", "B"),
    ("Glimepiride", "Fluconazole", "moderate", "저혈당", "CYP2C9 억제", "심한 저혈당", "혈당 모니터링, SU 감량", "B"),
    ("Pioglitazone", "Gemfibrozil", "moderate", "피오글리타존 농도 상승", "CYP2C8 억제", "부종, 심부전", "피오글리타존 15mg 이하", "B"),
    ("Donepezil", "Beta-blocker", "moderate", "서맥 위험", "콜린 효과 + 심박수 감소", "서맥, 실신", "심박수 모니터링", "B"),
    ("Rivastigmine", "Beta-blocker", "moderate", "서맥", "콜린 효과 중복", "실신", "심박수 모니터링", "B"),
    ("Sildenafil", "Alpha-blocker", "moderate", "저혈압", "혈관확장 중복", "기립성 저혈압, 실신", "4시간 간격 투여", "A"),
    ("Tadalafil", "Alpha-blocker", "moderate", "저혈압", "혈관확장 중복", "어지러움", "타다라필 5mg 이하", "A"),
    ("Iron", "Levothyroxine", "moderate", "갑상선 약물 흡수 감소", "킬레이트 형성", "TSH 상승", "4시간 간격", "A"),
    ("Iron", "Ciprofloxacin", "moderate", "항생제 흡수 감소", "킬레이트 형성", "치료 실패", "2시간 간격", "A"),
    ("Iron", "Tetracycline", "moderate", "양쪽 흡수 감소", "킬레이트 형성", "치료 실패", "2시간 간격", "A"),
    ("Calcium", "Bisphosphonate", "moderate", "비스포스포네이트 흡수 감소", "킬레이트 형성", "골다공증 치료 실패", "최소 30분 간격", "A"),
    ("Antacid", "Levothyroxine", "moderate", "갑상선 약물 흡수 감소", "위산 중화", "TSH 상승", "4시간 간격", "A"),
    ("Cholestyramine", "Levothyroxine", "moderate", "갑상선 약물 흡수 감소", "담즙산 결합", "TSH 상승", "4시간 간격", "A"),
    ("Cholestyramine", "Warfarin", "moderate", "항응고 효과 감소", "담즙산 결합으로 흡수 감소", "혈전", "6시간 간격", "B"),
    ("Sucralfate", "Fluoroquinolone", "moderate", "항생제 흡수 감소", "킬레이트 형성", "치료 실패", "2시간 전 퀴놀론 투여", "A"),
    ("Carbamazepine", "OCP", "moderate", "피임 실패", "CYP3A4 유도", "원치 않는 임신", "추가 피임법", "A"),
    ("Phenytoin", "OCP", "moderate", "피임 효과 감소", "CYP3A4 유도", "원치 않는 임신", "추가 피임법", "A"),
    ("St. John's Wort", "SSRI", "moderate", "세로토닌 증후군 위험", "세로토닌 중복", "초조, 발열", "병용 회피", "B"),
    ("St. John's Wort", "OCP", "moderate", "피임 효과 감소", "CYP3A4 유도", "원치 않는 임신", "병용 금기", "A"),
    ("St. John's Wort", "Warfarin", "moderate", "INR 감소", "CYP 유도", "혈전 위험", "병용 회피", "A"),
    ("St. John's Wort", "Cyclosporine", "moderate", "면역억제 효과 감소", "CYP3A4 + P-gp 유도", "이식 거부 위험", "병용 금기", "A"),
    ("Amiodarone", "Digoxin", "moderate", "디곡신 농도 상승", "P-gp 억제 + 신배설 감소", "서맥", "디곡신 50% 감량", "A"),
    ("Phenobarbital", "Warfarin", "moderate", "INR 감소", "CYP 유도", "혈전", "INR 모니터링, 용량 조절", "B"),
    ("Valproate", "Lamotrigine", "moderate", "라모트리진 농도 2배 상승", "글루쿠론산 결합 억제", "SJS/TEN 위험", "라모트리진 50% 감량", "A"),
    ("Carbamazepine", "Lamotrigine", "moderate", "라모트리진 농도 감소", "글루쿠론산 결합 유도", "경련 조절 실패", "라모트리진 증량 필요", "A"),
    ("Phenytoin", "Doxycycline", "moderate", "독시사이클린 반감기 감소", "CYP 유도", "항생제 효과 저하", "대체 항생제 또는 증량", "B"),
    ("Metoclopramide", "Haloperidol", "moderate", "추체외로 증상 증가", "도파민 차단 중복", "근긴장이상", "병용 주의", "B"),
    ("Domperidone", "Ketoconazole", "moderate", "QT 연장", "CYP3A4 억제", "부정맥", "돔페리돈 10mg 이하", "A"),
    ("Warfarin", "Cranberry", "moderate", "INR 상승 가능", "CYP2C9 억제 (in vitro)", "출혈", "대량 섭취 회피", "C"),
    ("Warfarin", "Ginseng", "moderate", "INR 감소 가능", "항혈소판 + CYP 유도", "INR 불안정", "정기 INR 모니터링", "C"),
    ("Warfarin", "Green tea", "moderate", "INR 감소", "비타민 K 함유", "혈전", "일정량 유지", "C"),
    ("Warfarin", "Vitamin E", "moderate", "출혈 위험 증가", "항혈소판 효과", "피하출혈", "400IU 이하 권장", "C"),
    ("Atorvastatin", "Diltiazem", "moderate", "스타틴 농도 상승", "CYP3A4 경쟁", "근육통", "스타틴 최소 용량", "B"),
    ("Simvastatin", "Amlodipine", "moderate", "횡문근융해증 위험", "CYP3A4 경쟁", "CK 상승", "심바스타틴 20mg 이하", "A"),
    ("Rosuvastatin", "Gemfibrozil", "moderate", "횡문근융해증 위험", "OATP 억제", "CK 상승", "로수바스타틴 10mg 이하", "B"),
]


def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] {DB_PATH} not found")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    before = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    print(f"[before] drug_interactions: {before}")

    added = 0
    skipped = 0
    for row in INTERACTIONS:
        exists = conn.execute(
            "SELECT 1 FROM drug_interactions WHERE LOWER(drug_a)=LOWER(?) AND LOWER(drug_b)=LOWER(?)",
            (row[0], row[1])).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO drug_interactions (drug_a, drug_b, severity, description, mechanism, clinical_effect, management, evidence_level) VALUES (?,?,?,?,?,?,?,?)",
                row)
            added += 1
        else:
            skipped += 1

    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    conn.close()

    print(f"[after] drug_interactions: {after} (+{added}, skipped {skipped})")
    by_severity = f"major {sum(1 for r in INTERACTIONS if r[2]=='major')}, moderate {sum(1 for r in INTERACTIONS if r[2]=='moderate')}"
    print(f"[data] 추가 데이터: {len(INTERACTIONS)}건 ({by_severity})")


if __name__ == "__main__":
    main()
