#!/usr/bin/env python3
"""의료 데이터 보충 — 약물 상호작용 + 삭감사유 DB 추가"""
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "clinic_codes.db")

# 추가 약물 상호작용 (한국 임상 빈출 약물 중심)
ADDITIONAL_INTERACTIONS = [
    # (drug_a, drug_b, severity, description, mechanism, clinical_effect, management, evidence_level)
    # --- Major ---
    ("Methotrexate", "NSAIDs", "major", "MTX 독성 증가", "신장 배설 감소", "골수억제, 점막염", "병용 금기 또는 용량 감소", "A"),
    ("Lithium", "NSAIDs", "major", "리튬 독성 증가", "신장 배설 감소", "진전, 구역, 신독성", "리튬 농도 모니터링", "A"),
    ("Digoxin", "Amiodarone", "major", "디곡신 농도 2배 상승", "P-gp 억제", "서맥, 부정맥", "디곡신 용량 50% 감소", "A"),
    ("Digoxin", "Verapamil", "major", "디곡신 농도 상승", "신장 배설 감소", "서맥, AV block", "디곡신 용량 감소", "A"),
    ("Clopidogrel", "Omeprazole", "major", "클로피도그렐 효과 감소", "CYP2C19 억제", "혈전 위험 증가", "판토프라졸로 대체", "A"),
    ("Simvastatin", "Clarithromycin", "major", "횡문근융해증 위험", "CYP3A4 억제", "근육통, CK 상승", "병용 금기", "A"),
    ("Simvastatin", "Itraconazole", "major", "횡문근융해증 위험", "CYP3A4 강력 억제", "횡문근융해증", "병용 금기", "A"),
    ("Atorvastatin", "Clarithromycin", "major", "스타틴 독성 증가", "CYP3A4 억제", "근육통, 간독성", "용량 제한 또는 대체", "B"),
    ("Metformin", "Contrast media", "major", "젖산산증 위험", "신기능 저하", "대사산증", "CT 전후 48시간 중단", "A"),
    ("Potassium", "Spironolactone", "major", "고칼륨혈증", "칼륨 보존 효과 중복", "심정지 위험", "칼륨 모니터링, 병용 주의", "A"),
    ("Potassium", "ACE inhibitor", "major", "고칼륨혈증", "RAAS 억제로 칼륨 배설 감소", "부정맥", "칼륨 모니터링", "A"),
    ("Sildenafil", "Nitrate", "major", "급격한 저혈압", "NO/cGMP 경로 시너지", "쇼크, 실신", "절대 병용 금기", "A"),
    ("MAO inhibitor", "SSRI", "major", "세로토닌 증후군", "세로토닌 과다", "고열, 경련, 사망 가능", "14일 washout 필수", "A"),
    ("MAO inhibitor", "Meperidine", "major", "세로토닌 증후군", "세로토닌 재흡수 억제", "고열, 발작", "절대 병용 금기", "A"),
    ("Phenytoin", "Fluconazole", "major", "페니토인 독성", "CYP2C9 억제", "운동실조, 안진", "농도 모니터링, 용량 감소", "A"),
    ("Theophylline", "Ciprofloxacin", "major", "테오필린 독성", "CYP1A2 억제", "구역, 경련, 부정맥", "테오필린 용량 50% 감소", "A"),
    ("Carbamazepine", "Erythromycin", "major", "CBZ 독성 증가", "CYP3A4 억제", "어지러움, 복시", "CBZ 농도 모니터링", "A"),
    ("Cyclosporine", "Ketoconazole", "major", "사이클로스포린 농도 상승", "CYP3A4 억제", "신독성", "용량 50-75% 감소", "A"),
    ("Tacrolimus", "Fluconazole", "major", "타크로리무스 농도 상승", "CYP3A4 억제", "신독성, 진전", "농도 모니터링, 용량 감소", "A"),
    ("Rifampin", "OCP", "major", "피임 실패", "CYP3A4 유도", "원치 않는 임신", "추가 피임법 사용", "A"),

    # --- Moderate ---
    ("Amlodipine", "Simvastatin", "moderate", "스타틴 농도 증가", "CYP3A4 경쟁", "근육통 위험", "심바스타틴 20mg 이하", "B"),
    ("Metformin", "Alcohol", "moderate", "젖산산증 위험 증가", "간 gluconeogenesis 억제", "대사산증", "과음 피할 것", "B"),
    ("Warfarin", "Acetaminophen", "moderate", "INR 상승 가능", "비타민K 대사 영향", "출혈 위험", "4g/일 이하, INR 모니터링", "B"),
    ("ACE inhibitor", "Potassium-sparing diuretic", "moderate", "고칼륨혈증", "칼륨 배설 감소 중복", "부정맥 위험", "칼륨 모니터링", "B"),
    ("Beta-blocker", "Insulin", "moderate", "저혈당 증상 은폐", "교감신경 차단", "저혈당 인지 지연", "혈당 주의 깊게 모니터링", "B"),
    ("SSRI", "Tramadol", "moderate", "세로토닌 증후군 위험", "세로토닌 재흡수 억제 중복", "진전, 발한, 초조", "저용량 시작, 주의 관찰", "B"),
    ("Ciprofloxacin", "Antacid", "moderate", "퀴놀론 흡수 감소", "킬레이트 형성", "항생제 효과 저하", "2시간 간격 투여", "A"),
    ("Tetracycline", "Calcium", "moderate", "항생제 흡수 감소", "킬레이트 형성", "치료 실패", "2시간 간격", "A"),
    ("Levothyroxine", "Calcium", "moderate", "갑상선 약물 흡수 감소", "킬레이트 형성", "TSH 상승", "4시간 간격", "A"),
    ("Levothyroxine", "Iron", "moderate", "갑상선 약물 흡수 감소", "킬레이트 형성", "TSH 상승", "4시간 간격", "A"),
    ("Phenytoin", "Valproate", "moderate", "유리 페니토인 상승", "단백결합 경쟁", "독성 증가 가능", "농도 모니터링", "B"),
    ("Grapefruit", "Felodipine", "moderate", "혈중농도 3배 증가", "장 CYP3A4 억제", "저혈압, 두통", "자몽 회피", "A"),
    ("Grapefruit", "Atorvastatin", "moderate", "스타틴 농도 증가", "장 CYP3A4 억제", "근육통", "자몽 회피", "B"),
    ("Diclofenac", "Lithium", "moderate", "리튬 농도 상승", "신장 배설 감소", "리튬 독성", "리튬 농도 모니터링", "B"),
    ("Fluoxetine", "Tramadol", "moderate", "경련 역치 저하", "세로토닌 + 경련 위험", "발작 위험", "대체 진통제 고려", "B"),
    ("Amiodarone", "Beta-blocker", "moderate", "과도한 서맥", "AV 전도 억제 중복", "서맥, 저혈압", "심박수 모니터링", "B"),
    ("Diltiazem", "Beta-blocker", "moderate", "과도한 서맥", "심장 전도 억제 중복", "서맥, 저혈압, 심부전", "심박수/혈압 모니터링", "B"),
    ("Verapamil", "Beta-blocker", "moderate", "과도한 심장 억제", "AV block + 음성 변력", "서맥, 심부전", "병용 주의", "A"),
    ("Allopurinol", "Azathioprine", "moderate", "아자치오프린 독성", "XO 억제로 대사 감소", "골수억제", "아자치오프린 75% 감량", "A"),
    ("Probenecid", "Methotrexate", "moderate", "MTX 독성 증가", "신세관 분비 경쟁", "골수억제", "MTX 용량 감소", "B"),
    ("Erythromycin", "Theophylline", "moderate", "테오필린 농도 상승", "CYP1A2 억제", "구역, 두통", "테오필린 농도 모니터링", "B"),
    ("Fluconazole", "Midazolam", "moderate", "진정 작용 연장", "CYP3A4 억제", "과도한 졸음", "미다졸람 용량 감소", "A"),
    ("Ketoconazole", "Omeprazole", "moderate", "케토코나졸 흡수 감소", "위산 억제", "항진균 효과 저하", "산성 음료와 복용", "B"),
    ("Prednisolone", "NSAIDs", "moderate", "소화성 궤양 위험 증가", "위점막 보호 감소 중복", "위장관 출혈", "PPI 병용 고려", "B"),
    ("Heparin", "Aspirin", "moderate", "출혈 위험 증가", "항응고 + 항혈소판 중복", "출혈", "출혈 모니터링", "A"),
    ("Clopidogrel", "Aspirin", "moderate", "출혈 위험 증가 (이중항혈소판)", "항혈소판 중복", "소화관 출혈", "PPI 병용 고려 (판토프라졸)", "A"),
    ("Metoclopramide", "Levodopa", "moderate", "레보도파 효과 감소", "도파민 수용체 차단", "파킨슨 악화", "돔페리돈으로 대체", "B"),
]

