# v2.124.19 — 보안 P3 방어심층 (L3·L5·L6)

2026-07-21 | 계획: `docs/01-plan/features/security-review-remediation.plan.md` §5 | 관련: [[project_security_review_20260623]]

2026-06-23 전면 감사에서 🔵LOW로 분류돼 미뤄 두었던 방어심층 항목 중 3건 처리
(L2는 v2.118.2에서 완료). 전부 **저위험·기존 동작 보존**을 전제로 했습니다.

## L3 — LIKE 패턴 이스케이프

`routes/medical_law.py`의 `LIKE ?` 5곳에 사용자 입력이 이스케이프 없이 들어가고
있었습니다. **주입은 아닙니다**(값은 항상 바인딩). 문제는 **검색 거동이 조용히
달라지는 것**입니다 — `%`가 든 질의는 전체 매칭으로 번지고 `_`는 임의 1자로 동작합니다.

- `_like()` 헬퍼 — `\` → `%` → `_` 순으로 리터럴화(역슬래시 우선이라 이중 이스케이프
  사고 없음) + 호출부에 `ESCAPE '\'` 지정.
- 적용: `/ask` 시드 조문 조회, 조문 검색(content·title·law_name), 신고 대상 질병 조회,
  위반 조문 검색.

## L5 — 보안 헤더

앱 자체 헤더가 없어 엣지(nginx/CF)에 의존하고 있었습니다. SSH 터널·직접 접근처럼
엣지를 우회하는 경로에서도 보장되도록 앱에서 부착합니다.

- `X-Content-Type-Options: nosniff`, `Referrer-Policy` — 상시(이미 있으면 유지).
- `Strict-Transport-Security` — **HTTPS 요청에만**(평문 localhost 접근이 깨지지 않도록).
- **CSP + X-Frame-Options** — HTML 응답에만 부착(API JSON 불변).
- `setdefault`를 써서 **엣지가 이미 보낸 값은 덮지 않습니다**(라이브 확인:
  X-Frame-Options·Referrer-Policy는 기존 값 유지, CSP만 신규 추가).

⚠️ **CSP 강도의 한계를 정직하게 기록합니다.** 콘솔이 인라인 핸들러(`onclick` 111곳)·
인라인 스타일(567곳)·CDN Chart.js를 쓰기 때문에 `script-src`/`style-src`에
`'unsafe-inline'`을 허용해야 합니다. 그래도 `frame-ancestors 'none'`(클릭재킹)·
`object-src 'none'`·`base-uri 'self'`는 실효가 있어 남겼습니다. 완전 강화는
**인라인 제거가 선행 과제**입니다. config `security_headers_enabled=false`로 비활성 가능.

## L6 — 프롬프트 문자 캡

전역 20MB 바디 제한만 있어, 인증된 호출자가 수 MB 프롬프트로 단일 워커를 장시간
점유할 수 있었습니다.

- `/api/chat`·`/api/chat/enhanced`·`/api/generate`에 **필드 합산 20만자** 캡 →
  초과 시 413. config `max_prompt_chars`(0=비활성).
- **여유 있는 값입니다**: 전역 `num_ctx=8192`로 실제 활용 가능한 입력은 ~30KB 수준이라,
  20만자는 정상 사용을 막지 않습니다.
- **`/v1`(OpenAI 호환)은 영향 없습니다** — Ollama에 직접 붙어 이 라우트를 거치지
  않습니다(OpenPencil의 ~27KB 시스템 프롬프트 무관).

### 🐞 라이브 검증이 잡은 패치 누락

첫 배포 후 25만자 프롬프트가 **통과**했습니다. 원인은 패치가 문자열 매칭 첫 지점인
`/api/chat/enhanced`에만 들어가고 **본 라우트 `/api/chat`은 무방비**였던 것.
라이브 확인을 하지 않았으면 "적용했다"고 오인할 뻔했습니다 — 두 경로 모두 적용 후
재검증(둘 다 413).

## 검증

- 회귀 `tests/test_security_hardening.py` **5**(LIKE 이스케이프 4종·캡 경계·합산·
  비활성화·기본값 여유) + 관련 계열 **121 passed**.
- 라이브: 보안 헤더 부착 확인(HTML=CSP+XFO, JSON=nosniff만) · `/api/chat` 25만자 →
  413 · **정상 채팅 200** · `/medical-law/ask` 정상 응답 · health 200 · NRestarts 0.

## 잔여

- **L1**(`str(e)` 응답 ~59곳 → 제네릭 에러바디): 광범위 변경이라 별도 세션 권장.
- **L4**(CSRF 빈 바디 토큰 강제): API 클라이언트(HIS·ERP·트윈) 영향 검토가 선행돼야
  해 보류. 현재 SameSite=Lax로 대부분 완화된 상태입니다.
- P2 신중(M3 테넌트키 해싱·M4 medical_bp fail-closed·M5 잡 IDOR)은 기존대로 협의 대기.

---

## 함께 처리된 백로그 운영 항목 2건

같은 저우선 백로그 정리에서 나온 항목이라 이 릴리즈에 함께 기록합니다.

### judge 자기일관성 주간 cron 등록

`scripts/eval_judge_consistency.py`가 "주 1회 권장"으로 만들어져 있었으나 스케줄에
올라가 있지 않았습니다. 실검증 후 등록했습니다.

- 검증 실행: core 5문항 × R=4, **2.5분** 소요. 평균 flip rate **25.0%**·overall std 0.173.
  가장 불안정한 항목은 여전히 `accuracy.conditions_kept`(5문항 중 3개에서 flip) —
  기존 관측과 일치하며 체크 정의 개선 대상으로 남습니다.
- cron: **일요일 04:30**(야간 배치 03:30과 분리) → `logs/eval_judge_consistency.log`.

### 수집보고서에 품질 종합 지수 합류

`core/collect_log.build_report`가 정본 데이터 수집(입력)만 담고 있어, 답변 품질(출력)은
별도 화면에서만 보였습니다. 보고서에 `quality_index` 블록과 요약문 말미 `품질 지수
91.1(A)` 표기를 추가해 **한 보고서에서 입력·출력을 함께** 보도록 했습니다.
미측정·실패 시 `None`으로 비차단(수집 보고 자체를 막지 않음).
