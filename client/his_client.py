"""WeRU.B HIS Integration Client — 병원정보시스템 연동 Python SDK

HIS/EMR 시스템에서 WeRU.B Medical AI API를 쉽게 호출하기 위한 클라이언트.
일반 JSON API와 FHIR R4 API 모두 지원합니다.

Usage:
    from client.his_client import HISClient

    client = HISClient("https://example.org/ollama", medical_key="med-xxx")

    # 1. SOAP 요약
    soap = client.summarize("45세 남성, 3일 전부터 흉통...")
    print(soap["summary"])

    # 2. 추론 체인 (5단계 감별진단)
    chain = client.reasoning_chain("55세 여성, 피로감, 체중감소, 목 덩어리")
    print(chain["final_assessment"])

    # 3. 자동 의무기록
    record = client.auto_record("의사: 어디가 불편하세요?\\n환자: 머리가 아파요...")
    print(record["soap"])

    # 4. 약물 상호작용
    check = client.drug_interaction(["Warfarin", "Aspirin"])

    # 5. FHIR DiagnosticReport
    fhir = client.fhir_diagnostic_report("흉통 환자", patient_id="P001")

    # 6. 배치 처리
    results = client.batch_summarize([
        {"id": "1", "text": "환자1 기록"},
        {"id": "2", "text": "환자2 기록"},
    ])
"""

import json
import time
import requests


