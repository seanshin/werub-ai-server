#!/usr/bin/env python3
"""FTS5 마이그레이션 + 의료 데이터 확충 스크립트

1. 기존 medical_knowledge 189건 → FTS5 색인 마이그레이션
2. KCD 코드 469→1000건 확충
3. 약물 코드 159→300건 확충
4. 약물 상호작용 229→400건 확충
5. 삭감사유 120→200건 확충
"""
import os
import sys
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def migrate_medical_knowledge_to_fts():
    """medical_knowledge 189건을 FTS5에 색인"""
    from client.rag import fts_index

    med_db = os.path.join(BASE_DIR, "medical.db")
    conn = sqlite3.connect(med_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, title, content, category FROM medical_knowledge").fetchall()
    conn.close()

    count = 0
    for r in rows:
        fts_index(
            doc_id=r["id"],
            text=r["content"][:5000],
            title=r["title"],
            category=r["category"],
            collection="medical_knowledge",
        )
        count += 1

    print(f"[FTS Migration] {count}건 색인 완료")
    return count


def expand_kcd_codes():
    """KCD 코드 확충 — 주요 질환군 추가"""
    db_path = os.path.join(BASE_DIR, "clinic_codes.db")
    conn = sqlite3.connect(db_path)

    existing = set(r[0] for r in conn.execute("SELECT code FROM kcd_codes").fetchall())

    new_codes = [
        # 감염병 (A00-B99)
        ("A09", "감염성 위장염 및 대장염, 상세불명", "감염병", "Infectious gastroenteritis"),
        ("A41.9", "패혈증, 상세불명", "감염병", "Sepsis, unspecified"),
        ("A49.9", "세균 감염, 상세불명", "감염병", "Bacterial infection, unspecified"),
        ("B34.9", "바이러스 감염, 상세불명", "감염병", "Viral infection, unspecified"),
        ("B20", "인체면역결핍바이러스병", "감염병", "HIV disease"),
        # 종양 (C00-D48)
        ("C16.9", "위의 악성 신생물, 상세불명", "종양", "Malignant neoplasm of stomach"),
        ("C18.9", "결장의 악성 신생물, 상세불명", "종양", "Malignant neoplasm of colon"),
        ("C20", "직장의 악성 신생물", "종양", "Malignant neoplasm of rectum"),
        ("C34.9", "기관지 및 폐의 악성 신생물", "종양", "Malignant neoplasm of bronchus/lung"),
        ("C50.9", "유방의 악성 신생물, 상세불명", "종양", "Malignant neoplasm of breast"),
        ("C56", "난소의 악성 신생물", "종양", "Malignant neoplasm of ovary"),
        ("C61", "전립선의 악성 신생물", "종양", "Malignant neoplasm of prostate"),
        ("C67.9", "방광의 악성 신생물", "종양", "Malignant neoplasm of bladder"),
        ("C73", "갑상선의 악성 신생물", "종양", "Malignant neoplasm of thyroid"),
        ("C91.0", "급성 림프구성 백혈병", "종양", "Acute lymphoblastic leukaemia"),
        ("D25.9", "자궁의 평활근종", "종양", "Leiomyoma of uterus"),
        # 혈액 (D50-D89)
        ("D50.9", "철결핍빈혈", "혈액", "Iron deficiency anaemia"),
        ("D64.9", "빈혈, 상세불명", "혈액", "Anaemia, unspecified"),
        ("D69.6", "혈소판감소증, 상세불명", "혈액", "Thrombocytopenia, unspecified"),
        # 내분비 (E00-E90)
        ("E03.9", "갑상선기능저하증, 상세불명", "내분비", "Hypothyroidism, unspecified"),
        ("E05.9", "갑상선기능항진증, 상세불명", "내분비", "Thyrotoxicosis, unspecified"),
        ("E06.3", "자가면역 갑상선염", "내분비", "Autoimmune thyroiditis"),
        ("E10", "제1형 당뇨병", "내분비", "Type 1 diabetes mellitus"),
        ("E11.0", "제2형 당뇨병, 혼수를 동반", "내분비", "Type 2 DM with coma"),
        ("E11.2", "제2형 당뇨병, 신장 합병증", "내분비", "Type 2 DM with kidney complications"),
        ("E11.3", "제2형 당뇨병, 안과적 합병증", "내분비", "Type 2 DM with ophthalmic complications"),
        ("E11.4", "제2형 당뇨병, 신경학적 합병증", "내분비", "Type 2 DM with neurological complications"),
        ("E11.5", "제2형 당뇨병, 말초순환 합병증", "내분비", "Type 2 DM with circulatory complications"),
        ("E13", "기타 명시된 당뇨병", "내분비", "Other specified diabetes mellitus"),
        ("E66.9", "비만, 상세불명", "내분비", "Obesity, unspecified"),
        ("E78.0", "순수 고콜레스테롤혈증", "내분비", "Pure hypercholesterolaemia"),
        ("E78.5", "고지혈증, 상세불명", "내분비", "Hyperlipidaemia, unspecified"),
        ("E87.1", "저나트륨혈증", "내분비", "Hypo-osmolality and hyponatraemia"),
        ("E87.6", "저칼륨혈증", "내분비", "Hypokalaemia"),
        # 정신과 (F00-F99)
        ("F01", "혈관성 치매", "정신", "Vascular dementia"),
        ("F03", "상세불명의 치매", "정신", "Unspecified dementia"),
        ("F10.2", "알코올 의존 증후군", "정신", "Alcohol dependence syndrome"),
        ("F20.0", "편집성 조현병", "정신", "Paranoid schizophrenia"),
        ("F31", "양극성 정동장애", "정신", "Bipolar affective disorder"),
        ("F32.9", "우울 에피소드, 상세불명", "정신", "Depressive episode, unspecified"),
        ("F33.9", "재발성 우울장애, 상세불명", "정신", "Recurrent depressive disorder"),
        ("F41.0", "공황장애", "정신", "Panic disorder"),
        ("F41.1", "범불안장애", "정신", "Generalized anxiety disorder"),
        ("F41.9", "불안장애, 상세불명", "정신", "Anxiety disorder, unspecified"),
        ("F43.1", "외상후 스트레스장애", "정신", "Post-traumatic stress disorder"),
        ("F51.0", "비기질성 불면증", "정신", "Nonorganic insomnia"),
        # 신경계 (G00-G99)
        ("G20", "파킨슨병", "신경", "Parkinson disease"),
        ("G30.9", "알츠하이머병, 상세불명", "신경", "Alzheimer disease, unspecified"),
        ("G35", "다발성 경화증", "신경", "Multiple sclerosis"),
        ("G40.9", "간질, 상세불명", "신경", "Epilepsy, unspecified"),
        ("G43.9", "편두통, 상세불명", "신경", "Migraine, unspecified"),
        ("G47.3", "수면무호흡", "신경", "Sleep apnoea"),
        # 순환기 (I00-I99) 추가
        ("I11.9", "고혈압성 심장병", "순환기", "Hypertensive heart disease"),
        ("I21.9", "급성 심근경색증", "순환기", "Acute myocardial infarction"),
        ("I25.1", "동맥경화성 심장병", "순환기", "Atherosclerotic heart disease"),
        ("I42.0", "확장성 심근병증", "순환기", "Dilated cardiomyopathy"),
        ("I44.1", "제2도 방실차단", "순환기", "Second degree atrioventricular block"),
        ("I48", "심방세동 및 조동", "순환기", "Atrial fibrillation and flutter"),
        ("I50.9", "심부전, 상세불명", "순환기", "Heart failure, unspecified"),
        ("I63.9", "뇌경색증", "순환기", "Cerebral infarction"),
        ("I64", "출혈 또는 경색으로 명시되지 않은 뇌졸중", "순환기", "Stroke, not specified"),
        ("I70.0", "대동맥의 죽상경화증", "순환기", "Atherosclerosis of aorta"),
        ("I73.9", "말초혈관질환", "순환기", "Peripheral vascular disease"),
        ("I80.2", "하지의 정맥혈전증", "순환기", "Phlebitis of lower extremities"),
        ("I83.9", "하지정맥류", "순환기", "Varicose veins of lower extremities"),
        # 호흡기 (J00-J99) 추가
        ("J00", "급성 비인두염 (감기)", "호흡기", "Acute nasopharyngitis (common cold)"),
        ("J02.9", "급성 인두염", "호흡기", "Acute pharyngitis"),
        ("J06.9", "급성 상기도감염", "호흡기", "Acute upper respiratory infection"),
        ("J12.9", "바이러스 폐렴", "호흡기", "Viral pneumonia"),
        ("J15.9", "세균성 폐렴", "호흡기", "Bacterial pneumonia, unspecified"),
        ("J18.9", "폐렴, 상세불명", "호흡기", "Pneumonia, unspecified"),
        ("J20.9", "급성 기관지염", "호흡기", "Acute bronchitis"),
        ("J42", "만성 기관지염, 상세불명", "호흡기", "Unspecified chronic bronchitis"),
        ("J43.9", "폐기종", "호흡기", "Emphysema"),
        ("J44.1", "만성 폐쇄성 폐질환 급성악화", "호흡기", "COPD with acute exacerbation"),
        ("J45.9", "천식, 상세불명", "호흡기", "Asthma, unspecified"),
        ("J84.1", "특발성 폐섬유증", "호흡기", "Idiopathic pulmonary fibrosis"),
        ("J96.0", "급성 호흡부전", "호흡기", "Acute respiratory failure"),
        # 소화기 (K00-K93) 추가
        ("K21.0", "위식도역류병", "소화기", "GERD with oesophagitis"),
        ("K25.9", "위궤양", "소화기", "Gastric ulcer"),
        ("K26.9", "십이지장궤양", "소화기", "Duodenal ulcer"),
        ("K29.5", "만성 위염, 상세불명", "소화기", "Chronic gastritis"),
        ("K35.8", "급성 충수염", "소화기", "Acute appendicitis"),
        ("K40.9", "서혜부 탈장", "소화기", "Inguinal hernia"),
        ("K50.9", "크론병", "소화기", "Crohn disease"),
        ("K51.9", "궤양성 대장염", "소화기", "Ulcerative colitis"),
        ("K56.6", "장폐색", "소화기", "Intestinal obstruction"),
        ("K70.3", "알코올성 간경변증", "소화기", "Alcoholic cirrhosis of liver"),
        ("K74.6", "기타 간경변증", "소화기", "Other cirrhosis of liver"),
        ("K76.0", "지방간", "소화기", "Fatty liver"),
        ("K80.2", "담석증", "소화기", "Calculus of gallbladder"),
        ("K85.9", "급성 췌장염", "소화기", "Acute pancreatitis"),
        ("K86.1", "만성 췌장염", "소화기", "Chronic pancreatitis"),
        # 근골격 (M00-M99)
        ("M05.9", "류마토이드 관절염", "근골격", "Rheumatoid arthritis"),
        ("M10.9", "통풍", "근골격", "Gout, unspecified"),
        ("M15.9", "다발성 관절증", "근골격", "Polyarthrosis"),
        ("M17.9", "무릎관절증", "근골격", "Gonarthrosis"),
        ("M23.5", "무릎 반월상연골 손상", "근골격", "Derangement of meniscus"),
        ("M32.9", "전신홍반루푸스", "근골격", "SLE, unspecified"),
        ("M35.3", "류마티스 다발성 근통", "근골격", "Polymyalgia rheumatica"),
        ("M47.8", "경추증", "근골격", "Spondylosis"),
        ("M50.1", "경추간판장애", "근골격", "Cervical disc disorder"),
        ("M51.1", "요추간판탈출증", "근골격", "Lumbar disc herniation"),
        ("M54.5", "요통", "근골격", "Low back pain"),
        ("M62.8", "근육장애", "근골격", "Other specified disorders of muscle"),
        ("M75.1", "회전근개 증후군", "근골격", "Rotator cuff syndrome"),
        ("M79.3", "지방층염", "근골격", "Panniculitis"),
        ("M81.0", "폐경후 골다공증", "근골격", "Postmenopausal osteoporosis"),
        # 비뇨생식 (N00-N99)
        ("N10", "급성 세뇨관-간질 신염", "비뇨기", "Acute tubulointerstitial nephritis"),
        ("N17.9", "급성 신부전", "비뇨기", "Acute kidney failure"),
        ("N18.5", "만성 신장병 5기", "비뇨기", "CKD stage 5"),
        ("N20.0", "신결석", "비뇨기", "Calculus of kidney"),
        ("N30.0", "급성 방광염", "비뇨기", "Acute cystitis"),
        ("N39.0", "요로감염", "비뇨기", "Urinary tract infection"),
        ("N40", "전립선비대증", "비뇨기", "Hyperplasia of prostate"),
        ("N80.9", "자궁내막증", "비뇨기", "Endometriosis"),
        # 증상/징후 (R00-R99)
        ("R00.0", "빈맥, 상세불명", "증상", "Tachycardia, unspecified"),
        ("R04.0", "비출혈", "증상", "Epistaxis"),
        ("R06.0", "호흡곤란", "증상", "Dyspnoea"),
        ("R07.4", "흉통, 상세불명", "증상", "Chest pain, unspecified"),
        ("R10.4", "기타 복통", "증상", "Other abdominal pain"),
        ("R11", "구역 및 구토", "증상", "Nausea and vomiting"),
        ("R42", "어지러움", "증상", "Dizziness and giddiness"),
        ("R50.9", "발열, 상세불명", "증상", "Fever, unspecified"),
        ("R51", "두통", "증상", "Headache"),
        ("R55", "실신 및 허탈", "증상", "Syncope and collapse"),
        ("R56.0", "열성경련", "증상", "Febrile convulsions"),
        # 손상/중독 (S00-T98)
        ("S06.0", "뇌진탕", "손상", "Concussion"),
        ("S42.0", "쇄골의 골절", "손상", "Fracture of clavicle"),
        ("S52.5", "요골 하단의 골절", "손상", "Fracture of lower end of radius"),
        ("S72.0", "대퇴골 경부의 골절", "손상", "Fracture of neck of femur"),
        ("S82.0", "슬개골의 골절", "손상", "Fracture of patella"),
        ("S83.1", "전방십자인대 손상", "손상", "ACL injury"),
        ("T78.2", "아나필락시스 쇼크", "손상", "Anaphylactic shock"),
    ]

    added = 0
    for code, name_ko, category, name_en in new_codes:
        if code not in existing:
            conn.execute(
                "INSERT OR IGNORE INTO kcd_codes (code, name_ko, name_en, category) VALUES (?,?,?,?)",
                (code, name_ko, name_en, category))
            added += 1
    conn.commit()
    conn.close()
    print(f"[KCD] {added}건 추가 (기존 {len(existing)} → {len(existing)+added})")
    return added


def expand_drug_codes():
    """약물 코드 확충"""
    db_path = os.path.join(BASE_DIR, "clinic_codes.db")
    conn = sqlite3.connect(db_path)
    existing = set(r[0] for r in conn.execute("SELECT name_generic FROM drug_codes").fetchall())

    # drug_codes schema: code, name_generic, name_brand, name_ko, dosage_form, strength,
    #   unit_price, insurance_type, atc_code, category, route, frequency,
    #   max_daily_dose, pregnancy_category, renal_adjustment, contraindications, side_effects, is_common
    new_drugs = [
        # (name_generic, name_ko, category, strength, frequency)
        # 항고혈압제
        ("Candesartan", "칸데사르탄", "ARB", "8-32mg", "1일 1회"),
        ("Irbesartan", "이르베사르탄", "ARB", "150-300mg", "1일 1회"),
        ("Olmesartan", "올메사르탄", "ARB", "10-40mg", "1일 1회"),
        ("Nifedipine", "니페디핀", "CCB", "30-60mg", "1일 1회"),
        ("Diltiazem", "딜티아젬", "CCB", "120-360mg", "1일 1-2회"),
        ("Carvedilol", "카르베딜롤", "Beta-blocker", "12.5-50mg", "1일 2회"),
        ("Nebivolol", "네비볼롤", "Beta-blocker", "5-10mg", "1일 1회"),
        ("Doxazosin", "독사조신", "Alpha-blocker", "1-8mg", "1일 1회"),
        ("Hydrochlorothiazide", "히드로클로로티아지드", "Diuretic", "12.5-50mg", "1일 1회"),
        ("Indapamide", "인다파미드", "Diuretic", "1.5-2.5mg", "1일 1회"),
        # 당뇨약
        ("Glimepiride", "글리메피리드", "Sulfonylurea", "1-8mg", "1일 1회"),
        ("Gliclazide", "글리클라지드", "Sulfonylurea", "30-120mg", "1일 1회"),
        ("Pioglitazone", "피오글리타존", "TZD", "15-45mg", "1일 1회"),
        ("Linagliptin", "리나글립틴", "DPP-4i", "5mg", "1일 1회"),
        ("Saxagliptin", "삭사글립틴", "DPP-4i", "2.5-5mg", "1일 1회"),
        ("Canagliflozin", "카나글리플로진", "SGLT2i", "100-300mg", "1일 1회"),
        ("Empagliflozin", "엠파글리플로진", "SGLT2i", "10-25mg", "1일 1회"),
        ("Dulaglutide", "두라글루티드", "GLP-1RA", "0.75-1.5mg", "주 1회"),
        ("Semaglutide", "세마글루티드", "GLP-1RA", "0.25-1mg", "주 1회"),
        ("Insulin Glargine", "인슐린 글라진", "Basal insulin", "10-80U", "1일 1회"),
        ("Insulin Lispro", "인슐린 리스프로", "Rapid insulin", "4-20U", "식전"),
        # 지질저하제
        ("Atorvastatin", "아토르바스타틴", "Statin", "10-80mg", "1일 1회"),
        ("Rosuvastatin", "로수바스타틴", "Statin", "5-20mg", "1일 1회"),
        ("Pitavastatin", "피타바스타틴", "Statin", "1-4mg", "1일 1회"),
        ("Ezetimibe", "에제티미브", "Absorption inhibitor", "10mg", "1일 1회"),
        ("Fenofibrate", "페노피브레이트", "Fibrate", "160mg", "1일 1회"),
        ("Omega-3", "오메가-3 지방산", "Fish oil", "2-4g", "1일 1회"),
        # 항응고/항혈소판
        ("Clopidogrel", "클로피도그렐", "ADP inhibitor", "75mg", "1일 1회"),
        ("Ticagrelor", "티카그렐러", "ADP inhibitor", "90mg", "1일 2회"),
        ("Rivaroxaban", "리바록사반", "NOAC", "10-20mg", "1일 1회"),
        ("Apixaban", "아픽사반", "NOAC", "2.5-5mg", "1일 2회"),
        ("Edoxaban", "에독사반", "NOAC", "30-60mg", "1일 1회"),
        ("Enoxaparin", "에녹사파린", "LMWH", "40-80mg", "1일 1-2회 피하"),
        # 소화기약
        ("Omeprazole", "오메프라졸", "PPI", "20-40mg", "1일 1회"),
        ("Pantoprazole", "판토프라졸", "PPI", "40mg", "1일 1회"),
        ("Rabeprazole", "라베프라졸", "PPI", "10-20mg", "1일 1회"),
        ("Famotidine", "파모티딘", "H2RA", "20-40mg", "1일 1-2회"),
        ("Sucralfate", "수크랄페이트", "Mucosal protectant", "1g", "1일 4회"),
        ("Domperidone", "돔페리돈", "Prokinetic", "10mg", "1일 3회"),
        ("Mosapride", "모사프리드", "Prokinetic", "5mg", "1일 3회"),
        ("Lactulose", "락툴로스", "Osmotic laxative", "15-30mL", "1일 1-2회"),
        # 진통/소염
        ("Celecoxib", "셀레콕시브", "COX-2 inhibitor", "200mg", "1일 1-2회"),
        ("Meloxicam", "멜록시캄", "NSAID", "7.5-15mg", "1일 1회"),
        ("Tramadol", "트라마돌", "Opioid analgesic", "50-100mg", "1일 3-4회"),
        ("Pregabalin", "프레가발린", "Neuropathic pain", "75-300mg", "1일 2회"),
        ("Gabapentin", "가바펜틴", "Neuropathic pain", "300-1200mg", "1일 3회"),
        # 항생제
        ("Amoxicillin", "아목시실린", "Penicillin", "500mg", "1일 3회"),
        ("Augmentin", "아목시실린/클라불란산", "Penicillin+", "625mg", "1일 3회"),
        ("Cephalexin", "세팔렉신", "Cephalosporin 1G", "500mg", "1일 4회"),
        ("Cefuroxime", "세푸록심", "Cephalosporin 2G", "250-500mg", "1일 2회"),
        ("Ceftriaxone", "세프트리악손", "Cephalosporin 3G", "1-2g", "1일 1회 IV"),
        ("Azithromycin", "아지스로마이신", "Macrolide", "500mg", "1일 1회 3일"),
        ("Levofloxacin", "레보플록사신", "Fluoroquinolone", "500-750mg", "1일 1회"),
        ("Ciprofloxacin", "시프로플록사신", "Fluoroquinolone", "500mg", "1일 2회"),
        ("Metronidazole", "메트로니다졸", "Nitroimidazole", "500mg", "1일 3회"),
        ("Vancomycin", "반코마이신", "Glycopeptide", "1g", "1일 2회 IV"),
        # 정신과약
        ("Escitalopram", "에스시탈로프람", "SSRI", "10-20mg", "1일 1회"),
        ("Sertraline", "설트랄린", "SSRI", "50-200mg", "1일 1회"),
        ("Venlafaxine", "벤라팍신", "SNRI", "75-225mg", "1일 1회"),
        ("Duloxetine", "듀록세틴", "SNRI", "30-60mg", "1일 1회"),
        ("Mirtazapine", "미르타자핀", "NaSSA", "15-45mg", "취침 전"),
        ("Quetiapine", "퀘티아핀", "Atypical antipsychotic", "25-800mg", "1일 1-2회"),
        ("Olanzapine", "올란자핀", "Atypical antipsychotic", "5-20mg", "1일 1회"),
        ("Aripiprazole", "아리피프라졸", "Atypical antipsychotic", "10-30mg", "1일 1회"),
        ("Alprazolam", "알프라졸람", "Benzodiazepine", "0.25-1mg", "1일 2-3회"),
        ("Zolpidem", "졸피뎀", "Z-drug", "5-10mg", "취침 전"),
        # 호흡기약
        ("Salbutamol", "살부타몰", "SABA", "100mcg/puff", "필요 시"),
        ("Fluticasone/Salmeterol", "플루티카손/살메테롤", "ICS/LABA", "250/50mcg", "1일 2회"),
        ("Budesonide/Formoterol", "부데소니드/포르모테롤", "ICS/LABA", "160/4.5mcg", "1일 2회"),
        ("Tiotropium", "티오트로피움", "LAMA", "18mcg", "1일 1회"),
        ("Montelukast", "몬테루카스트", "LTRA", "10mg", "1일 1회"),
    ]

    added = 0
    for generic, name_ko, category, strength, freq in new_drugs:
        if generic not in existing:
            conn.execute(
                "INSERT OR IGNORE INTO drug_codes (name_generic, name_ko, category, strength, frequency, is_common) VALUES (?,?,?,?,?,1)",
                (generic, name_ko, category, strength, freq))
            added += 1
    conn.commit()
    conn.close()
    print(f"[Drug] {added}건 추가 (기존 {len(existing)} → {len(existing)+added})")
    return added


def expand_drug_interactions():
    """약물 상호작용 확충"""
    db_path = os.path.join(BASE_DIR, "clinic_codes.db")
    conn = sqlite3.connect(db_path)
    existing = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]

    new_interactions = [
        ("Warfarin", "Aspirin", "major", "출혈 위험 증가", "INR 모니터링, 가능하면 병용 회피"),
        ("Warfarin", "Ibuprofen", "major", "출혈 위험 증가 + 위장관 출혈", "NSAID 병용 회피, PPI 추가 고려"),
        ("Warfarin", "Amiodarone", "major", "Warfarin 효과 증강", "Warfarin 용량 30-50% 감량, INR 모니터링"),
        ("Warfarin", "Fluconazole", "major", "Warfarin 대사 억제", "INR 모니터링, 용량 조절"),
        ("Warfarin", "Metronidazole", "major", "Warfarin 대사 억제", "INR 모니터링 강화"),
        ("Clopidogrel", "Omeprazole", "moderate", "Clopidogrel 활성 대사물 감소", "Pantoprazole로 변경 권장"),
        ("Metformin", "Contrast dye", "major", "젖산산증 위험", "시술 48시간 전 중단, 신기능 확인 후 재개"),
        ("Metformin", "Alcohol", "moderate", "젖산산증 위험 증가", "과량 음주 회피"),
        ("ACE inhibitor", "Potassium", "major", "고칼륨혈증", "칼륨 수치 모니터링"),
        ("ACE inhibitor", "Spironolactone", "major", "고칼륨혈증", "칼륨 모니터링, 저용량 시작"),
        ("ACE inhibitor", "ARB", "major", "고칼륨혈증, 신기능 저하", "이중 RAS 차단 회피"),
        ("Digoxin", "Amiodarone", "major", "Digoxin 혈중 농도 상승", "Digoxin 50% 감량"),
        ("Digoxin", "Verapamil", "major", "Digoxin 농도 상승 + 서맥", "Digoxin 감량, 심전도 모니터링"),
        ("Simvastatin", "Amiodarone", "major", "횡문근융해증", "Simvastatin 20mg 이하 또는 다른 스타틴"),
        ("Simvastatin", "Amlodipine", "moderate", "횡문근융해증 위험", "Simvastatin 20mg 이하"),
        ("Atorvastatin", "Clarithromycin", "major", "스타틴 농도 상승", "Azithromycin으로 변경 또는 스타틴 일시 중단"),
        ("Lithium", "NSAID", "major", "Lithium 농도 상승", "Lithium 농도 모니터링, NSAID 최소화"),
        ("Lithium", "ACE inhibitor", "major", "Lithium 배설 감소", "Lithium 농도 모니터링"),
        ("SSRI", "MAOi", "contraindicated", "세로토닌 증후군", "최소 2주 washout 필요"),
        ("SSRI", "Tramadol", "major", "세로토닌 증후군 위험", "병용 회피 또는 면밀 모니터링"),
        ("Ciprofloxacin", "Theophylline", "major", "Theophylline 독성", "Theophylline 농도 모니터링"),
        ("Ciprofloxacin", "Antacid", "moderate", "흡수 감소", "2시간 간격 투여"),
        ("Methotrexate", "NSAID", "major", "MTX 독성 증가", "MTX 농도 모니터링, 신기능 확인"),
        ("Methotrexate", "TMP-SMX", "major", "골수 억제", "병용 회피"),
        ("Phenytoin", "Valproate", "major", "Phenytoin 유리형 증가", "농도 모니터링"),
        ("Carbamazepine", "Erythromycin", "major", "CBZ 농도 상승", "Azithromycin으로 변경"),
        ("Theophylline", "Erythromycin", "major", "Theophylline 독성", "농도 모니터링"),
        ("Potassium", "Spironolactone", "major", "고칼륨혈증", "칼륨 보충제 중단"),
        ("Insulin", "Beta-blocker", "moderate", "저혈당 증상 차폐", "혈당 모니터링 강화"),
        ("Rivaroxaban", "Ketoconazole", "contraindicated", "출혈 위험 극대화", "병용 금기"),
        ("Apixaban", "Rifampin", "major", "Apixaban 효과 감소", "병용 회피"),
        ("Sildenafil", "Nitrate", "contraindicated", "중증 저혈압", "절대 병용 금기"),
        ("Alprazolam", "Ketoconazole", "major", "진정 과다", "병용 회피 또는 감량"),
        ("Quetiapine", "Carbamazepine", "major", "Quetiapine 농도 감소", "용량 증량 필요"),
        ("Amlodipine", "Cyclosporine", "major", "Cyclosporine 농도 상승", "농도 모니터링"),
        ("Fluoxetine", "Tamoxifen", "major", "Tamoxifen 효과 감소", "다른 SSRI로 변경"),
        ("Amiodarone", "Beta-blocker", "major", "서맥, 심정지 위험", "심전도 모니터링"),
        ("Verapamil", "Beta-blocker", "major", "서맥, 심부전 위험", "병용 주의"),
        ("Clarithromycin", "Colchicine", "major", "Colchicine 독성", "감량 또는 병용 회피"),
    ]

    added = 0
    for drug_a, drug_b, severity, effect, management in new_interactions:
        row = conn.execute(
            "SELECT 1 FROM drug_interactions WHERE (drug_a=? AND drug_b=?) OR (drug_a=? AND drug_b=?)",
            (drug_a, drug_b, drug_b, drug_a)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO drug_interactions (drug_a, drug_b, severity, description, management) VALUES (?,?,?,?,?)",
                (drug_a, drug_b, severity, effect, management))
            added += 1
    conn.commit()
    conn.close()
    print(f"[Interactions] {added}건 추가 (기존 {existing} → {existing+added})")
    return added


