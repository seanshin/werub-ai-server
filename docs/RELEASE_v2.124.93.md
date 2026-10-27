# v2.124.93 — 상대 감소 가드 전 수집 경로 확장 + 배포 dry-run 교정

> 2026-08-06 · `core/sync_guard.py`(신설)·`hira_mcat_sync`·`hira_drug_sync`·`dur_sync`
> 회귀 +10 (1375 passed / 7 skipped) · 앞선 릴리즈: `docs/RELEASE_v2.124.92.md`

`v2.124.92`가 만든 가드가 **치료재료 한 곳에만** 있었다. 같은 사각이 약가·대체조제·DUR
9소스에 그대로 남아 있어 전 경로로 확장한다.

## 1. `core/sync_guard.py` — 공용 가드 (순수·fail-open)

절대 하한은 **"0에 가까운 응답"만 잡고 "35% 빠진 응답"은 못 잡는다**(08-03 실사고:
91,211 → 59,000이 `MIN_COUNT=10,000`을 넉넉히 통과).

| 함수 | 역할 |
|---|---|
| `drop_too_large(new, current, ratio)` | 현재 적재량 대비 허용 밖 감소인가 |
| `drop_reason(...)` | 사람이 판단할 수 있게 실제 수치를 담은 skip 사유 |
| `max_drop_ratio()` | config `sync_max_drop_ratio`(기본 0.20) 무재배포·범위 클램프 |
| `safe_count(fn)` | 현재 건수 조회 — 실패는 None(가드 미적용) |

**전부 fail-open**이다: 첫 적재·조회 실패·설정 오류는 통과시킨다. 가드가 갱신 자체를
막으면 정본이 낡아 가는 다른 손해가 생긴다.

### 🔑 기준을 메타가 아니라 **현재 적재된 실제 행 수**로

`v2.124.92`의 mcat 구현은 메타(`hira_drug_sync_meta`)의 직전 ok 건수를 봤는데, 메타는
**`source`가 PK**라 skip이 한 번 기록되면 **직전 ok 건수를 잃는다**(그 다음부터 가드가
조용히 꺼진다). 교체 대상 테이블을 직접 세도록 바꿨다.

## 2. 배선 3곳

| 모듈 | 현재 건수 기준 |
|---|---|
| `hira_mcat_sync` | `fee_codes` WHERE `category='MATERIAL'` |
| `hira_drug_sync` | 약제급여목록 → `fee_codes` DRUG · 저가약대체조제 → `drug_substitution` |
| `dur_sync` | kind별 — `interactions`=`dur.stats()['interactions']` · `single`·`split`=`single_warnings[source]`(소스명 = dur_type) · `easy`=`drug_info.stats()['total']` |

라이브 확인: 약제급여목록 21,998 · 저가약대체조제 11,034 · 병용금기/임부금기/서방정분할/
e약은요 전부 정상 조회.

## 3. 🐞 배포 dry-run이 조용히 망가져 있었다

`import app`이 **exit 139(세그멘테이션 오류)**로 죽는다. 확인해 보니:

- 앱 초기화는 **전부 완료**된다(블루프린트·스케줄러·워커 로그가 다 찍힌다)
- `Fatal Python error: Segmentation fault` + **`<no Python frame>`** — 인터프리터 **종료 단계**의 네이티브 크래시
- **`HEAD~1`에서도 동일하게 재현** → 이번 변경과 무관한 환경 문제
- gunicorn은 종료하지 않으므로 **서비스에는 영향 없음**(라이브 health 200 유지)

⚠️ 그런데 배포 절차의 안전장치가 **항상 실패로 보고**하게 된다 — 실제 문법·임포트 오류와
구분이 안 되므로 위험하다.

→ **dry-run을 `python -c "import app, os; os._exit(0)"`로 교정**한다(teardown 우회, 3/3 OK).
임포트 성공 여부는 그대로 검증하면서 종료 크래시에 걸리지 않는다.

## 4. 검증

- 회귀 **1375 passed / 7 skipped**(1365 → +10)
- `test_sync_guard` 12(실사고 재현·경계·fail-open 4종) + mcat 배선 3
- 라이브 health 200 · NRestarts 0 · `fee_codes` MATERIAL **91,377** 유지
