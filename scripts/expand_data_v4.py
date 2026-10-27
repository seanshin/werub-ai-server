#!/usr/bin/env python3
"""KCD 4차 확충 — 665→1000 목표 (335건 추가)

전략: 각 카테고리별 세부 질환 코드 추가 (실제 임상 빈도 높은 코드 우선)
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
        # === 순환기 추가 (47→65) ===
        ("I05.0", "승모판 협착", "Mitral stenosis", "순환기"),
        ("I06.1", "대동맥판 폐쇄부전", "Aortic insufficiency", "순환기"),
        ("I07.0", "삼첨판 협착", "Tricuspid stenosis", "순환기"),
        ("I08.0", "승모판 및 대동맥판 장애", "Combined mitral and aortic valve disorder", "순환기"),
        ("I09.9", "류마티스성 심장병", "Rheumatic heart disease", "순환기"),
        ("I10", "본태성 고혈압", "Essential hypertension", "순환기"),
        ("I12.9", "고혈압성 신장병", "Hypertensive renal disease", "순환기"),
        ("I13.1", "고혈압성 심장신장병", "Hypertensive heart and renal disease", "순환기"),
        ("I20.1", "관상동맥 경련에 의한 협심증", "Prinzmetal angina", "순환기"),
        ("I21.0", "전벽 급성 심근경색", "Acute anterior MI", "순환기"),
        ("I21.1", "하벽 급성 심근경색", "Acute inferior MI", "순환기"),
        ("I25.0", "죽상경화성 심혈관질환", "ASCVD", "순환기"),
        ("I25.5", "허혈성 심근병증", "Ischaemic cardiomyopathy", "순환기"),
        ("I30.9", "심낭염, 상세불명", "Pericarditis, unspecified", "순환기"),
        ("I31.3", "심낭삼출", "Pericardial effusion", "순환기"),
        ("I34.0", "승모판 폐쇄부전(비류마티스)", "Mitral insufficiency (nonrheumatic)", "순환기"),
        ("I35.0", "대동맥판 협착(비류마티스)", "Aortic stenosis (nonrheumatic)", "순환기"),
        ("I46.9", "심정지", "Cardiac arrest", "순환기"),
        # === 내분비 추가 (36→55) ===
        ("E01.2", "요오드 결핍 관련 갑상선종", "Iodine-deficiency related goitre", "내분비"),
        ("E04.1", "비독성 단일 갑상선 결절", "Nontoxic single thyroid nodule", "내분비"),
        ("E04.2", "비독성 다결절 갑상선종", "Nontoxic multinodular goitre", "내분비"),
        ("E05.0", "미만독성갑상선종(그레이브스)", "Graves disease", "내분비"),
        ("E06.1", "아급성 갑상선염", "Subacute thyroiditis", "내분비"),
        ("E06.9", "갑상선염, 상세불명", "Thyroiditis, unspecified", "내분비"),
        ("E10.9", "제1형 당뇨병, 합병증 없음", "Type 1 DM without complications", "내분비"),
        ("E11.6", "제2형 당뇨병, 기타 합병증", "Type 2 DM with other complications", "내분비"),
        ("E11.7", "제2형 당뇨병, 다발 합병증", "Type 2 DM with multiple complications", "내분비"),
        ("E14.9", "당뇨병, 상세불명", "Unspecified diabetes mellitus", "내분비"),
        ("E21.3", "부갑상선기능항진증, 상세불명", "Hyperparathyroidism, unspecified", "내분비"),
        ("E22.1", "고프로락틴혈증", "Hyperprolactinaemia", "내분비"),
        ("E23.2", "요붕증", "Diabetes insipidus", "내분비"),
        ("E26.0", "원발성 알도스테론증(콘 증후군)", "Primary hyperaldosteronism", "내분비"),
        ("E27.2", "에디슨 위기", "Addisonian crisis", "내분비"),
        ("E34.9", "내분비장애, 상세불명", "Endocrine disorder, unspecified", "내분비"),
        ("E46", "영양실조, 상세불명", "Unspecified protein-energy malnutrition", "내분비"),
        ("E56.0", "비타민E 결핍", "Vitamin E deficiency", "내분비"),
        ("E61.1", "철 결핍", "Iron deficiency", "내분비"),
        # === 정신 추가 (31→50) ===
        ("F02.8", "기타 질환에서의 치매", "Dementia in other diseases", "정신"),
        ("F05.9", "섬망, 상세불명", "Delirium, unspecified", "정신"),
        ("F06.2", "기질성 망상장애", "Organic delusional disorder", "정신"),
        ("F06.3", "기질성 기분장애", "Organic mood disorders", "정신"),
        ("F10.0", "알코올 급성 중독", "Acute alcohol intoxication", "정신"),
        ("F12.1", "대마의 유해한 사용", "Harmful use of cannabinoids", "정신"),
        ("F13.2", "진정제/수면제 의존", "Sedative dependence syndrome", "정신"),
        ("F15.1", "각성제의 유해한 사용", "Harmful use of stimulants", "정신"),
        ("F19.9", "다약물 사용 장애", "Multiple drug use disorder", "정신"),
        ("F20.5", "잔류형 조현병", "Residual schizophrenia", "정신"),
        ("F21", "조현형 장애", "Schizotypal disorder", "정신"),
        ("F22.0", "망상장애", "Delusional disorder", "정신"),
        ("F30.9", "조증 삽화", "Manic episode, unspecified", "정신"),
        ("F32.0", "경도 우울 에피소드", "Mild depressive episode", "정신"),
        ("F32.1", "중등도 우울 에피소드", "Moderate depressive episode", "정신"),
        ("F32.2", "중증 우울 에피소드(정신병적 증상 없음)", "Severe depressive episode without psychosis", "정신"),
        ("F33.0", "재발성 우울장애, 경도", "Recurrent depressive disorder, mild", "정신"),
        ("F40.0", "광장공포증", "Agoraphobia", "정신"),
        ("F43.2", "적응장애", "Adjustment disorders", "정신"),
        # === 근골격 추가 (54→70) ===
        ("M00.9", "화농성 관절염", "Pyogenic arthritis", "근골격"),
        ("M02.3", "반응성 관절병증(라이터)", "Reiter disease", "근골격"),
        ("M06.0", "혈청양성 류마토이드 관절염", "Seronegative RA", "근골격"),
        ("M10.0", "특발성 통풍", "Idiopathic gout", "근골격"),
        ("M11.2", "석회성 건염", "Calcific tendinitis", "근골격"),
        ("M19.9", "관절증, 상세불명", "Arthrosis, unspecified", "근골격"),
        ("M22.0", "재발성 슬개골 탈구", "Recurrent dislocation of patella", "근골격"),
        ("M23.2", "반월상연골의 변성", "Derangement of meniscus due to old tear", "근골격"),
        ("M24.4", "관절의 재발성 탈구", "Recurrent dislocation of joint", "근골격"),
        ("M33.2", "다발성근염", "Polymyositis", "근골격"),
        ("M35.0", "건조증후군(쇼그렌)", "Sjogren syndrome", "근골격"),
        ("M35.9", "전신결합조직장애", "Systemic involvement of connective tissue", "근골격"),
        ("M42.1", "성인 척추 골연골증", "Adult osteochondrosis of spine", "근골격"),
        ("M43.1", "척추전방전위증", "Spondylolisthesis", "근골격"),
        ("M46.1", "천장관절염", "Sacroiliitis", "근골격"),
        ("M50.2", "경추간판변위", "Other cervical disc displacement", "근골격"),
        # === 비뇨기 추가 (30→45) ===
        ("N00.9", "급성 사구체신염", "Acute glomerulonephritis", "비뇨기"),
        ("N03.9", "만성 사구체신염", "Chronic glomerulonephritis", "비뇨기"),
        ("N04.9", "신증후군", "Nephrotic syndrome", "비뇨기"),
        ("N05.9", "사구체신염, 상세불명", "Glomerulonephritis, unspecified", "비뇨기"),
        ("N12", "세뇨관-간질 신염", "Tubulo-interstitial nephritis", "비뇨기"),
        ("N13.0", "수신증(신우요관폐쇄)", "Hydronephrosis with ureteropelvic junction obstruction", "비뇨기"),
        ("N13.6", "요로폐쇄에 의한 신농양", "Pyonephrosis", "비뇨기"),
        ("N28.1", "신낭종", "Cyst of kidney", "비뇨기"),
        ("N30.1", "간질성 방광염", "Interstitial cystitis", "비뇨기"),
        ("N31.9", "신경인성 방광", "Neuromuscular dysfunction of bladder", "비뇨기"),
        ("N32.0", "방광경부 폐쇄", "Bladder-neck obstruction", "비뇨기"),
        ("N35.9", "요도협착", "Urethral stricture", "비뇨기"),
        ("N43.3", "음낭수종", "Hydrocele", "비뇨기"),
        ("N44", "고환 꼬임(고환 염전)", "Torsion of testis", "비뇨기"),
        ("N47", "포경 및 감돈포경", "Phimosis and paraphimosis", "비뇨기"),
        # === 피부 추가 (26→40) ===
        ("L04.9", "급성 림프절염", "Acute lymphadenitis", "피부"),
        ("L05.0", "모소낭", "Pilonidal cyst with abscess", "피부"),
        ("L12.0", "수포성 유천포창", "Bullous pemphigoid", "피부"),
        ("L20.9", "아토피피부염", "Atopic dermatitis", "피부"),
        ("L25.9", "접촉피부염, 상세불명", "Contact dermatitis, unspecified", "피부"),
        ("L28.0", "단순태선(신경피부염)", "Lichen simplex chronicus", "피부"),
        ("L29.9", "소양증, 상세불명", "Pruritus, unspecified", "피부"),
        ("L42", "장미색비강진", "Pityriasis rosea", "피부"),
        ("L43.9", "편평태선", "Lichen planus", "피부"),
        ("L44.0", "모공성 홍색비강진", "Pityriasis rubra pilaris", "피부"),
        ("L53.9", "홍반, 상세불명", "Erythema, unspecified", "피부"),
        ("L57.0", "광선각화증", "Actinic keratosis", "피부"),
        ("L72.0", "표피낭종", "Epidermal cyst", "피부"),
        ("L82", "지루각화증", "Seborrhoeic keratosis", "피부"),
        # === 안과 추가 (21→33) ===
        ("H02.0", "안검내반 및 첩모난생", "Entropion and trichiasis", "안과"),
        ("H02.4", "안검하수", "Ptosis of eyelid", "안과"),
        ("H05.0", "안와의 급성 염증", "Acute inflammation of orbit", "안과"),
        ("H10.0", "점액농성 결막염", "Mucopurulent conjunctivitis", "안과"),
        ("H10.3", "급성 결막염, 상세불명", "Unspecified acute conjunctivitis", "안과"),
        ("H11.0", "익상편", "Pterygium", "안과"),
        ("H15.0", "상공막염", "Scleritis", "안과"),
        ("H18.6", "원추각막", "Keratoconus", "안과"),
        ("H26.0", "소아 및 청소년 백내장", "Infantile and juvenile cataract", "안과"),
        ("H30.9", "맥락망막염, 상세불명", "Chorioretinal inflammation", "안과"),
        ("H34.8", "기타 망막혈관폐쇄", "Other retinal vascular occlusions", "안과"),
        ("H35.0", "배경 망막병증", "Background retinopathy", "안과"),
        # === 이비인후 추가 (16→28) ===
        ("H66.0", "급성 화농성 중이염", "Acute suppurative otitis media", "이비인후"),
        ("H68.0", "이관염", "Eustachian salpingitis", "이비인후"),
        ("H71", "중이 진주종", "Cholesteatoma of middle ear", "이비인후"),
        ("H73.0", "급성 고막염", "Acute myringitis", "이비인후"),
        ("H80.0", "이경화증", "Otosclerosis", "이비인후"),
        ("H83.0", "미로염", "Labyrinthitis", "이비인후"),
        ("H91.9", "난청, 상세불명", "Hearing loss, unspecified", "이비인후"),
        ("H93.1", "이명", "Tinnitus", "이비인후"),
        ("J03.9", "급성 편도염", "Acute tonsillitis", "이비인후"),
        ("J04.0", "급성 후두염", "Acute laryngitis", "이비인후"),
        ("J05.0", "급성 폐쇄성 후두염(크루프)", "Acute obstructive laryngitis (croup)", "이비인후"),
        ("J36", "편도주위농양", "Peritonsillar abscess", "이비인후"),
        # === 혈액 추가 (19→30) ===
        ("D46.9", "골수이형성증후군", "MDS", "혈액"),
        ("D47.3", "본태성 혈소판증가증", "Essential thrombocythaemia", "혈액"),
        ("D56.9", "지중해빈혈, 상세불명", "Thalassaemia, unspecified", "혈액"),
        ("D57.1", "겸상적혈구병", "Sickle-cell disease", "혈액"),
        ("D60.9", "후천성 순적혈구무형성증", "Acquired pure red cell aplasia", "혈액"),
        ("D68.3", "순환 항응고물질에 의한 출혈장애", "Haemorrhagic disorder due to circulating anticoagulants", "혈액"),
        ("D69.0", "알레르기자반증(헤노흐-쇤라인)", "Allergic purpura", "혈액"),
        ("D69.4", "원발성 혈소판감소증", "Primary thrombocytopenia", "혈액"),
        ("D72.1", "호산구증가증", "Eosinophilia", "혈액"),
        ("D75.1", "속발성 적혈구증가증", "Secondary polycythaemia", "혈액"),
        ("D76.1", "혈구탐식림프조직구증", "Haemophagocytic lymphohistiocytosis", "혈액"),
        # === 선천/신생아 추가 ===
        ("P08.1", "과숙아", "Post-term infant", "신생아"),
        ("P21.0", "중증 신생아 가사", "Severe birth asphyxia", "신생아"),
        ("P23.9", "선천성 폐렴", "Congenital pneumonia", "신생아"),
        ("P27.1", "기관지폐이형성증", "Bronchopulmonary dysplasia", "신생아"),
        ("P28.5", "신생아 호흡부전", "Respiratory failure of newborn", "신생아"),
        ("P52.2", "뇌실내출혈(신생아)", "Intraventricular haemorrhage of newborn", "신생아"),
        ("P61.0", "신생아 일과성 혈소판감소증", "Transient neonatal thrombocytopenia", "신생아"),
        ("P77", "괴사성 장염(신생아)", "Necrotizing enterocolitis of newborn", "신생아"),
        ("Q01.9", "뇌류", "Encephalocele", "선천"),
        ("Q03.9", "선천성 수두증", "Congenital hydrocephalus", "선천"),
        ("Q04.3", "기타 뇌 축소증", "Other reduction deformities of brain", "선천"),
        ("Q20.0", "총동맥간존증", "Common arterial trunk", "선천"),
        ("Q21.3", "팔로 사징후", "Tetralogy of Fallot", "선천"),
        ("Q23.0", "선천성 대동맥판 협착", "Congenital aortic stenosis", "선천"),
        ("Q24.9", "선천성 심장 기형", "Congenital malformation of heart", "선천"),
        ("Q39.0", "식도 폐쇄(누공 없음)", "Atresia of oesophagus without fistula", "선천"),
        ("Q41.0", "선천성 십이지장 폐쇄", "Congenital absence of duodenum", "선천"),
        ("Q43.1", "선천성 거대결장(히르슈스프룽)", "Hirschsprung disease", "선천"),
        ("Q54.9", "요도하열", "Hypospadias, unspecified", "선천"),
        ("Q60.0", "편측 신장 무발생증", "Renal agenesis, unilateral", "선천"),
        ("Q61.2", "다낭성 신장(상염색체 우성)", "Polycystic kidney, autosomal dominant", "선천"),
        ("Q62.0", "선천성 수신증", "Congenital hydronephrosis", "선천"),
        ("Q79.0", "선천성 횡격막 탈장", "Congenital diaphragmatic hernia", "선천"),
        ("Q79.2", "제대류(배꼽탈장)", "Exomphalos", "선천"),
        ("Q89.7", "다발성 선천 기형", "Multiple congenital malformations", "선천"),
        ("Q96.9", "터너증후군", "Turner syndrome", "선천"),
        ("Q98.0", "클라인펠터증후군", "Klinefelter syndrome", "선천"),
        # === 감염병 추가 (3→15) ===
        ("A00.9", "콜레라", "Cholera", "감염병"),
        ("A03.9", "세균성 이질", "Shigellosis", "감염병"),
        ("A05.9", "세균성 식중독", "Bacterial foodborne intoxication", "감염병"),
        ("A06.0", "아메바성 이질", "Acute amoebic dysentery", "감염병"),
        ("A20.9", "페스트", "Plague", "감염병"),
        ("A21.9", "야토병", "Tularaemia", "감염병"),
        ("A22.9", "탄저", "Anthrax", "감염병"),
        ("A23.9", "브루셀라증", "Brucellosis", "감염병"),
        ("A27.9", "렙토스피라증", "Leptospirosis", "감염병"),
        ("A36.9", "디프테리아", "Diphtheria", "감염병"),
        ("A80.9", "급성 회백수염(소아마비)", "Acute poliomyelitis", "감염병"),
        ("A82.9", "광견병", "Rabies", "감염병"),
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
    print("KCD 4차 확충 (목표: 1000건)")
    print("=" * 50)
    added, total = expand_kcd()
    gap = 1000 - total
    if gap > 0:
        print(f"목표까지 {gap}건 남음")
    else:
        print("목표 1000건 달성!")
