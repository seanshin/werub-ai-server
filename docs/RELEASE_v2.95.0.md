# Release v2.95.0 — 의약품 코드 크로스워크 (약가마스터 표준코드)

**2026-06-12** | 이전: [v2.94.0](RELEASE_v2.94.0.md)

## 요약

HIRA 「약가마스터 의약품표준코드」(CP949 CSV, 305,522행)를 적재해, **표준코드(바코드)
↔ 제품코드(보험) ↔ 품목기준코드 ↔ 대표코드 ↔ 성분명코드 ↔ ATC**를 단일 해석으로
잇는다. HIS가 어떤 코드(스캔 바코드/보험코드 등)를 줘도 의약품 정체성을 해석 →
fee_codes(약가·notice_type)·drug_substitution(대체조제)·DUR(성분명) 연계의 **다리**.

## 핵심 연결

| 약가마스터 컬럼 | 우리 데이터 |
|----------------|-------------|
| 제품코드(개정후) | `fee_codes.code` (HIRA 보험 9자리) |
| 일반명코드(성분명코드, 예 487202ATB) | `drug_substitution.ingr_code` (HIRA 주성분코드) |
| 표준코드(13자리 바코드) | 약국 스캐너 입력 |
| 국제표준코드(ATC) | 약효 분류 |

→ 그동안 코딩 체계가 달라 끊겨 있던 약가/대체조제/DUR 서브시스템이 단일 코드 해석으로 연결.

## 변경 사항

### core/drug_master.py + drug_master 테이블 (notice.db)
- `drug_master`(std_code PK): product_code, item_code, rep_code, ingr_code, atc, name, maker, rx, spec, cancelled. 코드 컬럼 5종 인덱싱.
- `resolve(code)` — 표준/제품/품목기준/대표/성분코드 어느 것이든 매칭 → 제품(품목기준+제품+성분)으로 그룹화, 포장단위 표준코드 목록 동반.
- `stats()`.

### 엔드포인트 (routes/notice.py)
- `POST /api/notice/drug-master/resolve` · `GET /api/notice/drug-master/<code>` — 코드 → 정체성 해석.
- `GET /api/notice/drug-master/stats`.

### 오케스트레이션
- `drug_resolve` 도구(config/tools.json 26→27) → "이 바코드 무슨 약?" 등 자연어 코드 해석.

### 적재 도구
- `scripts/ingest_drug_master.py` — CP949 CSV 스트리밍, 표준코드 PK 멱등(INSERT OR REPLACE).

## 적재 절차

```bash
# GPU
venv/bin/python scripts/ingest_drug_master.py ~/drug_master.csv
sudo -n systemctl restart ollama-admin   # 엔드포인트 신규
curl -s http://localhost:8585/api/notice/drug-master/658700100   # 바라크루드 → 성분코드·ATC
```

## 검증
- iMac: `pytest tests/test_drug_master.py` → 6 passed. 실데이터 부분 적재로 658700100→487202ATB/J05AF10, 표준코드(바코드)→제품코드 양방향 해석 확인.
- GPU(배포 후): 305K 적재, resolve 라이브 + drug_resolve 도구 라우팅.