# 추가 삭감사유 (한국 건강보험심사평가원 기준)
ADDITIONAL_DENIAL_REASONS = [
    # (code, category, reason, description, regulation)
    ("D001", "약제", "1일 투여량 초과", "약제 허가사항 1일 최대 용량 초과 처방", "약제급여목록표"),
    ("D002", "약제", "투여일수 초과", "장기처방 가이드라인 초과 (1회 90일 이내)", "요양급여기준"),
    ("D003", "약제", "적응증 불일치", "약제 허가 적응증과 진단명 불일치", "약제급여목록표"),
    ("D004", "약제", "동일 계열 중복 처방", "같은 약효군 2종 이상 동시 처방", "약제급여기준"),
    ("D005", "약제", "동일 성분 중복", "동일 성분 다른 제형 중복 처방", "약제급여기준"),
    ("D006", "약제", "급여 비대상 약제", "비급여 대상 약제를 급여 청구", "약제급여목록표"),
    ("D007", "약제", "DUR 경고 미준수", "DUR 병용금기 약물 동시 처방", "의약품안전관리"),
    ("D008", "약제", "전문의약품 일반의 처방", "전문의약품을 전문과목 외 의사가 처방", "약사법"),
    ("D009", "약제", "처방전 없는 조제", "처방전 없이 전문의약품 조제", "약사법"),
    ("D010", "약제", "오리지널약 차액", "제네릭 대체 가능 시 오리지널약 차액", "약제급여기준"),
    ("E001", "검사", "검사 주기 미준수", "권장 검사 주기보다 빈번한 반복 검사", "요양급여기준"),
    ("E002", "검사", "비급여 검사 급여 청구", "건강검진 목적 검사를 급여 청구", "요양급여기준"),
    ("E003", "검사", "검사 적응증 미충족", "검사 시행 의학적 필요성 불인정", "요양급여기준"),
    ("E004", "검사", "동일 검사 중복", "같은 검사 단기간 중복 시행", "요양급여기준"),
    ("E005", "검사", "검사 수가 산정 오류", "검사 수가 코드 잘못 적용", "건강보험수가기준"),
    ("E006", "검사", "초음파 적응증 부적합", "초음파 검사 급여 인정 기준 미충족", "초음파급여기준"),
    ("E007", "검사", "MRI 사전승인 미이행", "MRI 사전승인 없이 시행", "MRI급여기준"),
    ("P001", "처치", "처치 수가 과다 산정", "실제 시행보다 높은 수가 청구", "건강보험수가기준"),
    ("P002", "처치", "처치 적응증 미충족", "처치의 의학적 필요성 불인정", "요양급여기준"),
    ("P003", "처치", "이중 산정", "하나의 처치를 분리하여 이중 청구", "건강보험수가기준"),
    ("P004", "처치", "재료대 별도 산정 불가", "처치 수가에 포함된 재료 별도 청구", "건강보험수가기준"),
    ("P005", "처치", "상급 처치 산정", "하위 처치를 상위 처치로 청구", "건강보험수가기준"),
    ("H001", "입원", "입원 적응증 미충족", "외래 치료 가능한 질환의 입원", "요양급여기준"),
    ("H002", "입원", "재원일수 초과", "표준 재원일수 대비 장기 입원", "DRG기준"),
    ("H003", "입원", "간호등급 미충족", "간호관리료 차등제 등급 미달", "입원료산정기준"),
    ("H004", "입원", "상급병실 차액", "환자 동의 없는 상급병실 사용", "요양급여기준"),
    ("R001", "진찰", "재진 간격 미준수", "동일 질환 단기간 반복 진찰", "요양급여기준"),
    ("R002", "진찰", "2인 이상 진찰 산정", "동일 환자 같은 날 2과 이상 진찰 중복", "진찰료산정기준"),
    ("R003", "진찰", "전화 상담 산정 오류", "전화 상담을 대면 진찰로 청구", "요양급여기준"),
    ("S001", "수술", "수술 적응증 미충족", "수술의 의학적 필요성 불인정", "요양급여기준"),
    ("S002", "수술", "수술 수가 오산정", "수술 난이도 분류 오류", "건강보험수가기준"),
    ("T001", "기타", "명세서 기재 오류", "진단명, 처방 내역 등 기재 불일치", "요양급여청구기준"),
    ("T002", "기타", "본인부담금 산정 오류", "본인부담률 적용 오류", "본인부담금산정기준"),
    ("T003", "기타", "산정특례 적용 오류", "중증질환 산정특례 자격 미확인", "산정특례기준"),
    ("T004", "기타", "의료급여 자격 미확인", "의료급여 수급권 자격 확인 누락", "의료급여법"),
    ("T005", "기타", "타 기관 청구 중복", "동일 환자 타 기관 동일 진료 중복 청구", "요양급여기준"),
    ("T006", "기타", "진료기록부 미비", "진료기록 미작성 또는 불충분", "의료법시행규칙"),
    ("T007", "기타", "서식 미비", "청구 서식 누락 또는 오류", "요양급여청구기준"),
    ("T008", "기타", "비급여 항목 급여 청구", "비급여 대상을 급여로 잘못 청구", "요양급여기준"),
    ("T009", "기타", "선별급여 본인부담 오류", "선별급여 본인부담률 잘못 적용", "선별급여기준"),
    ("T010", "기타", "응급의료 가산 부당", "비응급 환자에 응급 가산 적용", "응급의료법"),
]


