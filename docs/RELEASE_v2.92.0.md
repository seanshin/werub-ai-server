# Release v2.92.0 — 저가약 대체조제 통합 (목록 lookup + 청구요령 RAG)

**2026-06-12** | 이전: [v2.91.0](RELEASE_v2.91.0.md)

## 요약

심평원 **저가약 대체조제** 자료 2건 통합:
1. **「저가약 대체조제 장려금 지급대상 의약품 목록」 2026.5.1판(10,979품목)** →
   notice.db `drug_substitution` 전용 조회 테이블 + lookup API. "이 약이 장려금
   대상인가 + 어떤 동일성분 의약품으로 대체조제 가능한가"를 조회(ERP 사전심사 보조).
2. **「저가약 대체조제 약제비 청구요령」(구형 .hwp)** → `medical_pub_knowledge` RAG 색인.
   구형 바이너리 HWP를 olefile(순수 Python)로 직접 추출(rhwp CLI 불요).

## 변경 사항

### A. 저가약 대체조제 목록 → drug_substitution

- `core/drug_substitution.py` (신규) — notice.db `drug_substitution` 테이블(code PK,
  주성분코드, 대표코드, 주성분명, 제품명, 업체, 상한금액, 전문/일반, 생동인정, 급여정지,
  eligible). `import_substitution`·`lookup`·`stats`.
  - **eligible 판정**: 목록 xlsx 2시트 중 sheet1='단일품목 제외'(대체 후보가 존재하는
    실제 장려금 대상) 멤버십 ∩ 급여정지 아님.
  - **대체 후보(임상안전)**: `lookup(code)`은 **정확한 주성분코드 동일**(성분+함량+제형
    동일) AND **대표코드 상이**(다른 제품) AND eligible 인 의약품을 상한금액 오름차순
    (저가 우선)으로 제시. 주성분코드 앞4+뒤3만 일치하는 광의 그룹(3mg/6mg 등 함량
    혼입 위험)은 쓰지 않음. 최종 판단은 ERP/약사.
- `scripts/ingest_drug_substitution.py` (신규) — sheet0(전체)+sheet1(eligible) 파싱·적재(멱등).
- 엔드포인트 (routes/notice.py): `GET /api/notice/drug-substitution/<code>`(lookup+후보),
  `GET /api/notice/drug-substitution/stats`.

### B. 구형 HWP 추출 + 단일 발간물 색인

- `core/doc_parser._extract_legacy_hwp` (신규) — 구형 바이너리 HWP 5.x(OLE 복합문서)를
  **olefile(순수 Python)** 로 열어 BodyText 스트림 zlib 해제 후 HWPTAG_PARA_TEXT(67)
  추출. **`_hwp_para_text`가 인라인/확장 컨트롤의 8-wchar 폭을 정확히 건너뛰어** 본문
  오염(컨트롤 데이터 누출) 제거. `parse_document`는 olefile→rhwp 폴백 순. rhwp 불요로
  구형 .hwp 자체 처리(기존 _needs_manual 부담 경감).
- `scripts/ingest_pub_file.py` (신규) — 단일 파일(PDF/HWPX/HWP/TXT) → medical_pub_knowledge
  색인(ingest_hira_pubs의 단건판, 멱등 source_file delete-first).

### C. xlsx 파서 일반화

- `core/hira_drug_parser.iter_xlsx_records` (신규) — 헤더 행을 **스캔**(제목·안내가 상단인
  양식 지원)하고 **다중 시트** 선택 가능. `parse_drug_xlsx`도 이를 사용하도록 정리(기존
  약가목록 적재 동작 불변).

### 의존성
- `olefile==0.47` (순수 Python, 컴파일 의존 없음) requirements.txt 추가.

## 적재 절차

```bash
# GPU (olefile 설치 필요)
venv/bin/pip install olefile==0.47
venv/bin/python scripts/ingest_drug_substitution.py ~/substitution.xlsx
venv/bin/python scripts/ingest_pub_file.py ~/substitution_guide.hwp \
  --title "저가약 대체조제 약제비 청구요령" --category 심사기준 --doc-type 청구작성요령
sudo -n systemctl restart ollama-admin   # 엔드포인트 신규 → 재기동 필요
curl -s http://localhost:8585/api/notice/drug-substitution/stats
```

## 검증
- iMac: `pytest tests/test_drug_substitution.py tests/test_doc_parser_hwp.py` → 15 passed
- HWP 추출: 청구요령 .hwp → 5,542자 / 9청크(컨트롤 누출 0)
- 목록 dry-run: 10,979 records, eligible(단일품목제외) 10,429
