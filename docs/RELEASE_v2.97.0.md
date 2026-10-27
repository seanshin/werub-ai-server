# Release v2.97.0 — DUR 7유형 확장 (식약처 오픈 API)

**2026-06-12** | 이전: [v2.96.1](RELEASE_v2.96.1.md)

## 요약

DUR을 병용금기 단일 유형에서 **7유형 정본**으로 확장. 사용자가 식약처 DUR성분정보
오픈 API(data.go.kr, 승인 계정)를 제공 → 파일 없이 API로 전량 수집해 적재.

| 유형 | 건수 | 비고 |
|------|------|------|
| 병용금기 | 1,816 | API 최신(기존 xls 1,742 갱신) → dur_interaction |
| 연령금기 | 230 | AGE_BASE → dur_single |
| 임부금기 | 1,433 | GRADE |
| 용량주의 | 708 | MAX_QTY |
| 투여기간주의 | 98 | MAX_DOSAGE_TERM |
| 노인주의 | 112 | — |
| 효능군중복 | 405 | EFFECT_CODE/SERS_NAME |

## 변경 사항

### core/dur_api.py (신규)
- 식약처 DUR성분정보 API 클라이언트(`1471000/DURIrdntInfoService03`, REST/JSON, 7 오퍼레이션, 페이지네이션 전량수집).
- **서비스키는 시크릿** — 코드 하드코딩 없이 호출 시 인자/env `DUR_API_KEY` 주입.

### core/dur.py 확장
- `dur_single` 테이블(단일성분 금기/주의 6종) + `import_single`·`import_interactions_api`.
- **`check(drugs, age=None, pregnant=None)` 환자 컨텍스트 인지**:
  - 병용금기(쌍 대조, 기존) + 임부금기(pregnant) + 연령금기(`_age_applies` AGE_BASE 파싱) +
    노인주의(age≥65) + 효능군중복(EFFECT_CODE pairwise) + 용량/투여기간 advisory(정보성).
  - 단일성분 금기 테이블에만 있는 성분도 이름으로 해석(`_resolve_one` dur_single 보강).

### 엔드포인트·오케스트레이션
- `/api/medical/dur/check`에 `age`/`pregnant`(또는 `patient.age/pregnant`) 파라미터.
- `dur_check` 도구에 age/pregnant 추가, 설명을 7유형으로 갱신.
- `/api/medical/drug-analysis`의 DUR 교차검증이 환자 나이/임부 전달.

### 적재 도구
- `scripts/ingest_dur_api.py` — 7 오퍼레이션 전량수집→clear 후 재적재(병용금기→dur_interaction, 나머지→dur_single).

## 적재 절차

```bash
# GPU (서비스키는 env, 미커밋)
DUR_API_KEY=<승인키> venv/bin/python scripts/ingest_dur_api.py
sudo -n systemctl restart ollama-admin
curl -s http://localhost:8585/api/medical/dur/stats
```

## 검증
- iMac: `pytest tests/test_dur.py` → 14 passed(연령파싱·임부·노인·효능군중복·API 적재).
- API 실측: 7 오퍼레이션 totalCount/필드 확인(병용금기 1,816 등).
- GPU(배포 후): 전량 적재 + check 환자 컨텍스트(임부/연령/노인) 라이브.
