# v2.125.65 — 표류 관측 로그 위양성·중복 제거(`_cacheable`)

기준 v2.125.64 · 결함 클래스 #2(탐지기는 양방향으로 틀린다) · 발견 `docs/01-plan/references/writing-draft-drift-2026-09-30.md` §6

## 결함

`[korean-filter] CJK 표류 입력` 관측 로그(v2.125.53)는 `force_korean`에 들어온 입력을 본다. 그런데
`routes/writing._cacheable`은 **출력이 아니라 캐시 여부 판정**에 `force_korean`을 쓰고, 모든 writing 생성에서 돈다 →

- **위양성**: ja·zh 번역은 가나·한자가 정답인데 표류로 기록 — 배포 게이트마다 `writing:121 share=0.929 len=14`
  (`translate-language-fidelity-ja`, 09-29 게이트 9회 전부).
- **중복**: 한국어 경로는 호출자의 필터(`writing:263` 등)가 같은 표류를 한 번 더 기록.

표류 추적이 이 로그를 caller별로 세므로(사전 등록 문서들), 위양성이 추적 수치를 부풀렸다.

## 처치

- `force_korean(text, observe=True)` — `observe=False`는 **로그만 끈다**(반환값 동일). 판정 전용 호출에만 쓴다.
- `_cacheable`과 측정 스크립트(`scripts/writing_drift_measure.py`의 출처 식별 재필터)에 적용.
- ⚠️ 사용자에게 나가는 출력을 거르는 곳에는 쓰지 않는다(소실이 다시 안 보이게 된다) — 주석에 명시.

## 검증

- 신규 2(반환값 동일·로그 없음 / ja 번역 캐시 판정 무로그·판정 불변) — **구 코드 2/2 실패**. 전체 **2376 passed**.
