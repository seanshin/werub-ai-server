# v2.124.84 — predict 3종 첫 측정(meditron 무동작) + DUR 한글 누락 봉합 + 계약 스모크

> 2026-08-04 · `routes/medical.py`·`core/medical_validator.py`·`scripts/smoke_contract.py`
> 회귀 +8 (1291 passed) · 스모크 23/23

## 1. 🔴 `/api/medical/predict/*` 3종 첫 측정 — 하나가 동작하지 않았다

**한 번도 측정된 적이 없던 경로다.** `v2.114.0` A/B도 `/api/chat/enhanced` 파이프라인만 봤다.

| 경로 | 배선 모델 | 결과 |
|---|---|---|
| `readmission` | `med42-8b` | ✅ 동작 · 구조화 정상 |
| `complication` | `med42-8b` | ✅ 동작 · 구조화 정상 |
| **`adverse-drug`** | **`meditron`** | 🔴 **분석 없음 — 4/4 결정적 재현** |

```
프롬프트: 약물 이상반응 위험을 JSON으로만 답하세요(와파린+아미오다론+아스피린, 78세 여성)
meditron: "이상반응의 위험도를 계산하는 과정을 알아보세요."   ← 27자, 분석 0
```

질문에 답하지 않아 JSON 파싱이 실패했고, **`{"response": <입력 에코>}` 폴백이 그대로 나갔다.**
같은 입력에 `qwen2.5:14b`는 1,388자로 `individual_risks`·`duplications`·`monitoring_plan` 등
**7필드**를 채운다.

**처치**: `DOMAIN_MODEL_ROUTING["pharmacology"]` 1순위 `meditron` → `qwen2.5:14b`.

## 2. 🔴 함께 발견 — DUR 연계가 한글을 못 읽었다

```python
drugs = re.findall(r'\b([A-Z][a-z]+(?:_[A-Z][a-z]+)*)\b', text)   # 영문 토큰만
```

**한국 의료 환경 API인데 `와파린`·`파록세틴`이 통째로 빠졌다.** DUR 조회가 빈 목록으로 돌아
0건이 나왔는데, `/api/medical/dur/check`는 같은 입력을 **29ms에 정확히 잡는다.**

`medications` 원본 목록을 그대로 합류시켜 봉합했다 — 해석은 DUR의 `_resolve_one`이
한글명·salt명·품목코드를 처리하므로 **책임을 단일화**했다.

라이브: `파록세틴+셀레길린` → `db_verified_interactions`에 **식약처DUR 정본 병합**(세로토닌성 증후군).

> 📌 **약물 상호작용의 정본은 식약처 DUR이다.** LLM은 그 위에 설명을 붙이는 역할이며,
> 상호작용 판정 자체를 LLM에 맡기지 말 것.

⚠️ 측정 중 내 임상 가정이 하나 틀렸다 — `와파린+아미오다론`이 금기일 거라 봤으나 DUR 정본에는
없다(용량 조절 대상이지 금기 아님). **0건이 맞는 답이었다.**

## 3. 계약 스모크 스위트 신설

### 왜 필요했나 — 회귀 1,291건이 왜 못 잡았나

```
107개 테스트 파일 중 실제 서버·모델 호출: 5개
predict / adverse-drug 테스트 파일: 아예 없음
```

오늘 찾은 결함 5건은 전부 **"코드는 정상인데 답이 쓸모없는"** 클래스라 단위 테스트로는
**원리적으로 잡히지 않는다.**

| 결함 | 성격 | 단위 테스트 |
|---|---|---|
| meditron 무동작 | 모델이 답을 안 함 | ❌ |
| 특수 토큰 누출 | 모델 출력 특성 | ❌ |
| 진료과 오역 | 모델 출력 특성 | ❌ |
| DUR 한글 누락 | 로직 결함이나 **한글 케이스 부재** | ⚠️ |
| 평가 하네스 오염 | 판정 방식 | ❌ |

### `scripts/smoke_contract.py` — 23/23 통과

구조화 필드 · 입력 에코 아님 · 특수 토큰 부재 · 금지 용어 부재 · 알려진 정답 케이스 ·
정본 병합 · 빈 응답 아님 · 한국어 비율.

```bash
./venv/bin/python scripts/smoke_contract.py            # 전체
./venv/bin/python scripts/smoke_contract.py --only dur # 일부
```

GPU를 쓰므로 **CI가 아니라 배포 전·모델 변경 시·주 1회**.

> ⚠️ **새 결함을 찾아주지 않는다. 아는 결함의 재발만 막는다.**

### 🐞 체크 자체도 과하면 안 된다

첫 구현에서 `"본문에 제15조 인용"`을 요구했다가 실패했는데, **답변은 오히려 정확했다** —
시행규칙 제15조 원문이 `"법 제22조제2항에 따른…"`이라 조문을 정확히 인용하면 본문에는
제22조가 나온다. **체크가 틀린 것이었고**, 본문 또는 `citations`를 함께 보도록 고쳤다.
(그 답변은 9개 보존기간을 전부 맞히고 제15조① 단서까지 자발적으로 포함했다.)

## 4. ⚠️ 미처치 — predict 3종의 입력 계약이 서로 다르다

```
readmission   : {patient_data: {diagnosis, lab_results, ...}}   ← 래퍼
complication  : {diagnosis, patient_info: {...}}                ← 최상위
adverse-drug  : {medications: [...], patient_info: {...}}       ← 배열
```

같은 계열인데 셋 다 다르다. 첫 측정에서 두 개를 틀릴 만큼 헷갈린다.

## 5. 테스트 교훈

`sys.modules` 패치는 대상이 **이미 임포트돼 있으면 패키지 속성 해석으로 우회**돼
**전체 스위트에서만 실패**한다(단독 실행은 통과). 실제 함수를 직접 패치해야 한다.

## 6. 검증

`tests/test_drug_interaction_korean.py` **8** 신규 · 전체 **1291 passed / 7 skipped** ·
계약 스모크 **23/23**.
