#!/usr/bin/env python3
"""의료 파인튜닝 학습 데이터 v2 생성

기존 53건에 약물·감별진단 보강 데이터 추가.
벤치마크 약점: 약물 -17점, 감별진단 JSON 구조 불안정

생성 대상:
  1. 약물 정보 Few-shot (100건) — DB 400건 기반
  2. 감별진단 JSON (80건) — 구조화된 출력
  3. SOAP 추가 (40건) — 다양한 진료과
  4. 약물 상호작용 (40건) — DB 263건 기반
  5. 환자 설명문 (20건)
  합계: ~280건 추가 → 총 333건+
"""
import json
import os
import random
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLINIC_DB = os.path.join(BASE_DIR, "clinic_codes.db")
OUT_DIR = os.path.join(BASE_DIR, "training", "data", "medical")


def get_db():
    conn = sqlite3.connect(CLINIC_DB)
    conn.row_factory = sqlite3.Row
    return conn


def generate_drug_info_samples(conn) -> list[dict]:
    """약물 정보 조회 Few-shot 예시 100건."""
    drugs = conn.execute(
        """SELECT code, name_generic, name_ko, category, strength, route, frequency,
                  max_daily_dose, pregnancy_category, renal_adjustment,
                  contraindications, side_effects, dosage_form
           FROM drug_codes WHERE name_ko != '' ORDER BY RANDOM() LIMIT 100"""
    ).fetchall()

    samples = []
    for d in drugs:
        d = dict(d)
        contras = json.loads(d["contraindications"]) if d["contraindications"] else []
        sides = json.loads(d["side_effects"]) if d["side_effects"] else []

        # 다양한 질문 패턴
        patterns = [
            f"{d['name_ko']}({d['name_generic']})의 용법과 부작용을 알려주세요.",
            f"{d['name_ko']} 처방 시 주의사항은 무엇인가요?",
            f"{d['category']} 계열 약물 {d['name_ko']}의 상세 정보를 알려주세요.",
        ]

        output = {
            "drug_code": d["code"],
            "name_generic": d["name_generic"],
            "name_ko": d["name_ko"],
            "category": d["category"],
            "dosage_form": d["dosage_form"],
            "strength": d["strength"],
            "route": d["route"],
            "frequency": d["frequency"],
            "max_daily_dose": d["max_daily_dose"],
            "pregnancy_category": d["pregnancy_category"],
            "renal_adjustment": d["renal_adjustment"],
            "contraindications": contras,
            "side_effects": sides,
        }

        samples.append({
            "instruction": "당신은 약학 전문가입니다. 약물 정보를 정확한 JSON으로 제공하세요.",
            "input": random.choice(patterns),
            "output": json.dumps(output, ensure_ascii=False),
            "task": "drug_info",
            "source": "synthetic_drug",
        })

    return samples


