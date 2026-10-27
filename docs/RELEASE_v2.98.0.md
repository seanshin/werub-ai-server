# Release v2.98.0 — 의약품개요정보(e약은요) 환자용 정보 정본

**2026-06-12** | 이전: [v2.97.1](RELEASE_v2.97.1.md)

## 요약

식약처 **의약품개요정보(e약은요)** 오픈 API를 적재해, 약 이름으로 **환자 친화 정본 정보**
(효능·사용법·주의사항·상호작용·부작용·보관법)를 조회한다. "이 약 어떻게 먹어요? 부작용은?"
류 질의를 LLM 환각 없이 정본으로 답변(자문 보조, 최종 판단 의사·약사).

## 변경 사항

### core/drug_info.py + drug_info.db (신규)
- `drug_easy_info`(item_seq PK, 4,806품목): name·maker·efficacy·usage·warn·precaution·
  interaction·side_effect·storage·image·bizrno·open_de·update_de.
- `lookup(query)` — 품목일련번호/제품명(정확→부분일치) → 환자용 정보(빈 필드 생략).
- `stats()`, `import_api_items`(item_seq PK 멱등, clear 후 재적재).

### 엔드포인트 (routes/medical_advanced.py)
- `POST /api/medical/drug-info`(`{"query": 제품명/품목코드}`) · `GET /api/medical/drug-info/<item_seq>` · `/stats`.

### 오케스트레이션
- `drug_info` 도구(config/tools.json 27→28) → 자연어 "이 약 효능/복용법/부작용?" 자동 조회.

### 인프라
- `core/dur_api.fetch_all`에 `base` 파라미터 일반화 → e약은요 등 다른 data.go.kr 서비스 재사용.
  item 래퍼 유무 모두 처리(DUR=`.item` 래퍼 / e약은요=직접 dict).
- `scripts/ingest_drug_info.py` — 서비스키 시크릿(env `DUR_API_KEY`/인자, 미커밋).

## 적재 절차

```bash
# GPU
DUR_API_KEY=<승인키> venv/bin/python scripts/ingest_drug_info.py
sudo -n systemctl restart ollama-admin
curl -s http://localhost:8585/api/medical/drug-info -d '{"query":"타이레놀"}'
```

## 검증
- iMac: `pytest tests/test_drug_info.py` → 6 passed.
- API 실측: getDrbEasyDrugList total 4,806, 필드(efcyQesitm/useMethodQesitm/seQesitm 등) 확인.
- GPU(배포 후): 4,806 적재, drug-info lookup + drug_info 도구 라우팅.