class HISClient:
    """WeRU.B Medical AI — HIS 연동 클라이언트

    Args:
        base_url: API 서버 URL (예: "https://example.org/ollama")
        api_key: Bearer 인증 키 (외부 접근 시 필수)
        medical_key: X-Medical-Key (테넌트 인증)
        timeout: 기본 타임아웃 (초)
    """

    def __init__(self, base_url="http://localhost:8585", api_key=None,
                 medical_key=None, timeout=120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"
        if medical_key:
            self.session.headers["X-Medical-Key"] = medical_key

    def _post(self, path, data=None, timeout=None, **kwargs):
        r = self.session.post(
            f"{self.base_url}{path}",
            json=data, timeout=timeout or self.timeout, **kwargs)
        r.raise_for_status()
        return r.json()

    def _get(self, path, **kwargs):
        r = self.session.get(
            f"{self.base_url}{path}",
            timeout=self.timeout, **kwargs)
        r.raise_for_status()
        return r.json()

    # =======================================================================
    # 기본 의료 API
    # =======================================================================

    def summarize(self, text, format="soap", specialty=None, model=None,
                  split_soap=True):
        """진료 기록 SOAP 요약

        Args:
            text: 진료 기록 텍스트
            format: 출력 형식 (soap/brief/detailed)
            specialty: 전문과 (내과/외과/소아과 등)
            model: LLM 모델 (기본: 파인튜닝 모델)
            split_soap: True면 S/O/A/P 병렬 생성 (빠름)

        Returns:
            {"summary": {"subjective":..., "objective":..., "assessment":..., "plan":...},
             "icd_codes": [...], "key_findings": [...], "urgency": "..."}
        """
        payload = {"text": text, "format": format, "split_soap": split_soap}
        if specialty:
            payload["specialty"] = specialty
        if model:
            payload["model"] = model
        return self._post("/api/medical/summarize", payload)

    def analyze(self, text, specialty=None, model=None):
        """감별진단 분석 — 상위 3개 확률순

        Returns:
            [{"diagnosis":..., "icd10":..., "probability":..., "supporting":...}]
        """
        payload = {"text": text}
        if specialty:
            payload["specialty"] = specialty
        if model:
            payload["model"] = model
        return self._post("/api/medical/analyze", payload)

    def explain(self, text, model=None):
        """환자용 설명문 생성 — 쉬운 한국어

        Returns:
            {"explanation":..., "key_points":[...], "precautions":[...]}
        """
        return self._post("/api/medical/explain", {"text": text, "model": model})

    def draft(self, text, record_type="progress", model=None):
        """의무기록 초안 생성

        Args:
            record_type: progress/admission/discharge/operation/consultation
        """
        return self._post("/api/medical/draft",
                          {"text": text, "type": record_type, "model": model})

    # =======================================================================
    # Phase 4: 고급 의료 AI
    # =======================================================================

    def reasoning_chain(self, patient_text, specialty=None, patient_info=None,
                        model=None):
        """5단계 추론 체인 — 증상→감별→순위화→검사→평가

        Args:
            patient_text: 환자 호소 텍스트
            specialty: 전문과
            patient_info: {"age": 45, "sex": "M", "conditions": [...]}

        Returns:
            {"chain_id":..., "steps":[...], "final_assessment":{...},
             "recommended_tests":{...}, "total_tokens":..., "total_duration_ms":...}
        """
        payload = {"patient_text": patient_text}
        if specialty:
            payload["specialty"] = specialty
        if patient_info:
            payload["patient_info"] = patient_info
        if model:
            payload["model"] = model
        return self._post("/api/medical/reasoning/chain", payload, timeout=300)

    def auto_record(self, conversation, specialty=None, patient_info=None,
                    model=None):
        """진료 대화 → 자동 의무기록 (NER → SOAP + ICD-10 + 처방)

        Args:
            conversation: 의사-환자 대화 텍스트

        Returns:
            {"record_id":..., "entities":{...}, "soap":{...},
             "icd_codes":[...], "prescriptions":[...], "orders":[...]}
        """
        payload = {"conversation": conversation}
        if specialty:
            payload["specialty"] = specialty
        if patient_info:
            payload["patient_info"] = patient_info
        if model:
            payload["model"] = model
        return self._post("/api/medical/auto-record", payload, timeout=180)

    def predict_readmission(self, patient_data, model=None):
        """30일 재입원 위험도 예측 (HOSPITAL Score)

        Args:
            patient_data: 환자 데이터 (진단, 검사, 입원 정보)
        """
        return self._post("/api/medical/predict/readmission",
                          {"patient_data": patient_data, "model": model})

    def predict_complication(self, diagnosis, patient_info=None, model=None):
        """합병증 위험도 예측

        Args:
            diagnosis: 주진단 (예: "제2형 당뇨병")
            patient_info: {"age":..., "conditions":[...], "medications":[...]}
        """
        payload = {"diagnosis": diagnosis}
        if patient_info:
            payload["patient_info"] = patient_info
        if model:
            payload["model"] = model
        return self._post("/api/medical/predict/complication", payload)

    def predict_adverse_drug(self, medications, patient_info=None, model=None):
        """약물 부작용(ADR) 위험도 예측

        Args:
            medications: ["Metformin", "Amlodipine", ...]
        """
        payload = {"medications": medications}
        if patient_info:
            payload["patient_info"] = patient_info
        if model:
            payload["model"] = model
        return self._post("/api/medical/predict/adverse-drug", payload)

    # =======================================================================
    # 의료 코드 + 약물
    # =======================================================================

    def search_kcd(self, query):
        """KCD(ICD-10) 질병코드 검색"""
        return self._post("/api/medical/code/kcd", {"query": query})

    def search_edi(self, query):
        """EDI 수가코드 검색"""
        return self._post("/api/medical/code/edi", {"query": query})

    def code_mapping(self, kcd_code):
        """진단코드 → 검사/약물 자동 매핑"""
        return self._post("/api/medical/code/mapping", {"kcd_code": kcd_code})

    def drug_interaction(self, drugs):
        """약물 상호작용 검사

        Args:
            drugs: ["Warfarin", "Aspirin"] 약물명 리스트
        """
        return self._post("/api/medical/drug-interactions", {"drugs": drugs})

    # =======================================================================
    # 배치 처리
    # =======================================================================

    def batch_summarize(self, records, model=None, specialty=None):
        """배치 SOAP 요약 (최대 20건)

        Args:
            records: [{"id": "1", "text": "진료기록"}, ...]

        Returns:
            [{"id": "1", "result": {...}}, ...]
        """
        payload = {"action": "summarize", "records": records}
        if model:
            payload["model"] = model
        if specialty:
            payload["specialty"] = specialty
        return self._post("/api/medical/batch", payload, timeout=300)

    # =======================================================================
    # FHIR R4 API
    # =======================================================================

    def fhir_diagnostic_report(self, patient_text, patient_id=None,
                                encounter_id=None, specialty=None, model=None):
        """추론 체인 → FHIR DiagnosticReport Bundle

        Returns:
            FHIR Bundle {resourceType: "Bundle", entry: [DiagnosticReport, Observations, ServiceRequests]}
        """
        payload = {"patient_text": patient_text}
        if patient_id:
            payload["patient_id"] = patient_id
        if encounter_id:
            payload["encounter_id"] = encounter_id
        if specialty:
            payload["specialty"] = specialty
        if model:
            payload["model"] = model
        return self._post("/api/fhir/DiagnosticReport", payload, timeout=300)

    def fhir_document_reference(self, conversation, patient_id=None,
                                 encounter_id=None, specialty=None, model=None):
        """자동 의무기록 → FHIR DocumentReference Bundle

        Returns:
            FHIR Bundle {entry: [DocumentReference, MedicationRequests]}
        """
        payload = {"conversation": conversation}
        if patient_id:
            payload["patient_id"] = patient_id
        if encounter_id:
            payload["encounter_id"] = encounter_id
        if specialty:
            payload["specialty"] = specialty
        if model:
            payload["model"] = model
        return self._post("/api/fhir/DocumentReference", payload, timeout=180)

    def fhir_risk_assessment(self, pred_type, patient_id=None, **kwargs):
        """예측 분석 → FHIR RiskAssessment

        Args:
            pred_type: "readmission" | "complication" | "adverse-drug"
            patient_data: (readmission) 환자 데이터
            diagnosis: (complication) 주진단
            medications: (adverse-drug) 약물 목록
        """
        payload = {"type": pred_type}
        if patient_id:
            payload["patient_id"] = patient_id
        payload.update(kwargs)
        return self._post("/api/fhir/RiskAssessment", payload)

    def fhir_drug_check(self, medications, patient_id=None):
        """약물 상호작용 → FHIR Observation Bundle"""
        payload = {"medications": medications}
        if patient_id:
            payload["patient_id"] = patient_id
        return self._post("/api/fhir/Observation/drug-check", payload)

    def fhir_condition_search(self, query, patient_id=None):
        """KCD 검색 → FHIR Condition Bundle"""
        payload = {"query": query}
        if patient_id:
            payload["patient_id"] = patient_id
        return self._post("/api/fhir/Condition/search", payload)

    def fhir_metadata(self):
        """서버 CapabilityStatement 조회"""
        return self._get("/api/fhir/metadata")

    # =======================================================================
    # 유틸리티
    # =======================================================================

    def status(self):
        """서버 상태 조회"""
        return self._get("/api/status")

    def gpu_status(self):
        """GPU 스케줄러 상태"""
        return self._get("/api/gpu/scheduler")

    def version(self):
        """서버 버전"""
        return self._get("/api/version")