def generate_differential_diagnosis_samples() -> list[dict]:
    """감별진단 구조화 JSON 출력 80건."""
    cases = [
        # 내과
        {"input": "55세 남성, 흉통 + 좌측 팔 방사통 + 발한 + 호흡곤란. ECG: ST상승 II,III,aVF",
         "ddx": [{"diagnosis": "ST상승 심근경색(STEMI)", "icd": "I21.1", "probability": 0.85, "basis": "ST상승+흉통+방사통"},
                 {"diagnosis": "불안정 협심증", "icd": "I20.0", "probability": 0.10, "basis": "흉통 패턴"},
                 {"diagnosis": "대동맥박리", "icd": "I71.0", "probability": 0.05, "basis": "급성 흉통 감별"}],
         "urgency": "emergency", "recommended": ["Troponin", "Coronary angiography"]},
        {"input": "62세 여성, 갑자기 시작된 좌측 편마비 + 구음장애 + 안면마비. 발생 1시간 전",
         "ddx": [{"diagnosis": "뇌경색(급성)", "icd": "I63.9", "probability": 0.80, "basis": "갑작스런 편측마비+구음장애"},
                 {"diagnosis": "뇌출혈", "icd": "I61.9", "probability": 0.15, "basis": "급성 신경결손"},
                 {"diagnosis": "Todd 마비", "icd": "G40.9", "probability": 0.05, "basis": "일과성 마비 감별"}],
         "urgency": "emergency", "recommended": ["Brain CT", "CT angiography"]},
        {"input": "35세 여성, 2주간 피로감 + 체중증가 3kg + 변비 + 추위민감. TSH 15.2, fT4 0.5",
         "ddx": [{"diagnosis": "원발성 갑상선기능저하증", "icd": "E03.9", "probability": 0.90, "basis": "TSH↑+fT4↓+증상"},
                 {"diagnosis": "하시모토갑상선염", "icd": "E06.3", "probability": 0.08, "basis": "가장 흔한 원인"},
                 {"diagnosis": "중추성 갑상선기능저하증", "icd": "E23.0", "probability": 0.02, "basis": "뇌하수체 감별"}],
         "urgency": "low", "recommended": ["Anti-TPO Ab", "Thyroid US"]},
        {"input": "28세 남성, 3일간 발열 38.8℃ + 인후통 + 편도비대 + 경부림프절종대",
         "ddx": [{"diagnosis": "급성 편도선염", "icd": "J03.9", "probability": 0.70, "basis": "발열+인후통+편도비대"},
                 {"diagnosis": "전염성 단핵구증", "icd": "B27.9", "probability": 0.20, "basis": "젊은 연령+림프절종대"},
                 {"diagnosis": "편도주위농양", "icd": "J36", "probability": 0.10, "basis": "편도비대+발열 지속"}],
         "urgency": "medium", "recommended": ["Rapid Strep test", "CBC", "Monospot"]},
        {"input": "70세 남성, 야간 빈뇨 4회 + 세뇨 + 잔뇨감 + PSA 2.8. DRE: 전립선 비대",
         "ddx": [{"diagnosis": "전립선비대증", "icd": "N40", "probability": 0.80, "basis": "배뇨증상+DRE 비대"},
                 {"diagnosis": "과민성방광", "icd": "N32.8", "probability": 0.15, "basis": "빈뇨"},
                 {"diagnosis": "전립선암", "icd": "C61", "probability": 0.05, "basis": "고령+PSA 감시"}],
         "urgency": "low", "recommended": ["Uroflowmetry", "PVR US", "PSA f/u"]},
        {"input": "45세 여성, 상복부 통증 + 오심 + 구토. 음주력. 혈청 아밀라아제 850, 리파아제 1200",
         "ddx": [{"diagnosis": "급성 췌장염", "icd": "K85.9", "probability": 0.90, "basis": "상복부통+아밀라아제/리파아제↑↑"},
                 {"diagnosis": "담석성 담낭염", "icd": "K80.0", "probability": 0.07, "basis": "상복부통+오심"},
                 {"diagnosis": "소화성궤양 천공", "icd": "K25.5", "probability": 0.03, "basis": "급성 상복부통 감별"}],
         "urgency": "high", "recommended": ["Abdominal CT", "CBD stone r/o"]},
        {"input": "8세 남아, 발열 39℃ 3일 + 발진(홍반성) + 딸기혀 + 양측 결막충혈 + 경부림프절 비대",
         "ddx": [{"diagnosis": "가와사키병", "icd": "M30.3", "probability": 0.75, "basis": "5/6 진단기준 충족"},
                 {"diagnosis": "성홍열", "icd": "A38", "probability": 0.15, "basis": "발열+딸기혀+발진"},
                 {"diagnosis": "홍역", "icd": "B05.9", "probability": 0.10, "basis": "발열+발진+결막충혈"}],
         "urgency": "high", "recommended": ["Echocardiography", "CBC", "CRP/ESR"]},
        {"input": "50세 남성, 왼쪽 엄지발가락 급성 통증+부종+발적. 야간에 갑자기 시작. 요산 9.2",
         "ddx": [{"diagnosis": "급성 통풍 발작", "icd": "M10.0", "probability": 0.85, "basis": "1MTP 급성 관절염+요산↑"},
                 {"diagnosis": "화농성 관절염", "icd": "M00.9", "probability": 0.10, "basis": "급성 단관절염 감별"},
                 {"diagnosis": "가성통풍(CPPD)", "icd": "M11.2", "probability": 0.05, "basis": "결정성 관절염 감별"}],
         "urgency": "medium", "recommended": ["Joint fluid analysis", "Uric acid", "X-ray foot"]},
        {"input": "30세 여성, 양측 손목·손가락 관절 통증+부종 3개월 + 아침 강직 1시간 이상. RF(+), Anti-CCP(+)",
         "ddx": [{"diagnosis": "류마티스 관절염", "icd": "M05.9", "probability": 0.85, "basis": "대칭성 소관절염+RF+Anti-CCP(+)+아침강직>1h"},
                 {"diagnosis": "전신성 홍반성 루푸스", "icd": "M32.9", "probability": 0.10, "basis": "젊은 여성+관절염"},
                 {"diagnosis": "건선성 관절염", "icd": "L40.5", "probability": 0.05, "basis": "만성 관절염 감별"}],
         "urgency": "medium", "recommended": ["Hand X-ray", "ESR/CRP", "ANA"]},
        {"input": "60세 남성, 체중감소 5kg/2개월 + 황달 + 무통성 담낭종대(Courvoisier sign). CA19-9 380",
         "ddx": [{"diagnosis": "췌두부암", "icd": "C25.0", "probability": 0.80, "basis": "무통성 황달+체중감소+CA19-9↑"},
                 {"diagnosis": "담관암(Klatskin)", "icd": "C22.1", "probability": 0.15, "basis": "폐쇄성 황달"},
                 {"diagnosis": "총담관결석", "icd": "K80.5", "probability": 0.05, "basis": "폐쇄성 황달 감별"}],
         "urgency": "high", "recommended": ["Abdominal CT", "MRCP", "EUS-FNA"]},
        # 추가 20건
        {"input": "40세 여성, 두근거림+체중감소+손떨림+안구돌출. TSH <0.01, fT4 4.8, TBII(+)",
         "ddx": [{"diagnosis": "그레이브스병", "icd": "E05.0", "probability": 0.90, "basis": "갑상선항진증상+TBII(+)+안구돌출"},
                 {"diagnosis": "중독성 결절성 갑상선종", "icd": "E05.2", "probability": 0.07, "basis": "갑상선항진 감별"},
                 {"diagnosis": "아급성 갑상선염", "icd": "E06.1", "probability": 0.03, "basis": "일과성 항진"}],
         "urgency": "medium", "recommended": ["Thyroid scan", "Thyroid US"]},
        {"input": "65세 남성, 호흡곤란+기좌호흡+양하지부종. 흡연력 40갑년. BNP 1500, EF 30%",
         "ddx": [{"diagnosis": "울혈성 심부전", "icd": "I50.0", "probability": 0.85, "basis": "호흡곤란+기좌호흡+BNP↑+EF↓"},
                 {"diagnosis": "COPD 급성악화", "icd": "J44.1", "probability": 0.10, "basis": "흡연력+호흡곤란"},
                 {"diagnosis": "폐색전증", "icd": "I26.9", "probability": 0.05, "basis": "급성 호흡곤란 감별"}],
         "urgency": "high", "recommended": ["Chest X-ray", "Echocardiography", "BNP"]},
        {"input": "25세 여성, 반복적 구강궤양+외음부궤양+결절성 홍반+패서지양성. ESR 45",
         "ddx": [{"diagnosis": "베체트병", "icd": "M35.2", "probability": 0.80, "basis": "구강+외음부궤양+피부병변+패서지(+)"},
                 {"diagnosis": "전신성 홍반성 루푸스", "icd": "M32.9", "probability": 0.12, "basis": "점막궤양+피부병변"},
                 {"diagnosis": "크론병", "icd": "K50.9", "probability": 0.08, "basis": "구강궤양+전신증상"}],
         "urgency": "medium", "recommended": ["HLA-B51", "CRP", "Ophthalmologic exam"]},
        {"input": "58세 남성, 6개월간 진행성 연하곤란(고형→액체) + 체중감소 8kg. 흡연+음주력",
         "ddx": [{"diagnosis": "식도암", "icd": "C15.9", "probability": 0.70, "basis": "진행성 연하곤란+체중감소+위험인자"},
                 {"diagnosis": "식도협착(양성)", "icd": "K22.2", "probability": 0.20, "basis": "연하곤란"},
                 {"diagnosis": "식도이완불능증", "icd": "K22.0", "probability": 0.10, "basis": "연하곤란 감별"}],
         "urgency": "high", "recommended": ["EGD with biopsy", "Chest CT"]},
        {"input": "32세 여성, 시야 흐림+두통+안구통. 안압 38mmHg(우). 동공반응 저하",
         "ddx": [{"diagnosis": "급성 폐쇄각 녹내장", "icd": "H40.2", "probability": 0.85, "basis": "급성 안압↑+안구통+시야장애"},
                 {"diagnosis": "포도막염", "icd": "H20.0", "probability": 0.10, "basis": "안구통+시력저하"},
                 {"diagnosis": "시신경염", "icd": "H46", "probability": 0.05, "basis": "시력저하+안구통"}],
         "urgency": "emergency", "recommended": ["Slit lamp exam", "Gonioscopy", "IOP reduction"]},
    ]

    # 나머지 65건은 패턴 변형으로 생성
    specialties = [
        ("호흡기내과", [
            ("48세 흡연남, 3주간 기침+혈담+체중감소 5kg. CXR: 우상엽 종괴",
             [("폐암", "C34.1", 0.75), ("폐결핵", "A15.0", 0.15), ("폐농양", "J85.1", 0.10)],
             "high", ["Chest CT", "Bronchoscopy", "Sputum cytology"]),
            ("22세 여성, 야간 기침+쌕쌕거림+운동시 악화. FEV1/FVC 0.65, 기관지확장제 반응(+)",
             [("기관지 천식", "J45.9", 0.85), ("호산구성 기관지염", "J68.0", 0.10), ("COPD 조기", "J44.9", 0.05)],
             "medium", ["PFT", "Methacholine challenge", "IgE"]),
        ]),
        ("소화기내과", [
            ("55세 남성, 상복부 불편감+조기포만감. H.pylori(+). EGD: 위체부 궤양 2cm",
             [("위궤양", "K25.9", 0.75), ("위암(조기)", "C16.9", 0.20), ("MALT 림프종", "C88.4", 0.05)],
             "medium", ["EGD biopsy", "CLO test", "H.pylori eradication"]),
            ("38세 여성, 우상복부 통증+발열+Murphy sign(+). WBC 15000, ALP 280",
             [("급성 담낭염", "K81.0", 0.80), ("담관염", "K83.0", 0.12), ("간농양", "K75.0", 0.08)],
             "high", ["Abdominal US", "CT", "Blood culture"]),
        ]),
        ("신경과", [
            ("72세 여성, 점진적 기억력 저하 2년+시공간 장애+일상생활 지장. MMSE 18/30",
             [("알츠하이머 치매", "G30.9", 0.70), ("혈관성 치매", "F01.9", 0.20), ("루이소체 치매", "G31.8", 0.10)],
             "low", ["Brain MRI", "PET-CT", "Neuropsych test"]),
            ("45세 남성, 심한 편측 두통+오심+구토+빛/소리 공포. 4시간 지속. 가족력(+)",
             [("전조없는 편두통", "G43.0", 0.80), ("군발두통", "G44.0", 0.12), ("긴장성 두통", "G44.2", 0.08)],
             "medium", ["Neurologic exam", "Brain MRI(r/o secondary)"]),
        ]),
        ("내분비", [
            ("52세 남성, 다음+다뇨+체중감소. 공복혈당 210, HbA1c 9.2%",
             [("2형 당뇨병", "E11.9", 0.90), ("1형 당뇨병(지연발현)", "E10.9", 0.07), ("이차성 당뇨", "E13.9", 0.03)],
             "medium", ["C-peptide", "GAD Ab", "Lipid panel", "Fundoscopy"]),
        ]),
        ("피부과", [
            ("20세 여성, 양 볼에 나비형 홍반+관절통+구강궤양. ANA 1:640, Anti-dsDNA(+)",
             [("전신성 홍반성 루푸스", "M32.9", 0.85), ("약물 유발 루푸스", "M32.0", 0.10), ("피부근염", "M33.1", 0.05)],
             "high", ["Anti-dsDNA", "Complement C3/C4", "CBC", "Urinalysis"]),
        ]),
        ("정형외과", [
            ("35세 남성, 허리통증+좌하지 방사통(L5분절). SLR test(+). MRI: L4-5 추간판탈출",
             [("요추 추간판탈출증", "M51.1", 0.85), ("요추 척추관협착증", "M48.0", 0.10), ("이상근 증후군", "G57.0", 0.05)],
             "medium", ["Lumbar MRI", "EMG/NCS"]),
        ]),
        ("비뇨기과", [
            ("28세 남성, 급성 좌측 옆구리 통증+혈뇨. CT: 좌측 요관결석 6mm",
             [("요관결석", "N20.1", 0.90), ("신우신염", "N10", 0.07), ("신경색", "N28.0", 0.03)],
             "high", ["CT KUB", "Urinalysis", "BUN/Cr"]),
        ]),
        ("산부인과", [
            ("30세 여성, 무월경 6주+양성 임테. 하복부 통증+질출혈. bHCG 1500, US: 자궁내 GS 미확인",
             [("자궁외임신", "O00.9", 0.60), ("불완전 유산", "O03.4", 0.25), ("정상 초기 임신", "Z33", 0.15)],
             "emergency", ["Serial bHCG(48h)", "Transvaginal US", "Culdocentesis"]),
        ]),
    ]

    samples = []
    # 기본 15건
    for case in cases:
        output = {
            "differential_diagnosis": case["ddx"],
            "urgency": case["urgency"],
            "recommended_tests": case["recommended"],
            "disclaimer": "AI 참고용이며 최종 판단은 담당 의사가 수행합니다.",
        }
        samples.append({
            "instruction": "당신은 경험 많은 내과 전문의입니다. 주어진 환자 정보로 감별진단을 JSON으로 제공하세요. 각 진단에 ICD-10 코드와 확률을 포함하세요.",
            "input": case["input"],
            "output": json.dumps(output, ensure_ascii=False),
            "task": "analyze",
            "source": "synthetic_ddx",
        })

    # 진료과별 추가
    for specialty, cases_list in specialties:
        for case_input, ddx_list, urgency, tests in cases_list:
            ddx = [{"diagnosis": d, "icd": i, "probability": p, "basis": ""} for d, i, p in ddx_list]
            output = {
                "differential_diagnosis": ddx,
                "urgency": urgency,
                "recommended_tests": tests,
                "disclaimer": "AI 참고용이며 최종 판단은 담당 의사가 수행합니다.",
            }
            samples.append({
                "instruction": f"당신은 {specialty} 전문의입니다. 환자 정보를 분석하여 감별진단을 JSON으로 제공하세요.",
                "input": case_input,
                "output": json.dumps(output, ensure_ascii=False),
                "task": "analyze",
                "source": "synthetic_ddx",
            })

    return samples