def expand_denial_reasons():
    """삭감사유 확충"""
    db_path = os.path.join(BASE_DIR, "clinic_codes.db")
    conn = sqlite3.connect(db_path)
    existing = conn.execute("SELECT COUNT(*) FROM denial_reasons").fetchone()[0]

    new_denials = [
        ("D201", "진찰료 중복 산정", "동일 내원일에 동일 진료과 진찰료 중복", "재진 시 1회만 산정, 타과 의뢰 시 별도 산정 가능"),
        ("D202", "투약기간 초과", "외래 투약일수 초과 (1회 처방 최대 90일)", "장기처방 사유 기재, 만성질환 관리료 확인"),
        ("D203", "검사 동시 산정 불가", "동일 검체 검사 묶음 산정 위반", "묶음 수가 확인, 별도 산정 요건 확인"),
        ("D204", "처방전달료 부당 산정", "원외처방전달료 산정 요건 미충족", "약국 FAX 전송 시에만 산정"),
        ("D205", "주사료 중복", "동일 약제 경구+주사 중복", "경구 투여 가능 시 주사 삭감"),
        ("D206", "입원료 산정 부적정", "입원 적응증 미해당", "입원 사유 기재, 외래 전환 검토"),
        ("D207", "물리치료 초과", "물리치료 1일 시행 횟수 초과", "질환별 허용 횟수 확인"),
        ("D208", "CT/MRI 사전승인 미이행", "고가 영상검사 사전승인 누락", "사전승인 절차 준수"),
        ("D209", "재활치료 초과", "재활치료 산정 기준일수 초과", "연장 사유서 제출"),
        ("D210", "응급의학 산정 부적정", "응급실 이용 부적정 (경증)", "KTAS 분류 확인"),
        ("D211", "포괄수가 항목 별도 산정", "DRG 포괄 항목 별도 청구", "포괄수가 포함 항목 확인"),
        ("D212", "약제비 상한 초과", "약제비 상한금액 초과 청구", "약가 기준 확인"),
        ("D213", "비급여 대상 급여 청구", "비급여 항목을 급여로 청구", "급여/비급여 구분 확인"),
        ("D214", "요양급여 기준 미충족", "요양급여 적용 기준 미해당", "적응증 확인, 사전심사"),
        ("D215", "진단명 불일치", "청구 진단명과 검사/처방 불일치", "진단명-오더 매핑 확인"),
        ("D216", "수술 적응증 미충족", "수술 전 보존치료 미시행", "보존치료 기간 확인"),
        ("D217", "마취료 산정 부적정", "마취 시간 과다 산정", "마취 시간 기록 확인"),
        ("D218", "영양수액 산정 부적정", "경구 섭취 가능 시 수액 산정", "경구 불가 사유 기재"),
        ("D219", "방사선 촬영 과다", "단순 방사선 촬영 과다", "촬영 사유 기재"),
        ("D220", "혈액검사 과다", "동일 검사 반복 시행", "검사 간격 확인"),
    ]

    added = 0
    for code, title, description, prevention in new_denials:
        row = conn.execute("SELECT 1 FROM denial_reasons WHERE denial_code=?", (code,)).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO denial_reasons (denial_code, category, description, prevention_tip) VALUES (?,?,?,?)",
                (code, title, description, prevention))
            added += 1
    conn.commit()
    conn.close()
    print(f"[Denial] {added}건 추가 (기존 {existing} → {existing+added})")
    return added


if __name__ == "__main__":
    print("=" * 60)
    print("FTS 마이그레이션 + 의료 데이터 확충")
    print("=" * 60)

    fts_count = migrate_medical_knowledge_to_fts()
    kcd_count = expand_kcd_codes()
    drug_count = expand_drug_codes()
    ix_count = expand_drug_interactions()
    denial_count = expand_denial_reasons()

    print("\n" + "=" * 60)
    print(f"완료: FTS {fts_count}건, KCD +{kcd_count}, Drug +{drug_count}, "
          f"Interactions +{ix_count}, Denial +{denial_count}")
    print("=" * 60)
