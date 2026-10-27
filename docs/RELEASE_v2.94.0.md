# Release v2.94.0 — DUR 병용금기 (식약처 정본 데이터)

**2026-06-12** | 이전: [v2.93.1](RELEASE_v2.93.1.md)

## 요약

식약처 **DUR(의약품 안전사용서비스) 병용금기** 공개 데이터를 적재해, 처방 약물의
병용금기(약물상호작용 금기)를 **정본 데이터로 점검**한다(기존 LLM 시드 추측이 아님).
"권위 DUR 복제는 비효과적, 보강 레이어가 효과적"이라는 직전 전략 논의에서 사용자가
식약처 정본 데이터를 제공 → 정규화 풀세트 + 체크 API로 구축.

## 변경 사항

### core/dur.py + dur.db (신규)
- `dur_interaction` — Ingr 파일의 **성분 pair 병용금기 룰 1,742건**(성분A↔성분B + 금기내용·등급·고시일자). 권위 룰셋.
- `dur_product` — Item 파일(406,104행)을 **품목→DUR성분코드 슬림 색인**으로 정규화(품목일련번호 dedup). 제품 식별 해석용.
- `check(drugs)` — 약물 리스트를 DUR성분코드로 해석(성분명/D000코드/품목일련번호/품목명) 후 모든 쌍을 병용금기 대조.
- `drug_profile(drug)` — 단일 약물의 병용금기 상대 성분 목록.
- `stats()`.

### 엔드포인트 (routes/medical_advanced.py)
- `POST /api/medical/dur/check` — `{"drugs":[...]}` → 병용금기 경고+사유. **비식별**(코드/성분명만, 환자정보 미수신), 최대 50종.
- `POST /api/medical/dur/drug` · `GET /api/medical/dur/drug/<code>` — 단일 약물 병용금기 프로파일.
- `GET /api/medical/dur/stats`.

### 오케스트레이션
- `dur_check` 도구 등록(config/tools.json 25→26) → qwen3:8b가 "이 약들 같이 처방해도 돼?" 류를 자동 점검.

### 안전·범위
- **권위 = 식약처 정본**. 본 서비스는 미러 + 자문 보조이며 **실시간 차단용이 아니다**(disclaimer 강제, 최종 판단 처방의·약사).
- 범위 = **병용금기**(제공 데이터). 연령/임부/용량/투여기간 금기는 별도 데이터셋 영역(미적재) — 데이터 확보 시 동일 방식 확장.
- xls(실제 xlsx) 파싱 = `iter_xlsx_records`에 `required_field` 일반화.

## 적재 절차

```bash
# GPU
venv/bin/python scripts/ingest_dur.py --ingr ~/DurIngr.xls --item ~/DurItem.xls
sudo -n systemctl restart ollama-admin   # 엔드포인트 신규 → 재기동
curl -s http://localhost:8585/api/medical/dur/stats
```

## 검증
- iMac: `pytest tests/test_dur.py` → 9 passed. Ingr 실데이터 1,742 룰 적재, 파록세틴+셀레길린→세로토닌 증후군 병용금기 정확 탐지.
- GPU(배포 후): Item 406K → dur_product dedup 적재, dur/check 라이브 + 오케스트레이터 라우팅.
