# Release v2.93.1 — 대체조제 후보 cheaper 플래그 (장려금 정확성)

**2026-06-12** | 이전: [v2.93.0](RELEASE_v2.93.0.md)

## 배경

v2.93.0 라이브 점검 중, 대체조제 질의에서 LLM이 **더 비싼** 대체약(60원)을 질의약(59원)
대비 "저렴"하다고 잘못 표현. 저가약 대체조제 **장려금은 더 저렴한 약으로 대체할 때만** 지급되므로,
질의약이 이미 최저가인 경우 대체후보를 "절감 기회"로 제시하면 오해를 부른다.

## 변경 사항

- `core/drug_substitution.lookup`: 각 대체 후보에 **`cheaper`**(질의약보다 저렴 = 장려금 대상)
  플래그 추가 + 응답에 **`cheaper_alternatives_count`**. 양쪽 가격이 있을 때만 판정.
- `/api/notice/diagnose`의 `substitution`에도 `cheaper_alternatives_count` 동반.
- `drug_substitution` 도구 설명에 cheaper 플래그 명시 → LLM이 절감 가능 여부를 정확히 표현.
- 회귀 +1 (`test_lookup_pricier_alternative_not_cheaper`): 질의약이 최저가면 cheaper=False·count 0.

## 검증
- iMac: `pytest tests/test_drug_substitution.py` → 8 passed.
- GPU(배포 후): 재기동 + tools reload, lookup `cheaper`/`cheaper_alternatives_count` 라이브 확인.