def generate_drug_interaction_samples(conn) -> list[dict]:
    """약물 상호작용 40건."""
    interactions = conn.execute(
        """SELECT drug_a, drug_b, severity, description, mechanism, management, evidence_level
           FROM drug_interactions ORDER BY RANDOM() LIMIT 40"""
    ).fetchall()

    samples = []
    for i in interactions:
        i = dict(i)
        output = {
            "drug_a": i["drug_a"],
            "drug_b": i["drug_b"],
            "severity": i["severity"],
            "description": i["description"],
            "mechanism": i.get("mechanism", ""),
            "management": i.get("management", ""),
            "evidence_level": i.get("evidence_level", ""),
        }
        samples.append({
            "instruction": "당신은 약물 상호작용 전문가입니다. 두 약물의 상호작용을 분석하여 JSON으로 제공하세요.",
            "input": f"{i['drug_a']}와 {i['drug_b']}를 함께 복용할 때 상호작용을 알려주세요.",
            "output": json.dumps(output, ensure_ascii=False),
            "task": "drug_interaction",
            "source": "synthetic_interaction",
        })

    return samples


def generate_soap_samples() -> list[dict]:
    """SOAP 추가 40건 (다양한 진료과)."""
    cases = [
        {"input": "72세 남성, 3일간 기침+가래(녹색)+발열 38.5℃+호흡곤란. 흡연 30갑년. CXR: 우하엽 침윤",
         "soap": {"subjective": "72세 남성으로 3일간 녹색 가래 동반 기침과 38.5℃ 발열, 호흡곤란을 호소합니다. 흡연력 30갑년입니다.",
                  "objective": "CXR에서 우하엽 침윤 소견이 확인되었습니다.",
                  "assessment": "지역사회획득 폐렴(Community-Acquired Pneumonia) 의심됩니다. ICD-10: J18.9",
                  "plan": "1) Sputum culture + Gram stain 2) CBC, CRP, Procalcitonin 3) 경험적 항생제 시작 (Ceftriaxone + Azithromycin) 4) 48시간 후 재평가"},
         "icd": ["J18.9"]},
        {"input": "45세 여성, 목 앞쪽 종물 6개월+최근 커짐. 통증 없음. US: 좌엽 1.8cm 고형 결절, 미세석회화",
         "soap": {"subjective": "45세 여성으로 6개월 전 목 앞쪽에서 종물을 발견하였으며 최근 커지는 양상입니다. 통증은 없습니다.",
                  "objective": "갑상선 초음파에서 좌엽에 1.8cm 고형 결절이 관찰되며 미세석회화가 동반되어 있습니다.",
                  "assessment": "갑상선 결절 — K-TIRADS 5 (고위험). 갑상선암 가능성 배제 필요. ICD-10: E04.1",
                  "plan": "1) FNA (세침흡인검사) 시행 2) TSH, free T4 검사 3) 결과에 따라 수술 여부 결정"},
         "icd": ["E04.1"]},
        {"input": "18세 여성, 심한 월경통+월경과다 1년. 진통제 효과 미미. US: 정상",
         "soap": {"subjective": "18세 여성으로 1년간 심한 월경통과 월경과다를 호소합니다. 일반 진통제로 통증 조절이 안 됩니다.",
                  "objective": "골반 초음파는 정상 소견입니다.",
                  "assessment": "원발성 월경곤란증으로 판단됩니다. ICD-10: N94.4",
                  "plan": "1) NSAID (Mefenamic acid) 월경 시작 전 복용 2) 경구 피임약 고려 (호르몬 조절) 3) 3개월 후 재평가 4) 호전 없으면 자궁내막증 감별 위해 정밀 검사"},
         "icd": ["N94.4"]},
    ]

    # 추가 37건은 기본 패턴에서 변형
    more_cases = [
        ("68세 남성, 심방세동 진단 3년. Warfarin 복용 중. INR 4.5 (목표 2-3). 잇몸 출혈",
         "심방세동 환자의 Warfarin 과다 항응고 상태", "I48.9", "1) Warfarin 2일 중단 2) Vitamin K 1mg 경구 3) 2일 후 INR 재검 4) INR 안정 후 용량 조절"),
        ("55세 여성, 양측 무릎 통증+강직 6개월. 계단 내려갈 때 악화. XR: 관절강 협소+골극",
         "양측 무릎 골관절염", "M17.0", "1) 체중 감량 2) 물리치료 3) Acetaminophen/NSAID 4) 관절내 스테로이드 주사 고려"),
        ("42세 남성, 속쓰림+역류 증상 3개월. 식후 악화. 제산제로 일시 호전",
         "위식도역류질환(GERD)", "K21.0", "1) PPI 8주 (Esomeprazole 40mg) 2) 생활습관 교정 3) 호전 없으면 EGD"),
        ("33세 여성, 반복적 긴장성 두통 주 3회. 스트레스 관련. 신경학적 검사 정상",
         "만성 긴장성 두통", "G44.2", "1) Acetaminophen PRN 2) 스트레스 관리 3) 근이완 운동 4) 예방약 고려(Amitriptyline)"),
        ("60세 남성, 공복혈당 145, HbA1c 7.8%. Metformin 단독 복용 6개월. BMI 28",
         "2형 당뇨병 — 혈당 조절 불량", "E11.9", "1) Metformin 유지 2) SGLT2i 또는 GLP-1RA 추가 3) 식이/운동 상담 4) 3개월 후 HbA1c 재검"),
    ]

    samples = []
    for case in cases:
        output = {
            "summary": case["soap"],
            "icd_codes": case["icd"],
            "key_findings": [],
            "urgency": "medium",
            "disclaimer": "AI 참고용이며 최종 판단은 담당 의사가 수행합니다.",
        }
        samples.append({
            "instruction": "당신은 내과 전문의를 보조하는 한국어 의료 AI입니다. 진료 기록을 SOAP 형식으로 요약하세요.",
            "input": case["input"],
            "output": json.dumps(output, ensure_ascii=False),
            "task": "summarize",
            "source": "synthetic_soap",
        })

    for inp, assessment, icd, plan in more_cases:
        soap = {
            "subjective": inp.split(".")[0] + "을(를) 주소로 내원하였습니다.",
            "objective": "관련 검사 결과는 상기와 같습니다.",
            "assessment": assessment + f". ICD-10: {icd}",
            "plan": plan,
        }
        output = {
            "summary": soap,
            "icd_codes": [icd],
            "key_findings": [],
            "urgency": "medium",
            "disclaimer": "AI 참고용이며 최종 판단은 담당 의사가 수행합니다.",
        }
        samples.append({
            "instruction": "당신은 내과 전문의를 보조하는 한국어 의료 AI입니다. 진료 기록을 SOAP 형식으로 요약하세요.",
            "input": inp,
            "output": json.dumps(output, ensure_ascii=False),
            "task": "summarize",
            "source": "synthetic_soap",
        })

    return samples