def main():
    if not os.path.exists(DB_PATH):
        print(f"[ERROR] {DB_PATH} not found")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    # 기존 건수 확인
    before_di = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    before_dr = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]
    print(f"[before] drug_interactions: {before_di}, denial_reasons: {before_dr}")

    # 약물 상호작용 추가 (중복 방지)
    added_di = 0
    for row in ADDITIONAL_INTERACTIONS:
        exists = conn.execute(
            "SELECT 1 FROM drug_interactions WHERE LOWER(drug_a)=LOWER(?) AND LOWER(drug_b)=LOWER(?)",
            (row[0], row[1])).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO drug_interactions (drug_a, drug_b, severity, description, mechanism, clinical_effect, management, evidence_level) VALUES (?,?,?,?,?,?,?,?)",
                row)
            added_di += 1

    # 삭감사유 추가 (중복 방지)
    added_dr = 0
    for row in ADDITIONAL_DENIAL_REASONS:
        # (code, category, reason, description, regulation)
        denial_code, category, reason, description, regulation = row
        exists = conn.execute(
            "SELECT 1 FROM denial_reasons WHERE denial_code=?", (denial_code,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO denial_reasons (denial_code, category, subcategory, description, prevention_tip, source) VALUES (?,?,?,?,?,?)",
                (denial_code, category, reason, description, regulation, "보충 데이터"))
            added_dr += 1

    conn.commit()

    after_di = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    after_dr = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]
    conn.close()

    print(f"[drug_interactions] {before_di} → {after_di} (+{added_di})")
    print(f"[denial_reasons]   {before_dr} → {after_dr} (+{added_dr})")
    print(f"[done] 총 {added_di + added_dr}건 추가")


if __name__ == "__main__":
    main()
