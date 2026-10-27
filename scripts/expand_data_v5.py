#!/usr/bin/env python3
"""KCD 5차 확충 — 809→1000 목표 (191건 추가)"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def expand_kcd():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "clinic_codes.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    existing = set(r[0] for r in conn.execute("SELECT code FROM kcd_codes").fetchall())

    codes = [
        # === 소화기 세부 (63→80) ===
        ("K04.7", "근단주위 농양", "Periapical abscess", "소화기"),
        ("K05.1", "만성 치은염", "Chronic gingivitis", "소화기"),
        ("K08.1", "사고/발치에 의한 치아 상실", "Loss of teeth due to accident/extraction", "소화기"),
        ("K10.2", "턱뼈 염증 상태", "Inflammatory conditions of jaws", "소화기"),
        ("K11.2", "타액선염", "Sialolithiasis", "소화기"),
        ("K12.1", "구내염", "Other forms of stomatitis", "소화기"),
        ("K13.7", "구강점막 병변", "Other lesions of oral mucosa", "소화기"),
        ("K14.0", "설염", "Glossitis", "소화기"),
        ("K22.2", "식도 폐쇄", "Oesophageal obstruction", "소화기"),
        ("K22.5", "식도 게실", "Diverticulum of oesophagus", "소화기"),
        ("K29.3", "만성 표재성 위염", "Chronic superficial gastritis", "소화기"),
        ("K29.4", "만성 위축성 위염", "Chronic atrophic gastritis", "소화기"),
        ("K31.8", "위 및 십이지장 질환", "Other diseases of stomach and duodenum", "소화기"),
        ("K43.9", "복벽 절개 탈장", "Incisional hernia", "소화기"),
        ("K50.0", "소장의 크론병", "Crohn disease of small intestine", "소화기"),
        ("K51.0", "궤양성 전대장염", "Ulcerative pancolitis", "소화기"),
        ("K57.9", "게실질환, 상세불명", "Diverticular disease, unspecified", "소화기"),
        # === 종양 세부 (47→70) ===
        ("C17.9", "소장의 악성 신생물", "Malignant neoplasm of small intestine", "종양"),
        ("C19", "직장S상결장 이행부의 악성 신생물", "Malignant neoplasm of rectosigmoid junction", "종양"),
        ("C21.0", "항문의 악성 신생물", "Malignant neoplasm of anus", "종양"),
        ("C25.9", "췌장의 악성 신생물, 상세불명", "Malignant neoplasm of pancreas", "종양"),
        ("C37", "흉선의 악성 신생물", "Malignant neoplasm of thymus", "종양"),
        ("C38.4", "흉막의 악성 신생물(중피종)", "Malignant neoplasm of pleura (mesothelioma)", "종양"),
        ("C40.2", "하지 장골의 악성 신생물", "Malignant neoplasm of long bones of lower limb", "종양"),
        ("C41.0", "두개골 및 안면골의 악성 신생물", "Malignant neoplasm of bones of skull and face", "종양"),
        ("C45.0", "흉막 중피종", "Mesothelioma of pleura", "종양"),
        ("C48.0", "후복막의 악성 신생물", "Malignant neoplasm of retroperitoneum", "종양"),
        ("C55", "자궁의 악성 신생물, 부위 불명", "Malignant neoplasm of uterus, part unspecified", "종양"),
        ("C65", "신우의 악성 신생물", "Malignant neoplasm of renal pelvis", "종양"),
        ("C68.0", "요도의 악성 신생물", "Malignant neoplasm of urethra", "종양"),
        ("C69.2", "망막의 악성 신생물(망막모세포종)", "Retinoblastoma", "종양"),
        ("C72.0", "척수의 악성 신생물", "Malignant neoplasm of spinal cord", "종양"),
        ("C78.0", "폐의 이차성 악성 신생물(전이)", "Secondary malignant neoplasm of lung", "종양"),
        ("C78.7", "간의 이차성 악성 신생물(전이)", "Secondary malignant neoplasm of liver", "종양"),
        ("C79.3", "뇌의 이차성 악성 신생물(전이)", "Secondary malignant neoplasm of brain", "종양"),
        ("C79.5", "골의 이차성 악성 신생물(전이)", "Secondary malignant neoplasm of bone", "종양"),
        ("C85.9", "비호지킨 림프종, 상세불명", "Non-Hodgkin lymphoma, unspecified", "종양"),
        ("C92.0", "급성 골수성 백혈병", "Acute myeloid leukaemia", "종양"),
        ("C95.0", "급성 백혈병, 상세불명", "Acute leukaemia, unspecified", "종양"),
        ("D05.1", "유방 관내암종", "Intraductal carcinoma in situ of breast", "종양"),
        # === 감염 세부 (42→60) ===
        ("A18.0", "골 및 관절 결핵", "Tuberculosis of bones and joints", "감염"),
        ("A31.0", "비결핵 항산균 폐감염", "Pulmonary mycobacterial infection", "감염"),
        ("A41.0", "황색포도상구균 패혈증", "Staphylococcal sepsis", "감염"),
        ("A41.5", "그람음성균 패혈증", "Gram-negative sepsis", "감염"),
        ("A48.1", "레지오넬라증", "Legionnaires disease", "감염"),
        ("A56.0", "클라미디아 하부 요로생식기 감염", "Chlamydial LGT infection", "감염"),
        ("A60.0", "생식기 단순포진 감염", "Herpesviral infection of genitalia", "감염"),
        ("A63.0", "첨규 콘딜로마", "Anogenital warts", "감염"),
        ("A69.9", "스피로헤타 감염", "Spirochaetal infection, unspecified", "감염"),
        ("B00.0", "포진성 습진", "Eczema herpeticum", "감염"),
        ("B00.5", "포진성 안질환", "Herpesviral ocular disease", "감염"),
        ("B02.2", "대상포진 후 신경통", "Postherpetic neuralgia", "감염"),
        ("B08.1", "전염성 연속종", "Molluscum contagiosum", "감염"),
        ("B25.9", "거대세포바이러스 질환", "Cytomegaloviral disease", "감염"),
        ("B35.0", "수염 및 두부 백선", "Tinea barbae and tinea capitis", "감염"),
        ("B35.3", "발 백선(무좀)", "Tinea pedis", "감염"),
        ("B37.3", "칸디다 외음부질염", "Candidal vulvovaginitis", "감염"),
        ("B44.0", "침습성 폐 아스페르길루스증", "Invasive pulmonary aspergillosis", "감염"),
        # === 호흡기 세부 (41→55) ===
        ("J09", "조류 인플루엔자", "Avian influenza", "호흡기"),
        ("J13", "폐렴구균 폐렴", "Pneumonia due to Streptococcus pneumoniae", "호흡기"),
        ("J14", "인플루엔자균 폐렴", "Pneumonia due to Haemophilus influenzae", "호흡기"),
        ("J15.0", "폐렴간균 폐렴", "Pneumonia due to Klebsiella pneumoniae", "호흡기"),
        ("J15.1", "녹농균 폐렴", "Pneumonia due to Pseudomonas", "호흡기"),
        ("J15.2", "포도상구균 폐렴", "Pneumonia due to Staphylococcus", "호흡기"),
        ("J17.0", "세균질환에서의 폐렴", "Pneumonia in bacterial diseases classified elsewhere", "호흡기"),
        ("J22", "급성 하기도감염, 상세불명", "Acute lower respiratory infection, unspecified", "호흡기"),
        ("J38.0", "성대 마비", "Paralysis of vocal cords", "호흡기"),
        ("J43.1", "범소엽성 폐기종", "Panlobular emphysema", "호흡기"),
        ("J44.0", "COPD 하기도감염 동반", "COPD with acute lower respiratory infection", "호흡기"),
        ("J69.0", "흡인성 폐렴", "Aspiration pneumonia", "호흡기"),
        ("J84.9", "간질성 폐질환, 상세불명", "Interstitial pulmonary disease, unspecified", "호흡기"),
        ("J95.0", "기관절개구 기능장애", "Tracheostomy malfunction", "호흡기"),
        # === 외상 세부 (53→70) ===
        ("S14.1", "경추 척수 손상", "Injuries to cervical spinal cord", "외상"),
        ("S24.1", "흉추 척수 손상", "Injuries to thoracic spinal cord", "외상"),
        ("S34.1", "요추 척수 손상", "Injury of lumbar spinal cord", "외상"),
        ("S36.0", "비장의 손상", "Injury of spleen", "외상"),
        ("S36.1", "간의 손상", "Injury of liver", "외상"),
        ("S36.3", "위의 손상", "Injury of stomach", "외상"),
        ("S37.0", "신장의 손상", "Injury of kidney", "외상"),
        ("S37.2", "방광의 손상", "Injury of bladder", "외상"),
        ("S39.0", "복부 근육 및 건의 손상", "Injury of muscle and tendon of abdomen", "외상"),
        ("S41.0", "어깨의 열린 상처", "Open wound of shoulder", "외상"),
        ("S51.0", "전완부의 열린 상처", "Open wound of forearm", "외상"),
        ("S61.0", "손가락의 열린 상처", "Open wound of finger", "외상"),
        ("S71.0", "고관절부의 열린 상처", "Open wound of hip", "외상"),
        ("S81.0", "무릎의 열린 상처", "Open wound of knee", "외상"),
        ("S91.1", "발가락의 열린 상처", "Open wound of toe", "외상"),
        ("T14.9", "손상, 상세불명", "Injury, unspecified", "외상"),
        ("T39.1", "4-아미노페놀유도체 중독(아세트아미노펜)", "Poisoning by acetaminophen", "외상"),
        # === 산부인과 세부 추가 ===
        ("O10.0", "임신에 의해 악화된 고혈압", "Pre-existing hypertension complicating pregnancy", "산부인과"),
        ("O20.0", "절박유산", "Threatened abortion", "산부인과"),
        ("O21.0", "임신오조(임신구토)", "Mild hyperemesis gravidarum", "산부인과"),
        ("O26.8", "임신 관련 기타 상태", "Other specified pregnancy-related conditions", "산부인과"),
        ("O34.2", "이전 제왕절개 흉터에 의한 관리", "Maternal care due to uterine scar from previous surgery", "산부인과"),
        ("O36.4", "태아 사망에 대한 관리", "Maternal care for intrauterine death", "산부인과"),
        ("O44.1", "전치태반 출혈 동반", "Placenta praevia with haemorrhage", "산부인과"),
        ("O45.9", "태반조기박리", "Premature separation of placenta", "산부인과"),
        ("O46.9", "산전출혈", "Antepartum haemorrhage", "산부인과"),
        ("O60.0", "조기진통", "Preterm labour", "산부인과"),
        ("O62.2", "기타 자궁 무력증", "Other uterine inertia", "산부인과"),
        ("O68.0", "태아 곤란에 의한 분만", "Labour complicated by fetal stress", "산부인과"),
        ("O69.0", "탯줄 탈출에 의한 분만 합병", "Labour complicated by prolapse of cord", "산부인과"),
        ("O72.1", "기타 분만후 출혈", "Other immediate postpartum haemorrhage", "산부인과"),
        ("O85", "산욕 패혈증", "Puerperal sepsis", "산부인과"),
        ("O90.4", "분만후 급성 신부전", "Postpartum acute kidney failure", "산부인과"),
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
    return added, total


if __name__ == "__main__":
    print("=" * 50)
    print("KCD 5차 확충 (809→1000)")
    print("=" * 50)
    added, total = expand_kcd()
    if total >= 1000:
        print("*** 목표 1000건 달성! ***")
    else:
        print(f"목표까지 {1000 - total}건 남음")