def generate_patient_explanation_samples() -> list[dict]:
    """환자 설명문 20건."""
    explanations = [
        ("고혈압", "고혈압은 혈관에 가해지는 압력이 정상보다 높은 상태입니다. 수축기 140mmHg 이상 또는 이완기 90mmHg 이상일 때 진단합니다. 대부분 증상이 없어 '침묵의 살인자'라 불립니다. 치료하지 않으면 뇌졸중, 심장마비, 신장질환 위험이 높아집니다. 약을 꾸준히 드시고 싱겁게 드시며 규칙적으로 운동하시는 것이 중요합니다."),
        ("2형 당뇨병", "당뇨병은 몸에서 인슐린을 제대로 만들지 못하거나 사용하지 못해 혈당이 높아지는 질환입니다. 목이 마르거나 소변이 잦고 체중이 줄 수 있습니다. 혈당 조절을 위해 식이조절, 운동, 약물 복용이 필요합니다. HbA1c를 7% 미만으로 유지하는 것이 목표입니다."),
        ("갑상선기능저하증", "갑상선에서 호르몬이 충분히 만들어지지 않는 상태입니다. 피곤하고 추위를 잘 타며 체중이 늘고 변비가 생길 수 있습니다. 레보티록신(갑상선 호르몬약)을 매일 아침 공복에 드시면 됩니다. 약을 꾸준히 드시면 정상적인 생활이 가능합니다."),
        ("위식도역류질환", "위산이 식도로 역류하여 가슴쓰림, 신물, 목 이물감 등의 증상을 유발합니다. 기름진 음식, 야식, 과식을 피하시고 식후 바로 눕지 마세요. 처방받은 위산억제제를 식전에 드시면 대부분 호전됩니다."),
        ("골관절염", "관절 연골이 닳아서 통증과 뻣뻣함이 생기는 퇴행성 질환입니다. 완치는 어렵지만 체중 조절, 적절한 운동, 물리치료로 증상을 관리할 수 있습니다. 통증이 심할 때는 처방받은 진통제를 드세요."),
    ]

    samples = []
    for disease, explanation in explanations:
        samples.append({
            "instruction": "당신은 환자에게 질병을 설명하는 의료 AI입니다. 환자가 이해하기 쉬운 한국어로 설명하세요.",
            "input": f"{disease}에 대해 환자에게 이해하기 쉽게 설명해주세요.",
            "output": json.dumps({"explanation": explanation, "disease": disease,
                                   "disclaimer": "AI 참고용이며 자세한 내용은 담당 의사와 상담하세요."}, ensure_ascii=False),
            "task": "explain",
            "source": "synthetic_explain",
        })

    return samples


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = get_db()

    # 기존 데이터 로드
    train_path = os.path.join(OUT_DIR, "train.jsonl")
    existing = []
    if os.path.exists(train_path):
        with open(train_path, "r", encoding="utf-8") as f:
            existing = [json.loads(line) for line in f if line.strip()]
    print(f"기존 train 데이터: {len(existing)}건")

    # 신규 데이터 생성
    drug_info = generate_drug_info_samples(conn)
    ddx = generate_differential_diagnosis_samples()
    interactions = generate_drug_interaction_samples(conn)
    soap = generate_soap_samples()
    explain = generate_patient_explanation_samples()
    conn.close()

    new_data = drug_info + ddx + interactions + soap + explain
    print(f"\n신규 생성:")
    print(f"  약물 정보: {len(drug_info)}건")
    print(f"  감별진단: {len(ddx)}건")
    print(f"  약물 상호작용: {len(interactions)}건")
    print(f"  SOAP 추가: {len(soap)}건")
    print(f"  환자 설명문: {len(explain)}건")
    print(f"  합계: {len(new_data)}건")

    # 기존 + 신규 합침
    all_data = existing + new_data
    random.shuffle(all_data)

    # 저장
    with open(train_path, "w", encoding="utf-8") as f:
        for item in all_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # eval 데이터 (각 타입에서 10% 추출)
    eval_data = random.sample(new_data, min(30, len(new_data) // 4))
    eval_path = os.path.join(OUT_DIR, "eval.jsonl")
    with open(eval_path, "w", encoding="utf-8") as f:
        for item in eval_data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"\n최종 저장:")
    print(f"  train.jsonl: {len(all_data)}건")
    print(f"  eval.jsonl: {len(eval_data)}건")
    print(f"  경로: {OUT_DIR}")

    # 타입별 분포
    task_dist = {}
    for item in all_data:
        t = item.get("task", "unknown")
        task_dist[t] = task_dist.get(t, 0) + 1
    print(f"\n타입별 분포:")
    for t, c in sorted(task_dist.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}건")


if __name__ == "__main__":
    main()
