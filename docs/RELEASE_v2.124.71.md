# v2.124.71 — `.gitignore` DB 정책 default-deny 전환 (민감 DB 6종 누락 봉합)

> 2026-08-04 · 보안 점검 중 발견 · 회귀 4건 · **유출 없음**

## 발견

`counseling.db`가 `git status`에 `??`(미추적·미무시)로 떠 있었다. 추적하면 **상담 대화·위기 감지 기록**이 올라간다.

원인은 **`.gitignore`가 열거(allow-list) 방식**이었던 것이다 — 무시할 DB를 하나씩 적다 보니 새 DB가 생길 때마다 수동 추가가 필요했고, **6종이 누락**돼 있었다:

| 누락 DB | 민감도 |
|---------|--------|
| `counseling.db` | **상담 대화 · 위기 키워드 감지** |
| `meeting.db` | **회의·강의 STT 전사** |
| `medical_law.db` | 의료법 질의 이력(`law_queries` question/answer) |
| `medical_cli.db` · `orchestrator.db` · `rag_fts.db` | 운영 이력 · 문서 본문 인덱스 |

**유출은 없다** — 커밋 이력 0건 · 원격 `main`에 부재. 커밋이 로컬(iMac)에서만 이뤄지고 런타임 DB는 GPU에만 있어 우연히 막혀 있었다. 그러나 **GPU에서 `git add -A` 한 번이면 올라갈 상태**였다.

## fix — default-deny

```gitignore
*.db
*.db-shm
*.db-wal
*.sqlite
*.sqlite3
# 시드/참조 DB만 의도적 추적 유지 (갱신 시 `git add -f`)
!clinic_codes.db
!game.db
!hr_law.db
```

**전부 무시하고 시드 DB만 예외**로 뒤집었다. 새 DB는 **자동으로 보호**된다.

검증: 민감 DB 9종 전부 무시 확인 · 시드 3종 추적 유지 확인.

## 회귀 4건

`tests/test_gitignore_db.py`
- 민감 DB 12종 + **아직 없는 DB**(`future_unknown.db`)·중첩 경로도 무시되는가 → default-deny가 실제로 동작하는지
- 시드 3종은 추적 가능한가(예외가 깨지면 갱신이 막힌다)
- `-shm`/`-wal` 부산물
- **추적 중인 `.db`가 시드 3종뿐인가** — 실수로 커밋된 것이 있으면 잡는다

## 교훈

**보안 목록은 열거가 아니라 default-deny로 쓴다.** 2026-06-23 감사(C1/C2)가 `medical.db`·`users.db`를 이력에서 purge했지만, 그 뒤 추가된 DB들은 같은 보호를 못 받았다 — 열거 방식의 구조적 한계다.
