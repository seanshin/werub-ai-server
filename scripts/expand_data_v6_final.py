#!/usr/bin/env python3
"""KCD 최종 확충 — 884→1000 (116건 추가, 다양한 세부 코드)"""
import os, sqlite3
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def expand_kcd():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "clinic_codes.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    existing = set(r[0] for r in conn.execute("SELECT code FROM kcd_codes").fetchall())
    codes = [
        # 순환기
        ("I15.0", "신혈관성 고혈압", "Renovascular hypertension", "순환기"),
        ("I20.8", "기타 협심증", "Other forms of angina pectoris", "순환기"),
        ("I21.4", "급성 심내막하 심근경색", "Acute subendocardial MI", "순환기"),
        ("I24.9", "급성 허혈성 심장병", "Acute ischaemic heart disease", "순환기"),
        ("I42.9", "심근병증, 상세불명", "Cardiomyopathy, unspecified", "순환기"),
        ("I45.1", "우각 블록", "Right bundle-branch block", "순환기"),
        ("I45.2", "좌각 블록", "Left bundle-branch block", "순환기"),
        ("I49.9", "심장 부정맥, 상세불명", "Cardiac arrhythmia, unspecified", "순환기"),
        ("I60.9", "지주막하출혈", "Subarachnoid haemorrhage", "순환기"),
        ("I61.9", "뇌내출혈, 상세불명", "Intracerebral haemorrhage", "순환기"),
        ("I63.0", "뇌전동맥 혈전에 의한 뇌경색", "Cerebral infarction due to precerebral artery thrombosis", "순환기"),
        ("I65.2", "경동맥 폐쇄 및 협착", "Occlusion and stenosis of carotid artery", "순환기"),
        ("I67.4", "고혈압성 뇌병증", "Hypertensive encephalopathy", "순환기"),
        ("I69.3", "뇌경색의 후유증", "Sequelae of cerebral infarction", "순환기"),
        ("I70.2", "사지동맥 죽상경화증", "Atherosclerosis of arteries of extremities", "순환기"),
        ("I74.3", "하지 동맥 색전증", "Embolism of arteries of lower extremities", "순환기"),
        ("I77.6", "동맥염, 상세불명", "Arteritis, unspecified", "순환기"),
        ("I87.0", "정맥후 증후군", "Postthrombotic syndrome", "순환기"),
        ("I87.2", "정맥 부전", "Venous insufficiency", "순환기"),
        # 소화기
        ("K04.0", "치수염", "Pulpitis", "소화기"),
        ("K05.3", "만성 치주염", "Chronic periodontitis", "소화기"),
        ("K08.8", "치아 지지구조 장애", "Other specified disorders of teeth", "소화기"),
        ("K22.6", "위식도 열공증후군", "Gastro-oesophageal laceration-haemorrhage syndrome", "소화기"),
        ("K29.1", "기타 급성 위염", "Other acute gastritis", "소화기"),
        ("K35.3", "천공 없는 급성 충수염", "Acute appendicitis without perforation", "소화기"),
        ("K40.3", "편측 서혜부 탈장, 폐쇄 동반", "Inguinal hernia with obstruction", "소화기"),
        ("K52.0", "방사선 위장염", "Gastroenteritis due to radiation", "소화기"),
        ("K56.0", "마비성 장폐색", "Paralytic ileus", "소화기"),
        ("K56.2", "장염전", "Volvulus", "소화기"),
        ("K56.4", "기타 장 폐쇄", "Other impaction of intestine", "소화기"),
        ("K62.0", "항문 폴립", "Anal polyp", "소화기"),
        ("K62.1", "직장 폴립", "Rectal polyp", "소화기"),
        ("K70.1", "알코올성 간염", "Alcoholic hepatitis", "소화기"),
        ("K74.0", "간 섬유증", "Hepatic fibrosis", "소화기"),
        ("K76.6", "문맥 고혈압", "Portal hypertension", "소화기"),
        ("K80.1", "담석증, 기타", "Calculus of gallbladder with other cholecystitis", "소화기"),
        ("K83.1", "담관 폐쇄", "Obstruction of bile duct", "소화기"),
        ("K86.0", "알코올성 만성 췌장염", "Alcohol-induced chronic pancreatitis", "소화기"),
        ("K91.1", "위절제후 증후군", "Postgastric surgery syndromes", "소화기"),
        ("K92.2", "위장관 출혈, 상세불명", "GI haemorrhage, unspecified", "소화기"),
        # 근골격
        ("M08.0", "소아 류마토이드 관절염", "Juvenile RA", "근골격"),
        ("M10.1", "납에 의한 통풍", "Lead-induced gout", "근골격"),
        ("M12.5", "외상성 관절병증", "Traumatic arthropathy", "근골격"),
        ("M18.9", "제1수근중수관절 관절증", "Arthrosis of first CMC joint", "근골격"),
        ("M19.0", "원발성 관절증", "Primary arthrosis of other joints", "근골격"),
        ("M25.4", "관절삼출", "Effusion of joint", "근골격"),
        ("M31.3", "베게너 육아종증", "Wegener granulomatosis", "근골격"),
        ("M33.0", "소아 피부근염", "Juvenile dermatomyositis", "근골격"),
        ("M33.1", "기타 피부근염", "Other dermatomyositis", "근골격"),
        ("M34.0", "진행성 전신경화증", "Progressive systemic sclerosis", "근골격"),
        ("M42.0", "소아 척추 골연골증(쇼이어만)", "Juvenile osteochondrosis of spine", "근골격"),
        ("M43.0", "척추분리증", "Spondylolysis", "근골격"),
        ("M46.4", "추간판염", "Discitis", "근골격"),
        ("M51.0", "요추간판장애, 척수병증 동반", "Lumbar disc disorder with myelopathy", "근골격"),
        ("M53.1", "경완증후군(경추완증후군)", "Cervicobrachial syndrome", "근골격"),
        ("M65.4", "방아쇠 손가락", "Trigger finger", "근골격"),
        ("M66.0", "슬와 낭종 파열(베이커낭종)", "Rupture of popliteal cyst", "근골격"),
        ("M70.4", "전슬개 활액낭염", "Prepatellar bursitis", "근골격"),
        ("M72.2", "발바닥 근막 섬유종증", "Plantar fascial fibromatosis", "근골격"),
        ("M75.0", "유착 관절낭염(동결건)", "Adhesive capsulitis of shoulder", "근골격"),
        ("M76.1", "요근건염", "Psoas tendinitis", "근골격"),
        ("M77.0", "내측 상과염(골프 엘보)", "Medial epicondylitis", "근골격"),
        ("M79.7", "연부조직 장애", "Soft tissue disorder, unspecified", "근골격"),
        ("M80.0", "폐경후 골다공증 병적 골절", "Postmenopausal osteoporosis with pathological fracture", "근골격"),
        ("M84.1", "골 유합 불능", "Nonunion of fracture", "근골격"),
        ("M85.0", "섬유이형성증", "Fibrous dysplasia", "근골격"),
        ("M86.9", "골수염, 상세불명", "Osteomyelitis, unspecified", "근골격"),
        ("M87.0", "골괴사(특발성)", "Idiopathic aseptic necrosis of bone", "근골격"),
        # 증상 추가
        ("R04.2", "객혈", "Haemoptysis", "증상"),
        ("R06.2", "천명", "Wheezing", "증상"),
        ("R07.1", "흡기 시 흉통", "Chest pain on breathing", "증상"),
        ("R09.1", "흉막삼출(증상)", "Pleurisy", "증상"),
        ("R10.1", "상복부 통증", "Pain localized to upper abdomen", "증상"),
        ("R10.3", "하복부 통증", "Pain localized to lower abdomen", "증상"),
        ("R14", "고창 및 관련 상태", "Flatulence and related conditions", "증상"),
        ("R15", "대변 실금", "Faecal incontinence", "증상"),
        ("R19.4", "배변 습관의 변화", "Change in bowel habit", "증상"),
        ("R20.0", "피부 무감각", "Anaesthesia of skin", "증상"),
        ("R22.0", "머리의 국한성 부종", "Localized swelling of head", "증상"),
        ("R23.0", "청색증", "Cyanosis", "증상"),
        ("R23.3", "자발성 반상출혈", "Spontaneous ecchymoses", "증상"),
        ("R25.2", "경련 및 연축", "Cramp and spasm", "증상"),
        ("R27.0", "운동실조, 상세불명", "Ataxia, unspecified", "증상"),
        ("R29.6", "반복 낙상 경향", "Tendency to fall", "증상"),
        ("R30.0", "배뇨통", "Dysuria", "증상"),
        ("R32", "요실금, 상세불명", "Unspecified urinary incontinence", "증상"),
        ("R34", "무뇨 및 핍뇨", "Anuria and oliguria", "증상"),
        ("R35", "다뇨", "Polyuria", "증상"),
        ("R41.3", "기억상실, 상세불명", "Other amnesia", "증상"),
        ("R45.0", "신경과민", "Nervousness", "증상"),
        ("R45.1", "불안 및 초조", "Restlessness and agitation", "증상"),
        ("R47.1", "구음장애 및 언어불명료", "Dysarthria and anarthria", "증상"),
        ("R53", "불쾌감 및 피로", "Malaise and fatigue", "증상"),
        ("R55", "실신 및 허탈", "Syncope and collapse", "증상"),
        ("R56.8", "기타 경련", "Other and unspecified convulsions", "증상"),
        ("R59.9", "림프절비대, 상세불명", "Enlarged lymph nodes, unspecified", "증상"),
        ("R60.1", "전신 부종", "Generalized oedema", "증상"),
        ("R62.8", "발달 부진", "Other lack of expected normal development", "증상"),
        ("R63.0", "식욕부진", "Anorexia", "증상"),
        ("R68.8", "기타 전신 증상 및 징후", "Other general symptoms and signs", "증상"),
        ("R73.9", "고혈당증, 상세불명", "Hyperglycaemia, unspecified", "증상"),
        ("R74.0", "트랜스아미나제 상승", "Elevation of transaminase levels", "증상"),
        ("R77.0", "알부민 이상", "Abnormality of albumin", "증상"),
        ("R79.0", "혈중 무기질 수치 이상", "Abnormal level of blood mineral", "증상"),
        ("R79.8", "혈액화학 이상 소견", "Other specified abnormal findings of blood chemistry", "증상"),
        ("R80", "단백뇨", "Proteinuria", "증상"),
        ("R82.1", "혈뇨(미시적)", "Microscopic haematuria", "증상"),
        ("R91", "폐의 영상 이상 소견", "Abnormal findings on diagnostic imaging of lung", "증상"),
        ("R93.4", "신장 영상 이상 소견", "Abnormal findings on diagnostic imaging of kidney", "증상"),
        ("R94.3", "심혈관 기능검사 이상", "Abnormal results of cardiovascular function studies", "증상"),
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
    print("KCD 최종 확충 (884→1000)")
    print("=" * 50)
    added, total = expand_kcd()
    if total >= 1000:
        print("*** 목표 1000건 달성! ***")
    else:
        print(f"목표까지 {1000 - total}건 남음")
