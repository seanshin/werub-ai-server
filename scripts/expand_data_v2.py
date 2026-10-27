#!/usr/bin/env python3
"""의료 데이터 대량 확충 2차 — KCD 506→1000, Drug 177→300, Interactions 236→400

부족 영역 중심 보강:
  - KCD: 신경/안과/이비인후/혈액/피부/선천/신생아/정신 보강
  - Drug: 주요 처방 약물 추가 (카테고리 통합)
  - Interactions: 고빈도 상호작용 추가
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
        # 신경 (G) — 13→35
        ("G10", "헌팅턴병", "Huntington disease", "신경"),
        ("G12.2", "운동뉴런병", "Motor neuron disease", "신경"),
        ("G20", "파킨슨병", "Parkinson disease", "신경"),
        ("G24.9", "근긴장이상", "Dystonia, unspecified", "신경"),
        ("G25.0", "본태성 진전", "Essential tremor", "신경"),
        ("G35", "다발성경화증", "Multiple sclerosis", "신경"),
        ("G37.3", "횡단성 척수염", "Transverse myelitis", "신경"),
        ("G40.0", "특발성 국소 간질", "Localization-related epilepsy", "신경"),
        ("G43.0", "전조가 없는 편두통", "Migraine without aura", "신경"),
        ("G43.1", "전조가 있는 편두통", "Migraine with aura", "신경"),
        ("G44.2", "긴장성 두통", "Tension-type headache", "신경"),
        ("G47.0", "수면 개시 및 유지 장애", "Insomnia", "신경"),
        ("G47.4", "기면증 및 탈력발작", "Narcolepsy and cataplexy", "신경"),
        ("G50.0", "삼차신경통", "Trigeminal neuralgia", "신경"),
        ("G51.0", "벨마비", "Bell palsy", "신경"),
        ("G56.0", "손목터널증후군", "Carpal tunnel syndrome", "신경"),
        ("G61.0", "길랑-바레 증후군", "Guillain-Barre syndrome", "신경"),
        ("G62.9", "다발성 신경병증", "Polyneuropathy, unspecified", "신경"),
        ("G70.0", "중증근무력증", "Myasthenia gravis", "신경"),
        ("G80.9", "뇌성마비", "Cerebral palsy", "신경"),
        ("G91.9", "수두증", "Hydrocephalus, unspecified", "신경"),
        ("G93.4", "상세불명의 뇌병증", "Encephalopathy, unspecified", "신경"),
        # 안과 (H00-H59) — 10→30
        ("H00.0", "맥립종(다래끼)", "Hordeolum", "안과"),
        ("H01.0", "안검염", "Blepharitis", "안과"),
        ("H04.1", "건성안증후군", "Dry eye syndrome", "안과"),
        ("H10.1", "급성 아토피결막염", "Acute atopic conjunctivitis", "안과"),
        ("H10.4", "만성 결막염", "Chronic conjunctivitis", "안과"),
        ("H16.0", "각막궤양", "Corneal ulcer", "안과"),
        ("H20.0", "급성 홍채모양체염", "Acute iridocyclitis", "안과"),
        ("H25.9", "노인성 백내장", "Senile cataract", "안과"),
        ("H26.9", "백내장, 상세불명", "Cataract, unspecified", "안과"),
        ("H33.0", "망막박리", "Retinal detachment", "안과"),
        ("H34.1", "중심망막동맥폐쇄", "Central retinal artery occlusion", "안과"),
        ("H35.3", "황반변성", "Macular degeneration", "안과"),
        ("H36.0", "당뇨병성 망막병증", "Diabetic retinopathy", "안과"),
        ("H40.1", "원발성 개방각녹내장", "Primary open-angle glaucoma", "안과"),
        ("H40.2", "원발성 폐쇄각녹내장", "Primary angle-closure glaucoma", "안과"),
        ("H43.1", "유리체출혈", "Vitreous haemorrhage", "안과"),
        ("H46", "시신경염", "Optic neuritis", "안과"),
        ("H50.0", "수렴사시", "Convergent strabismus", "안과"),
        ("H52.1", "근시", "Myopia", "안과"),
        ("H52.4", "노안", "Presbyopia", "안과"),
        # 이비인후 (H60-H95) — 7→25
        ("H60.3", "외이도염", "Otitis externa", "이비인후"),
        ("H61.2", "이구전색(귀지)", "Cerumen impaction", "이비인후"),
        ("H65.0", "급성 장액성 중이염", "Acute serous otitis media", "이비인후"),
        ("H65.9", "비화농성 중이염", "Nonsuppurative otitis media", "이비인후"),
        ("H66.9", "화농성 중이염", "Suppurative otitis media", "이비인후"),
        ("H70.0", "급성 유양돌기염", "Acute mastoiditis", "이비인후"),
        ("H72.9", "고막천공", "Perforation of tympanic membrane", "이비인후"),
        ("H81.0", "메니에르병", "Meniere disease", "이비인후"),
        ("H81.1", "양성 발작성 두위현훈", "Benign paroxysmal positional vertigo", "이비인후"),
        ("H81.3", "전정신경염", "Vestibular neuronitis", "이비인후"),
        ("H83.3", "소음성 난청", "Noise-induced hearing loss", "이비인후"),
        ("H90.3", "감각신경성 난청, 양측", "Sensorineural hearing loss, bilateral", "이비인후"),
        ("H91.2", "돌발성 난청", "Sudden idiopathic hearing loss", "이비인후"),
        ("J01.9", "급성 부비동염", "Acute sinusitis", "이비인후"),
        ("J30.1", "알레르기비염(꽃가루)", "Allergic rhinitis due to pollen", "이비인후"),
        ("J32.9", "만성 부비동염", "Chronic sinusitis", "이비인후"),
        ("J34.2", "비중격만곡증", "Deviated nasal septum", "이비인후"),
        ("J35.1", "편도비대", "Hypertrophy of tonsils", "이비인후"),
        # 혈액 (D50-D89) — 4→20
        ("D50.0", "만성 실혈에 의한 철결핍빈혈", "Iron deficiency anaemia secondary to blood loss", "혈액"),
        ("D51.0", "비타민 B12 결핍빈혈", "Vitamin B12 deficiency anaemia", "혈액"),
        ("D52.9", "엽산 결핍빈혈", "Folate deficiency anaemia", "혈액"),
        ("D56.1", "베타 지중해빈혈", "Beta thalassaemia", "혈액"),
        ("D58.0", "유전성 구형적혈구증", "Hereditary spherocytosis", "혈액"),
        ("D59.1", "자가면역 용혈빈혈", "Autoimmune haemolytic anaemia", "혈액"),
        ("D61.9", "재생불량빈혈", "Aplastic anaemia, unspecified", "혈액"),
        ("D65", "파종성 혈관내 응고", "DIC", "혈액"),
        ("D66", "혈우병 A", "Haemophilia A", "혈액"),
        ("D68.4", "후천성 응고 인자 결핍", "Acquired coagulation factor deficiency", "혈액"),
        ("D69.3", "특발성 혈소판감소자반증", "ITP", "혈액"),
        ("D70", "무과립세포증", "Agranulocytosis", "혈액"),
        ("D72.8", "백혈구 증가증", "Leukocytosis", "혈액"),
        ("D73.1", "기능항진 비장", "Hypersplenism", "혈액"),
        ("D80.0", "유전성 저감마글로불린혈증", "Hereditary hypogammaglobulinaemia", "혈액"),
        ("D86.0", "폐 사르코이드증", "Sarcoidosis of lung", "혈액"),
        # 피부 (L00-L99) — 19→35
        ("L01.0", "농가진", "Impetigo", "피부"),
        ("L02.9", "피부 농양/종기", "Cutaneous abscess/furuncle", "피부"),
        ("L03.1", "봉와직염", "Cellulitis", "피부"),
        ("L08.0", "농피증", "Pyoderma", "피부"),
        ("L10.0", "보통천포창", "Pemphigus vulgaris", "피부"),
        ("L13.0", "유사천포창", "Bullous pemphigoid", "피부"),
        ("L21.0", "두부 지루피부염", "Seborrhoeic dermatitis of scalp", "피부"),
        ("L23.9", "알레르기접촉피부염", "Allergic contact dermatitis", "피부"),
        ("L27.0", "약물에 의한 전신 피부발진", "Generalized skin eruption due to drugs", "피부"),
        ("L30.9", "피부염, 상세불명", "Dermatitis, unspecified", "피부"),
        ("L40.0", "심상성 건선", "Psoriasis vulgaris", "피부"),
        ("L50.0", "알레르기 두드러기", "Allergic urticaria", "피부"),
        ("L51.1", "스티븐스-존슨 증후군", "Stevens-Johnson syndrome", "피부"),
        ("L60.0", "조갑내성장(내향성발톱)", "Ingrowing nail", "피부"),
        ("L63.9", "원형탈모증", "Alopecia areata", "피부"),
        ("L70.0", "심상성 여드름", "Acne vulgaris", "피부"),
        # 정신 추가 — 20→35
        ("F00.9", "알츠하이머병에서의 치매", "Dementia in Alzheimer disease", "정신"),
        ("F06.7", "경도 인지장애", "Mild cognitive disorder", "정신"),
        ("F10.1", "알코올의 유해한 사용", "Harmful use of alcohol", "정신"),
        ("F11.2", "아편양 물질 의존 증후군", "Opioid dependence syndrome", "정신"),
        ("F17.2", "니코틴 의존 증후군", "Nicotine dependence", "정신"),
        ("F23.9", "급성 일과성 정신장애", "Acute transient psychotic disorder", "정신"),
        ("F25.9", "분열정동장애", "Schizoaffective disorder", "정신"),
        ("F34.1", "기분저하증(감정부전)", "Dysthymia", "정신"),
        ("F40.1", "사회공포증", "Social phobia", "정신"),
        ("F42.9", "강박장애", "OCD", "정신"),
        ("F43.0", "급성 스트레스 반응", "Acute stress reaction", "정신"),
        ("F44.9", "해리장애", "Dissociative disorder", "정신"),
        ("F45.0", "신체화 장애", "Somatization disorder", "정신"),
        ("F50.0", "신경성 식욕부진증", "Anorexia nervosa", "정신"),
        ("F84.0", "자폐스펙트럼장애", "Autism spectrum disorder", "정신"),
        # 선천/신생아 추가 — 10→20
        ("P07.3", "저체중 출생아", "Low birth weight", "신생아"),
        ("P22.0", "신생아 호흡곤란증후군", "RDS of newborn", "신생아"),
        ("P36.9", "신생아 세균성 패혈증", "Neonatal sepsis", "신생아"),
        ("P59.9", "신생아 황달", "Neonatal jaundice", "신생아"),
        ("P70.4", "신생아 저혈당증", "Neonatal hypoglycaemia", "신생아"),
        ("Q21.0", "심실중격결손", "VSD", "선천"),
        ("Q21.1", "심방중격결손", "ASD", "선천"),
        ("Q25.0", "동맥관개존증", "PDA", "선천"),
        ("Q35.9", "구개파열", "Cleft palate", "선천"),
        ("Q53.9", "정류고환(잠복고환)", "Undescended testis", "선천"),
        ("Q65.0", "선천성 고관절탈구", "Congenital hip dislocation", "선천"),
        ("Q66.0", "선천성 만곡족", "Congenital talipes equinovarus", "선천"),
        ("Q90.9", "다운증후군", "Down syndrome", "선천"),
        # 비뇨기 추가 — 27→40
        ("N11.9", "만성 세뇨관-간질 신염", "Chronic tubulointerstitial nephritis", "비뇨기"),
        ("N18.1", "만성 신장병 1기", "CKD stage 1", "비뇨기"),
        ("N18.2", "만성 신장병 2기", "CKD stage 2", "비뇨기"),
        ("N18.3", "만성 신장병 3기", "CKD stage 3", "비뇨기"),
        ("N18.4", "만성 신장병 4기", "CKD stage 4", "비뇨기"),
        ("N20.1", "요관결석", "Calculus of ureter", "비뇨기"),
        ("N21.0", "방광결석", "Calculus of bladder", "비뇨기"),
        ("N41.0", "급성 전립선염", "Acute prostatitis", "비뇨기"),
        ("N41.1", "만성 전립선염", "Chronic prostatitis", "비뇨기"),
        ("N45.9", "고환염 및 부고환염", "Orchitis and epididymitis", "비뇨기"),
        ("N60.1", "유방낭종", "Diffuse cystic mastopathy", "비뇨기"),
        ("N76.0", "급성 질염", "Acute vaginitis", "비뇨기"),
        ("N92.0", "과다 월경", "Excessive menstruation", "비뇨기"),
        # 내분비 추가 — 25→40
        ("E00.9", "선천성 요오드 결핍 증후군", "Congenital iodine-deficiency syndrome", "내분비"),
        ("E04.9", "비중독성 갑상선종", "Nontoxic goitre", "내분비"),
        ("E16.2", "저혈당증, 상세불명", "Hypoglycaemia, unspecified", "내분비"),
        ("E20.9", "부갑상선기능저하증", "Hypoparathyroidism", "내분비"),
        ("E21.0", "원발성 부갑상선기능항진증", "Primary hyperparathyroidism", "내분비"),
        ("E22.0", "말단비대증 및 뇌하수체 거대증", "Acromegaly and pituitary gigantism", "내분비"),
        ("E23.0", "뇌하수체기능저하증", "Hypopituitarism", "내분비"),
        ("E24.9", "쿠싱증후군", "Cushing syndrome", "내분비"),
        ("E25.0", "선천성 부신과형성증", "Congenital adrenogenital disorders", "내분비"),
        ("E27.1", "원발성 부신피질기능부전(에디슨병)", "Primary adrenocortical insufficiency", "내분비"),
        ("E27.4", "부신피질기능부전, 상세불명", "Adrenocortical insufficiency, unspecified", "내분비"),
        ("E28.2", "다낭성 난소증후군", "PCOS", "내분비"),
        ("E55.9", "비타민D 결핍", "Vitamin D deficiency", "내분비"),
        ("E83.5", "고칼슘혈증", "Hypercalcaemia", "내분비"),
        ("E88.9", "대사장애, 상세불명", "Metabolic disorder, unspecified", "내분비"),
        # 순환기 추가
        ("I05.1", "승모판폐쇄부전", "Mitral insufficiency", "순환기"),
        ("I06.0", "대동맥판협착", "Aortic stenosis", "순환기"),
        ("I26.9", "폐색전증", "Pulmonary embolism", "순환기"),
        ("I27.0", "원발성 폐동맥 고혈압", "Primary pulmonary hypertension", "순환기"),
        ("I33.0", "급성 감염성 심내막염", "Acute infective endocarditis", "순환기"),
        ("I40.9", "급성 심근염", "Acute myocarditis", "순환기"),
        ("I42.1", "비후성 폐쇄성 심근병증", "HOCM", "순환기"),
        ("I44.2", "완전 방실차단", "Complete AV block", "순환기"),
        ("I47.1", "심실상성 빈맥", "Supraventricular tachycardia", "순환기"),
        ("I47.2", "심실빈맥", "Ventricular tachycardia", "순환기"),
        ("I49.0", "심실세동", "Ventricular fibrillation", "순환기"),
        ("I67.1", "뇌동맥류 비파열", "Cerebral aneurysm, nonruptured", "순환기"),
        ("I71.3", "복부대동맥류 비파열", "Abdominal aortic aneurysm, nonruptured", "순환기"),
        ("I71.4", "복부대동맥류 파열", "Abdominal aortic aneurysm, ruptured", "순환기"),
        # 소화기 추가
        ("K22.0", "이완불능증(아칼라지아)", "Achalasia of cardia", "소화기"),
        ("K27.9", "소화성 궤양, 상세불명", "Peptic ulcer, unspecified", "소화기"),
        ("K29.7", "위염, 상세불명", "Gastritis, unspecified", "소화기"),
        ("K37", "상세불명의 충수염", "Unspecified appendicitis", "소화기"),
        ("K44.9", "횡격막 탈장", "Diaphragmatic hernia", "소화기"),
        ("K55.0", "장간막 혈관의 급성 혈관 장애", "Acute vascular disorders of intestine", "소화기"),
        ("K57.3", "게실질환", "Diverticular disease of large intestine", "소화기"),
        ("K58.9", "과민성 대장증후군", "IBS", "소화기"),
        ("K59.0", "변비", "Constipation", "소화기"),
        ("K62.5", "치핵(치질)", "Haemorrhoids", "소화기"),
        ("K65.0", "급성 복막염", "Acute peritonitis", "소화기"),
        ("K72.9", "간부전", "Hepatic failure", "소화기"),
        ("K73.9", "만성 간염", "Chronic hepatitis", "소화기"),
        ("K83.0", "담관염", "Cholangitis", "소화기"),
        ("K92.0", "토혈", "Haematemesis", "소화기"),
        ("K92.1", "흑색변(멜레나)", "Melaena", "소화기"),
        # 근골격 추가
        ("M06.9", "류마토이드 관절염, 상세불명", "RA, unspecified", "근골격"),
        ("M13.9", "관절염, 상세불명", "Arthritis, unspecified", "근골격"),
        ("M16.9", "고관절증", "Coxarthrosis", "근골격"),
        ("M20.1", "무지외반증", "Hallux valgus", "근골격"),
        ("M21.1", "외반슬", "Valgus deformity of knee", "근골격"),
        ("M24.5", "관절 구축", "Contracture of joint", "근골격"),
        ("M25.5", "관절통", "Pain in joint", "근골격"),
        ("M34.9", "전신경화증(경피증)", "Systemic sclerosis", "근골격"),
        ("M41.9", "척추측만증", "Scoliosis", "근골격"),
        ("M45", "강직척추염", "Ankylosing spondylitis", "근골격"),
        ("M48.0", "척추관 협착증", "Spinal stenosis", "근골격"),
        ("M54.4", "좌골신경통", "Sciatica with lumbago", "근골격"),
        ("M72.0", "수장건막구축(듀프이트렌)", "Dupuytren contracture", "근골격"),
        ("M76.6", "아킬레스건염", "Achilles tendinitis", "근골격"),
        ("M77.1", "외측 상과염(테니스 엘보)", "Lateral epicondylitis", "근골격"),
        ("M79.1", "근육통", "Myalgia", "근골격"),
        ("M84.4", "병적 골절", "Pathological fracture", "근골격"),
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


def expand_drugs():
    conn = get_db()
    existing = set(r[0] for r in conn.execute("SELECT name_generic FROM drug_codes").fetchall())

    drugs = [
        # 심혈관
        ("Bisoprolol", "비소프롤롤", "BB(선택적)", "2.5-10mg", "1일 1회"),
        ("Propranolol", "프로프라놀롤", "BB(비선택)", "40-160mg", "1일 2-3회"),
        ("Lercanidipine", "레르카니디핀", "CCB", "10-20mg", "1일 1회"),
        ("Telmisartan", "텔미사르탄", "ARB", "40-80mg", "1일 1회"),
        ("Fimasartan", "피마사르탄", "ARB", "60-120mg", "1일 1회"),
        ("Perindopril", "페린도프릴", "ACEi", "4-8mg", "1일 1회"),
        ("Furosemide", "푸로세미드", "루프이뇨제", "20-80mg", "1일 1-2회"),
        ("Torasemide", "토라세미드", "루프이뇨제", "5-20mg", "1일 1회"),
        ("Eplerenone", "에플레레논", "MRA", "25-50mg", "1일 1회"),
        ("Ivabradine", "이바브라딘", "심박수조절", "5-7.5mg", "1일 2회"),
        ("Sacubitril/Valsartan", "사쿠비트릴/발사르탄", "ARNI", "50-200mg", "1일 2회"),
        ("Nicorandil", "니코란딜", "항협심증", "5-20mg", "1일 3회"),
        ("Isosorbide mononitrate", "이소소르비드", "질산염", "20-60mg", "1일 1-2회"),
        ("Amiodarone", "아미오다론", "항부정맥III", "200mg", "1일 1회"),
        ("Flecainide", "플레카이니드", "항부정맥IC", "50-150mg", "1일 2회"),
        ("Prasugrel", "프라수그렐", "ADP 억제제", "10mg", "1일 1회"),
        ("Dabigatran", "다비가트란", "NOAC(트롬빈)", "110-150mg", "1일 2회"),
        # 소화기
        ("Esomeprazole", "에소메프라졸", "PPI", "20-40mg", "1일 1회"),
        ("Dexlansoprazole", "덱스란소프라졸", "PPI", "30-60mg", "1일 1회"),
        ("Rebamipide", "레바미피드", "점막보호", "100mg", "1일 3회"),
        ("Tegaserod", "테가세로드", "5HT4작용제", "6mg", "1일 2회"),
        ("Trimebutine", "트리메부틴", "위장운동조절", "100-200mg", "1일 3회"),
        ("Bismuth subsalicylate", "비스무트", "위점막보호", "524mg", "필요 시"),
        ("Ursodeoxycholic acid", "우르소데옥시콜산", "담즙산", "300-900mg", "1일 2-3회"),
        ("Mesalazine", "메살라진", "5-ASA", "1.5-4g", "1일 2-3회"),
        ("Sulfasalazine", "설파살라진", "DMARD/5-ASA", "1-3g", "1일 2-3회"),
        # 내분비/대사
        ("Alogliptin", "알로글립틴", "DPP-4i", "25mg", "1일 1회"),
        ("Vildagliptin", "빌다글립틴", "DPP-4i", "50mg", "1일 2회"),
        ("Dapagliflozin", "다파글리플로진", "SGLT2i", "10mg", "1일 1회"),
        ("Liraglutide", "리라글루티드", "GLP-1RA", "0.6-1.8mg", "1일 1회"),
        ("Acarbose", "아카보스", "알파글루코시다제억제", "50-100mg", "식전"),
        ("Levothyroxine", "레보티록신", "갑상선호르몬", "25-200mcg", "1일 1회"),
        ("Methimazole", "메티마졸", "항갑상선", "5-30mg", "1일 1-3회"),
        ("Alendronate", "알렌드로네이트", "비스포스포네이트", "70mg", "주 1회"),
        ("Denosumab", "데노수맙", "RANKL억제", "60mg", "6개월 1회 피하"),
        ("Testosterone", "테스토스테론", "남성호르몬", "250mg", "2-4주 1회 IM"),
        # 항생제/항감염
        ("Ceftazidime", "세프타지딤", "3세대세팔", "1-2g", "1일 2-3회 IV"),
        ("Piperacillin/Tazobactam", "피페라실린/타조박탐", "페니실린+", "4.5g", "1일 3-4회 IV"),
        ("Meropenem", "메로페넴", "카바페넴", "1-2g", "1일 3회 IV"),
        ("Imipenem", "이미페넴", "카바페넴", "500mg", "1일 3-4회 IV"),
        ("Linezolid", "리네졸리드", "옥사졸리디논", "600mg", "1일 2회"),
        ("Daptomycin", "답토마이신", "리포펩타이드", "4-6mg/kg", "1일 1회 IV"),
        ("Fluconazole", "플루코나졸", "아졸계항진균", "100-400mg", "1일 1회"),
        ("Voriconazole", "보리코나졸", "아졸계항진균", "200mg", "1일 2회"),
        ("Acyclovir", "아시클로비르", "항바이러스", "200-800mg", "1일 5회"),
        ("Valacyclovir", "발라시클로비르", "항바이러스", "500-1000mg", "1일 2-3회"),
        ("Oseltamivir", "오셀타미비르", "항인플루엔자", "75mg", "1일 2회 5일"),
        # 근골격/통증
        ("Naproxen", "나프록센", "NSAID", "250-500mg", "1일 2회"),
        ("Diclofenac", "디클로페낙", "NSAID", "50mg", "1일 2-3회"),
        ("Etoricoxib", "에토리콕시브", "COX-2 선택", "60-120mg", "1일 1회"),
        ("Tizanidine", "티자니딘", "근이완제", "2-4mg", "1일 3회"),
        ("Colchicine", "콜키신", "통풍급성", "0.5mg", "1일 2-3회"),
        ("Febuxostat", "페북소스타트", "요산저하", "40-80mg", "1일 1회"),
        ("Methotrexate", "메토트렉세이트", "DMARD", "7.5-25mg", "주 1회"),
        ("Leflunomide", "레플루노미드", "DMARD", "10-20mg", "1일 1회"),
        ("Hydroxychloroquine", "하이드록시클로로퀸", "DMARD", "200-400mg", "1일 1-2회"),
        # 신경/정신 추가
        ("Donepezil", "도네페질", "치매(AChEi)", "5-10mg", "1일 1회"),
        ("Memantine", "메만틴", "치매(NMDA)", "5-20mg", "1일 1회"),
        ("Levodopa/Carbidopa", "레보도파/카르비도파", "항파킨슨", "100/25mg", "1일 3-4회"),
        ("Ropinirole", "로피니롤", "도파민작용제", "0.25-8mg", "1일 3회"),
        ("Valproate", "발프로산", "항경련/기분조절", "500-2000mg", "1일 2-3회"),
        ("Lamotrigine", "라모트리진", "항경련/기분조절", "25-400mg", "1일 1-2회"),
        ("Topiramate", "토피라메이트", "항경련/편두통", "25-200mg", "1일 2회"),
        ("Lithium", "리튬", "기분조절제", "300-600mg", "1일 2-3회"),
        ("Bupropion", "부프로피온", "NDRI/금연", "150-300mg", "1일 1-2회"),
        ("Trazodone", "트라조돈", "SARI", "50-150mg", "취침 전"),
        ("Clonazepam", "클로나제팜", "벤조디아제핀", "0.5-2mg", "1일 2회"),
        ("Lorazepam", "로라제팜", "벤조디아제핀", "0.5-2mg", "1일 2-3회"),
        ("Methylphenidate", "메틸페니데이트", "ADHD", "10-60mg", "1일 1-2회"),
        ("Atomoxetine", "아토목세틴", "ADHD(비자극)", "40-80mg", "1일 1회"),
        # 호흡기 추가
        ("Umeclidinium/Vilanterol", "우메클리디늄/빌란테롤", "LAMA/LABA", "62.5/25mcg", "1일 1회"),
        ("Glycopyrronium", "글리코피로늄", "LAMA", "50mcg", "1일 1회"),
        ("Roflumilast", "로플루밀라스트", "PDE4억제", "500mcg", "1일 1회"),
        ("Omalizumab", "오말리주맙", "항IgE", "150-375mg", "2-4주 1회 피하"),
        ("Acetylcysteine", "아세틸시스테인", "점액용해", "200-600mg", "1일 2-3회"),
        ("Codeine", "코데인", "진해제", "15-30mg", "1일 3-4회"),
    ]

    added = 0
    for generic, name_ko, category, strength, freq in drugs:
        if generic not in existing:
            conn.execute(
                "INSERT OR IGNORE INTO drug_codes (name_generic, name_ko, category, strength, frequency, is_common) VALUES (?,?,?,?,?,1)",
                (generic, name_ko, category, strength, freq))
            added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM drug_codes").fetchone()[0]
    conn.close()
    print(f"[Drug] +{added}건 (총 {total}건)")
    return added


def expand_interactions():
    conn = get_db()
    existing = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]

    interactions = [
        ("Metformin", "Iodinated contrast", "major", "젖산산증 위험", "시술 48시간 전 중단"),
        ("Warfarin", "Cranberry juice", "moderate", "INR 상승", "과량 섭취 회피"),
        ("Warfarin", "Rifampin", "major", "Warfarin 효과 감소", "INR 면밀 모니터링, 용량 증량"),
        ("Warfarin", "Acetaminophen", "moderate", "고용량 시 INR 상승", "아세트아미노펜 2g/일 이하"),
        ("Digoxin", "Clarithromycin", "major", "Digoxin 농도 상승", "Digoxin 감량 또는 Azithromycin"),
        ("Digoxin", "Spironolactone", "moderate", "Digoxin 농도 상승", "Digoxin 농도 모니터링"),
        ("Lithium", "Thiazide", "major", "Lithium 배설 감소", "Lithium 농도 모니터링"),
        ("Lithium", "Ibuprofen", "major", "Lithium 농도 상승", "NSAID 회피"),
        ("Methotrexate", "Penicillin", "moderate", "MTX 배설 감소", "신기능 모니터링"),
        ("Phenytoin", "Carbamazepine", "major", "상호 대사 영향", "양쪽 농도 모니터링"),
        ("Ciprofloxacin", "Tizanidine", "contraindicated", "Tizanidine 독성", "절대 병용 금기"),
        ("Ciprofloxacin", "Warfarin", "major", "INR 상승", "INR 면밀 모니터링"),
        ("Clarithromycin", "Simvastatin", "contraindicated", "횡문근융해증", "스타틴 일시 중단"),
        ("Clarithromycin", "Midazolam", "major", "과도한 진정", "벤조 감량"),
        ("Fluconazole", "Warfarin", "major", "INR 상승", "INR 모니터링, Warfarin 감량"),
        ("Voriconazole", "Sirolimus", "contraindicated", "Sirolimus 독성", "병용 금기"),
        ("Erythromycin", "Simvastatin", "major", "횡문근융해증", "스타틴 일시 중단"),
        ("Ketoconazole", "Triazolam", "contraindicated", "과도한 진정", "병용 금기"),
        ("Metronidazole", "Alcohol", "major", "디설피람 유사 반응", "알코올 절대 금지"),
        ("Metronidazole", "Lithium", "major", "Lithium 독성", "Lithium 농도 모니터링"),
        ("Amiodarone", "Digoxin", "major", "Digoxin 농도 2배 상승", "Digoxin 50% 감량"),
        ("Amiodarone", "Warfarin", "major", "INR 상승", "Warfarin 30-50% 감량"),
        ("Amiodarone", "Simvastatin", "major", "횡문근융해증", "Simvastatin 20mg 이하"),
        ("Verapamil", "Digoxin", "major", "Digoxin 농도 상승 + 서맥", "Digoxin 감량"),
        ("Diltiazem", "Simvastatin", "major", "횡문근융해증 위험", "Simvastatin 10mg 이하"),
        ("Spironolactone", "ACE inhibitor", "major", "고칼륨혈증", "칼륨 모니터링"),
        ("Spironolactone", "Potassium", "major", "고칼륨혈증", "칼륨 보충 중단"),
        ("Clopidogrel", "Esomeprazole", "moderate", "활성 대사물 감소", "Pantoprazole 권장"),
        ("Aspirin", "Ibuprofen", "moderate", "항혈소판 효과 감소", "Aspirin 선 복용 30분 후 NSAID"),
        ("Aspirin", "SSRI", "moderate", "출혈 위험 증가", "출혈 징후 모니터링"),
        ("SSRI", "Triptans", "major", "세로토닌 증후군", "면밀 모니터링"),
        ("SSRI", "Linezolid", "contraindicated", "세로토닌 증후군", "14일 washout"),
        ("Levodopa", "Metoclopramide", "major", "도파민 길항", "Domperidone으로 변경"),
        ("Theophylline", "Ciprofloxacin", "major", "Theophylline 독성", "농도 모니터링"),
        ("Theophylline", "Carbamazepine", "moderate", "Theophylline 효과 감소", "농도 모니터링"),
        ("Cyclosporine", "NSAID", "major", "신독성 증가", "신기능 모니터링"),
        ("Cyclosporine", "Potassium", "major", "고칼륨혈증", "칼륨 모니터링"),
        ("Tacrolimus", "Fluconazole", "major", "Tacrolimus 농도 상승", "농도 모니터링, 감량"),
        ("Sildenafil", "Alpha-blocker", "major", "저혈압", "4시간 간격"),
        ("Colchicine", "Cyclosporine", "major", "Colchicine 독성", "Colchicine 감량"),
        ("Allopurinol", "Azathioprine", "major", "골수 억제", "Azathioprine 25-33% 감량"),
        ("Allopurinol", "ACE inhibitor", "moderate", "과민 반응 증가", "피부 반응 모니터링"),
        ("Valproate", "Lamotrigine", "major", "Lamotrigine 농도 2배", "Lamotrigine 50% 감량"),
        ("Carbamazepine", "OCP", "major", "피임 효과 감소", "비호르몬 피임 병행"),
        ("Rifampin", "OCP", "major", "피임 효과 감소", "비호르몬 피임 병행"),
        ("Rifampin", "Warfarin", "major", "Warfarin 효과 대폭 감소", "INR 면밀 모니터링"),
        ("Rifampin", "Methadone", "major", "금단 증상", "Methadone 증량 필요"),
        ("Grapefruit", "Simvastatin", "major", "횡문근융해증", "자몽 회피"),
        ("Grapefruit", "Cyclosporine", "major", "농도 상승", "자몽 회피"),
        ("Grapefruit", "Felodipine", "moderate", "저혈압", "자몽 회피"),
        ("Iron", "Levothyroxine", "moderate", "흡수 감소", "4시간 간격"),
        ("Iron", "Fluoroquinolone", "moderate", "흡수 감소", "2시간 간격"),
        ("Calcium", "Fluoroquinolone", "moderate", "흡수 감소", "2시간 간격"),
        ("Calcium", "Levothyroxine", "moderate", "흡수 감소", "4시간 간격"),
        ("Antacid", "Fluoroquinolone", "moderate", "흡수 감소", "2시간 간격"),
    ]

    added = 0
    for a, b, sev, desc, mgmt in interactions:
        row = conn.execute(
            "SELECT 1 FROM drug_interactions WHERE (drug_a=? AND drug_b=?) OR (drug_a=? AND drug_b=?)",
            (a, b, b, a)).fetchone()
        if not row:
            conn.execute("INSERT INTO drug_interactions (drug_a, drug_b, severity, description, management) VALUES (?,?,?,?,?)",
                         (a, b, sev, desc, mgmt))
            added += 1
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM drug_interactions").fetchone()[0]
    conn.close()
    print(f"[Interactions] +{added}건 (총 {total}건)")
    return added


if __name__ == "__main__":
    print("=" * 50)
    print("의료 데이터 대량 확충 2차")
    print("=" * 50)
    k = expand_kcd()
    d = expand_drugs()
    i = expand_interactions()
    print(f"\n완료: KCD +{k}, Drug +{d}, Interactions +{i}")
