#!/usr/bin/env python3
"""의료 데이터 3차 확충 — KCD 598→1000 목표

보강 영역: 산부인과, 외상, 종양, 건진, 감염, 소화기, 호흡기, 근골격 추가
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_db():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "clinic_codes.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def expand_kcd():
    conn = get_db()
    existing = set(r[0] for r in conn.execute("SELECT code FROM kcd_codes").fetchall())

    codes = [
        # 산부인과 (29→55)
        ("N70.0", "급성 난관염 및 난소염", "Acute salpingitis and oophoritis", "산부인과"),
        ("N71.0", "급성 자궁염증질환", "Acute inflammatory disease of uterus", "산부인과"),
        ("N72", "자궁경부의 염증질환", "Inflammatory disease of cervix uteri", "산부인과"),
        ("N73.0", "급성 골반복막염", "Acute parametritis and pelvic cellulitis", "산부인과"),
        ("N75.1", "바르톨린선 농양", "Abscess of Bartholin gland", "산부인과"),
        ("N76.0", "급성 질염", "Acute vaginitis", "산부인과"),
        ("N77.1", "칸디다 질염", "Candidal vulvovaginitis", "산부인과"),
        ("N80.0", "자궁 자궁내막증", "Endometriosis of uterus", "산부인과"),
        ("N83.0", "난소 낭종", "Follicular cyst of ovary", "산부인과"),
        ("N83.2", "기타 난소 낭종", "Other ovarian cysts", "산부인과"),
        ("N84.0", "자궁체부 폴립", "Polyp of corpus uteri", "산부인과"),
        ("N85.0", "자궁내막 선종성 과형성", "Endometrial glandular hyperplasia", "산부인과"),
        ("N87.9", "자궁경부 이형성증", "Dysplasia of cervix uteri", "산부인과"),
        ("N91.0", "원발성 무월경", "Primary amenorrhoea", "산부인과"),
        ("N91.1", "속발성 무월경", "Secondary amenorrhoea", "산부인과"),
        ("N92.1", "불규칙 월경", "Irregular menstruation", "산부인과"),
        ("N93.9", "비정상 자궁출혈", "Abnormal uterine bleeding", "산부인과"),
        ("N94.6", "월경통, 상세불명", "Dysmenorrhoea, unspecified", "산부인과"),
        ("N95.1", "폐경 및 여성 갱년기 상태", "Menopausal and climacteric states", "산부인과"),
        ("N97.9", "여성 불임", "Female infertility", "산부인과"),
        ("O00.1", "난관 임신", "Tubal pregnancy", "산부인과"),
        ("O03.9", "완전 자연유산", "Complete spontaneous abortion", "산부인과"),
        ("O14.1", "중증 전자간증", "Severe pre-eclampsia", "산부인과"),
        ("O15.0", "자간증", "Eclampsia in pregnancy", "산부인과"),
        ("O24.4", "임신성 당뇨병", "Gestational diabetes mellitus", "산부인과"),
        ("O42.9", "조기양막파수", "Premature rupture of membranes", "산부인과"),
        # 외상 (43→70)
        ("S00.0", "두피의 표재성 손상", "Superficial injury of scalp", "외상"),
        ("S01.0", "두피의 열린 상처", "Open wound of scalp", "외상"),
        ("S02.0", "두개골의 골절", "Fracture of vault of skull", "외상"),
        ("S05.0", "결막 이물", "Conjunctival foreign body", "외상"),
        ("S09.9", "머리의 손상, 상세불명", "Unspecified injury of head", "외상"),
        ("S12.0", "제1경추 골절", "Fracture of first cervical vertebra", "외상"),
        ("S13.4", "경추 염좌 및 긴장", "Sprain of cervical spine", "외상"),
        ("S22.3", "늑골의 골절", "Fracture of rib", "외상"),
        ("S27.0", "외상성 기흉", "Traumatic pneumothorax", "외상"),
        ("S32.0", "요추 골절", "Fracture of lumbar vertebra", "외상"),
        ("S33.5", "요추 염좌", "Sprain of lumbar spine", "외상"),
        ("S42.2", "상완골 상단의 골절", "Fracture of upper end of humerus", "외상"),
        ("S43.0", "어깨 탈구", "Dislocation of shoulder joint", "외상"),
        ("S52.0", "요골 두부의 골절", "Fracture of head of radius", "외상"),
        ("S62.3", "중수골의 골절", "Fracture of metacarpal bone", "외상"),
        ("S63.5", "손가락 염좌", "Sprain of finger", "외상"),
        ("S72.1", "전자부 골절", "Pertrochanteric fracture", "외상"),
        ("S73.0", "고관절 탈구", "Dislocation of hip", "외상"),
        ("S76.1", "대퇴사두근 손상", "Injury of quadriceps muscle", "외상"),
        ("S82.1", "경골 근위단 골절", "Fracture of proximal end of tibia", "외상"),
        ("S82.6", "외측 복사뼈 골절", "Fracture of lateral malleolus", "외상"),
        ("S83.0", "슬개골 탈구", "Dislocation of patella", "외상"),
        ("S83.2", "반월판 파열", "Tear of meniscus, current", "외상"),
        ("S86.0", "아킬레스건 파열", "Injury of Achilles tendon", "외상"),
        ("S92.0", "종골의 골절", "Fracture of calcaneus", "외상"),
        ("S93.4", "발목 염좌", "Sprain of ankle", "외상"),
        ("T20.0", "머리 및 목의 화상", "Burn of head and neck", "외상"),
        ("T30.0", "화상, 상세불명 신체부위", "Burn of unspecified body region", "외상"),
        ("T63.0", "뱀독의 독작용", "Toxic effect of snake venom", "외상"),
        ("T78.4", "알레르기, 상세불명", "Allergy, unspecified", "외상"),
        # 종양 (37→60)
        ("C00.9", "입술의 악성 신생물", "Malignant neoplasm of lip", "종양"),
        ("C02.9", "혀의 악성 신생물", "Malignant neoplasm of tongue", "종양"),
        ("C06.9", "구강의 악성 신생물", "Malignant neoplasm of mouth", "종양"),
        ("C07", "이하선의 악성 신생물", "Malignant neoplasm of parotid gland", "종양"),
        ("C10.9", "구인두의 악성 신생물", "Malignant neoplasm of oropharynx", "종양"),
        ("C11.9", "비인두의 악성 신생물", "Malignant neoplasm of nasopharynx", "종양"),
        ("C15.9", "식도의 악성 신생물", "Malignant neoplasm of oesophagus", "종양"),
        ("C22.0", "간세포 암종", "Hepatocellular carcinoma", "종양"),
        ("C23", "담낭의 악성 신생물", "Malignant neoplasm of gallbladder", "종양"),
        ("C24.0", "간외 담관의 악성 신생물", "Malignant neoplasm of extrahepatic bile duct", "종양"),
        ("C25.0", "췌장 두부의 악성 신생물", "Malignant neoplasm of head of pancreas", "종양"),
        ("C32.9", "후두의 악성 신생물", "Malignant neoplasm of larynx", "종양"),
        ("C43.9", "악성 흑색종", "Malignant melanoma of skin", "종양"),
        ("C44.9", "기저세포암", "BCC of skin", "종양"),
        ("C49.9", "연부조직의 악성 신생물", "Malignant neoplasm of soft tissue", "종양"),
        ("C53.9", "자궁경부의 악성 신생물", "Malignant neoplasm of cervix uteri", "종양"),
        ("C54.1", "자궁체부의 악성 신생물", "Malignant neoplasm of endometrium", "종양"),
        ("C62.9", "고환의 악성 신생물", "Malignant neoplasm of testis", "종양"),
        ("C64", "신장의 악성 신생물(신우 제외)", "Malignant neoplasm of kidney", "종양"),
        ("C71.9", "뇌의 악성 신생물", "Malignant neoplasm of brain", "종양"),
        ("C82.9", "여포성 림프종", "Follicular lymphoma", "종양"),
        ("C83.3", "미만성 큰B세포 림프종", "DLBCL", "종양"),
        ("C90.0", "다발성 골수종", "Multiple myeloma", "종양"),
        # 감염 추가 (38→55)
        ("A01.0", "장티푸스", "Typhoid fever", "감염"),
        ("A02.0", "살모넬라 장염", "Salmonella enteritis", "감염"),
        ("A04.7", "클로스트리디움 디피실 장염", "C. difficile enterocolitis", "감염"),
        ("A15.0", "폐결핵(배양 확인)", "Tuberculosis of lung", "감염"),
        ("A16.9", "결핵, 상세불명", "Respiratory tuberculosis, unspecified", "감염"),
        ("A37.9", "백일해", "Whooping cough", "감염"),
        ("A38", "성홍열", "Scarlet fever", "감염"),
        ("A39.0", "수막구균 수막염", "Meningococcal meningitis", "감염"),
        ("A40.9", "연쇄구균 패혈증", "Streptococcal sepsis", "감염"),
        ("A46", "단독(丹毒)", "Erysipelas", "감염"),
        ("A69.2", "라임병", "Lyme disease", "감염"),
        ("B00.1", "단순포진 수포성 피부염", "Herpesviral vesicular dermatitis", "감염"),
        ("B01.9", "수두", "Varicella", "감염"),
        ("B02.9", "대상포진", "Herpes zoster", "감염"),
        ("B05.9", "홍역", "Measles", "감염"),
        ("B06.9", "풍진", "Rubella", "감염"),
        ("B15.9", "A형 간염", "Hepatitis A", "감염"),
        ("B16.9", "B형 간염(급성)", "Acute hepatitis B", "감염"),
        ("B17.1", "C형 간염(급성)", "Acute hepatitis C", "감염"),
        ("B18.1", "B형 간염(만성)", "Chronic hepatitis B", "감염"),
        ("B18.2", "C형 간염(만성)", "Chronic hepatitis C", "감염"),
        ("B27.9", "감염성 단핵증", "Infectious mononucleosis", "감염"),
        ("B35.1", "손발톱 백선(무좀)", "Tinea unguium", "감염"),
        ("B37.0", "칸디다 구내염", "Candidal stomatitis", "감염"),
        ("B86", "옴(개선)", "Scabies", "감염"),
        # 건진/증상 추가 (9→20)
        ("Z00.0", "일반 건강검진", "General health check-up", "건진"),
        ("Z01.0", "눈 및 시력 검사", "Examination of eyes and vision", "건진"),
        ("Z01.1", "귀 및 청력 검사", "Examination of ears and hearing", "건진"),
        ("Z01.3", "혈압 검사", "Examination of blood pressure", "건진"),
        ("Z01.6", "방사선 검사", "Radiological examination", "건진"),
        ("Z12.1", "위장관 악성 종양 선별검사", "Screening for GI malignancy", "건진"),
        ("Z12.3", "유방 악성 종양 선별검사", "Screening for breast malignancy", "건진"),
        ("Z12.4", "자궁경부 악성 종양 선별검사", "Screening for cervical malignancy", "건진"),
        ("Z13.1", "당뇨병 선별검사", "Screening for diabetes mellitus", "건진"),
        ("Z23", "예방접종", "Need for immunization", "건진"),
        ("Z30.0", "피임 상담", "Contraceptive counselling", "건진"),
        # 호흡기 추가
        ("J10.1", "인플루엔자(바이러스 확인) 폐렴", "Influenza with pneumonia", "호흡기"),
        ("J11.1", "인플루엔자(미확인) 폐렴", "Influenza with pneumonia, unidentified", "호흡기"),
        ("J21.9", "급성 세기관지염", "Acute bronchiolitis", "호흡기"),
        ("J39.9", "상기도 질환, 상세불명", "Disease of upper respiratory tract", "호흡기"),
        ("J47", "기관지확장증", "Bronchiectasis", "호흡기"),
        ("J60", "탄광부 진폐증", "Coalworker pneumoconiosis", "호흡기"),
        ("J67.9", "과민성 폐렴", "Hypersensitivity pneumonitis", "호흡기"),
        ("J80", "성인호흡곤란증후군(ARDS)", "ARDS", "호흡기"),
        ("J81", "폐부종", "Pulmonary oedema", "호흡기"),
        ("J90", "흉막삼출", "Pleural effusion", "호흡기"),
        ("J93.1", "자발성 기흉", "Spontaneous pneumothorax", "호흡기"),
        # 소화기 추가
        ("K20", "식도염", "Oesophagitis", "소화기"),
        ("K22.1", "식도 궤양", "Ulcer of oesophagus", "소화기"),
        ("K29.0", "급성 출혈성 위염", "Acute haemorrhagic gastritis", "소화기"),
        ("K31.0", "급성 위 확장", "Acute dilatation of stomach", "소화기"),
        ("K42.9", "제대 탈장", "Umbilical hernia", "소화기"),
        ("K46.9", "복벽 탈장, 상세불명", "Abdominal hernia, unspecified", "소화기"),
        ("K52.9", "비감염성 위장염 및 대장염", "Noninfective gastroenteritis", "소화기"),
        ("K61.0", "항문 농양", "Anal abscess", "소화기"),
        ("K60.0", "항문 열상(치열)", "Acute anal fissure", "소화기"),
        ("K63.1", "장 천공", "Perforation of intestine", "소화기"),
        ("K75.0", "간 농양", "Abscess of liver", "소화기"),
        ("K80.0", "급성 담낭염을 동반한 담석증", "Calculus of GB with cholecystitis", "소화기"),
        ("K81.0", "급성 담낭염", "Acute cholecystitis", "소화기"),
        # 증상 추가
        ("R00.1", "서맥, 상세불명", "Bradycardia, unspecified", "증상"),
        ("R03.0", "고혈압 수치", "Elevated blood-pressure reading", "증상"),
        ("R05", "기침", "Cough", "증상"),
        ("R09.2", "호흡 정지", "Respiratory arrest", "증상"),
        ("R10.0", "급성 복통", "Acute abdomen", "증상"),
        ("R13", "연하곤란", "Dysphagia", "증상"),
        ("R17", "상세불명의 황달", "Unspecified jaundice", "증상"),
        ("R18", "복수", "Ascites", "증상"),
        ("R19.7", "설사, 상세불명", "Diarrhoea, unspecified", "증상"),
        ("R20.2", "이상감각(감각이상)", "Paraesthesia of skin", "증상"),
        ("R21", "발진 및 기타 비특이적 피부 발진", "Rash and other nonspecific skin eruption", "증상"),
        ("R22.1", "목의 국한성 부종", "Localized swelling of neck", "증상"),
        ("R25.1", "진전, 상세불명", "Tremor, unspecified", "증상"),
        ("R26.0", "운동실조 보행", "Ataxic gait", "증상"),
        ("R31", "혈뇨", "Haematuria", "증상"),
        ("R33", "뇨정체", "Retention of urine", "증상"),
        ("R40.0", "졸림(기면)", "Somnolence", "증상"),
        ("R40.2", "혼수", "Coma, unspecified", "증상"),
        ("R41.0", "방향감각상실", "Disorientation", "증상"),
        ("R47.0", "실어증(실어)", "Dysphasia and aphasia", "증상"),
        ("R52", "통증, 상세불명", "Pain, unspecified", "증상"),
        ("R57.0", "심인성 쇼크", "Cardiogenic shock", "증상"),
        ("R57.1", "저혈량성 쇼크", "Hypovolaemic shock", "증상"),
        ("R57.8", "기타 쇼크", "Other shock", "증상"),
        ("R58", "출혈, 상세불명", "Haemorrhage NEC", "증상"),
        ("R59.0", "국한성 림프절비대", "Localized enlarged lymph nodes", "증상"),
        ("R60.0", "국한성 부종", "Localized oedema", "증상"),
        ("R63.4", "비정상 체중 감소", "Abnormal weight loss", "증상"),
        ("R73.0", "혈당 이상", "Abnormal glucose", "증상"),
    ]

    added = 0
    for code, name_ko, name_en, category in codes:
        if code not in existing:
            conn.execute("INSERT OR IGNORE INTO kcd_codes (code, name_ko, name_en, category) VALUES (?,?,?,?)",
                         (code, name_ko, name_en, category))
            added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM kcd_codes").fetchone()[0]
    conn.close()
    print(f"[KCD] +{added}건 (총 {total}건)")
    return added


if __name__ == "__main__":
    print("=" * 50)
    print("KCD 3차 확충 (목표: 1000건)")
    print("=" * 50)
    expand_kcd()
