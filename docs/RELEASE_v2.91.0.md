# Release v2.91.0 — HIRA 약가목록(약제급여 상한금액표) fee_codes 적재

**2026-06-12** | 이전: [v2.90.0](RELEASE_v2.90.0.md)

## 요약

심평원 「약제급여목록 및 급여상한금액표」 **2026.6.1판(21,898품목)**을 `fee_codes`에
**DRUG 카테고리**로 적재. 청구 `제품코드 → DRUG → notice_type[DRUG_MATERIAL,
REVIEW_CRITERIA]` 자동해석(방식 ②)이 약가코드에도 활성화되어 ERP 사전심사
검색/평가에서 약제 청구코드를 인식한다.

- **fee_codes**: 7,138(TARIFF) → **29,036**(+DRUG 21,898)
- **코드**: 전부 9자리 제품코드, 고유성 100%(21,898/21,898), 빈 name 0
- **전문/일반**: 전문 20,533 / 일반 1,365

## 변경 사항

### 신규
- `core/hira_drug_parser.py` — **순수 표준 라이브러리** xlsx 파서(openpyxl 불요).
  zipfile + ElementTree 스트리밍으로 ~22K행을 0.8초 파싱. 헤더 텍스트 기반 컬럼
  매핑(컬럼 순서·줄바꿈 변화에 강건). 상한금액·주성분·규격·단위·업체·전문/일반을
  `name` 필드에 패킹(비숫자 상한 '산정불가'는 '원' 없이 라벨 보존).
- `scripts/ingest_hira_drug.py` — fee_codes(DRUG) 적재 CLI. `--dry-run`/`--category`.
  `import_fee_codes`(INSERT OR REPLACE)로 **멱등**(재적재 안전).
- `tests/test_hira_drug_parser.py` — 순수 단위테스트 **12건**(헤더 매핑·레코드 변환·
  name 패킹·fee_rows·판본 추출). xlsx/GPU 불요(iMac 실행 가능).

### 설계 결정
- **RAG 미색인**: 22K 균질 코드표는 구조적 lookup(fee_codes)으로 충분하고, 대형
  ChromaDB 컬렉션의 HNSW `link_lists.bin` 폭주(2026-06-11 인시던트)를 회피하기 위해
  의도적으로 벡터 색인하지 않음. 약가 상세는 `fee_codes.name` LIKE 검색으로 노출.
- **스키마 무변경**: 기존 `fee_codes(code, category, name, updated_at)` 그대로 사용
  (valuepoint 적재와 동일 패턴). 가격은 `name`에 정보성으로 패킹.

## 적재 절차

```bash
# GPU
venv/bin/python scripts/ingest_hira_drug.py ~/drug_list.xlsx --dry-run   # 검증
venv/bin/python scripts/ingest_hira_drug.py ~/drug_list.xlsx             # 적재
curl -s http://localhost:8585/api/notice/fee-codes/stats
curl -s http://localhost:8585/api/notice/fee-codes/645302132             # → DRUG_MATERIAL, REVIEW_CRITERIA
```

코드 변경 없음 → **재기동 불요**(데이터만 적재). 되돌리기 = `DELETE FROM fee_codes WHERE category='DRUG'`.

## 검증
- iMac: `python3 -m pytest tests/test_hira_drug_parser.py` → 12 passed
- 실파일 dry-run: edition=2026-06-01, records=21,898, 전 필드 100% 충실
