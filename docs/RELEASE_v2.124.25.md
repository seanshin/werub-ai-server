# v2.124.25 — 보안 L1: 응답 에러 위생 (무절단 str(e) 500 노출 봉합)

2026-07-21 | 계기: 운영자 "보안 먼저 진행" | 계획: `security-review-remediation.plan.md` §5 L1

## 배경 — L1의 실제 노출원을 측정으로 좁힘

2026-06-23 감사에서 🔵LOW로 분류된 L1(`str(e)` 응답 노출)을 측정 선행으로 재평가:

- **production은 debug=off** → 미처리(raise된) 예외는 이미 Flask 기본 제네릭(스택·경로
  미노출). 즉 "500 내면 스택 샌다"는 아니었습니다.
- **실제 노출원은 명시적 `jsonify({"error": str(e)}), 500`** — 예외를 잡아서 내부 메시지
  (파일 경로·라이브러리 문자열)를 JSON으로 그대로 내보내는 43건. 이게 L1이 LOW인
  이유이자, 유일한 실제 노출입니다.
- **`str(e)[:200]` 절단분은 계획서가 안전 처리로 분류** → 유지(정합).

## 조치 — 무절단 str(e) 500을 0으로

- **`core/errors.internal_error(exc, context)`** — 500 응답을 클라이언트엔 제네릭
  (`{"error": "internal server error"}`)으로, 상세는 **서버 로그에만**(logger.warning).
- **정확 패턴 33건 일괄 교체**(10개 파일: coding·chat·game·gateway·image·
  medical_training·ollama·novel·training·system) → `return internal_error(e)` + import.
- **변형 6건 개별 처리**: `transcribe_failed`의 detail(str(e)) 제거(위 logger.error 유지)·
  `{"ok": False, "error": str(e)}` 제네릭+로깅·`f"LLM error: {str(e)}"` 3건은 카테고리
  ("LLM 처리 중 오류") 유지하고 str(e) 제거·novel의 source_id 동반 응답은 error만 제네릭.
- **400대(사용자 입력 검증 메시지)는 그대로** — 유용하고 내부 노출이 아님.

## 검증

- 레포 전체 무절단 str(e) 500 노출 **0건**(grep + 테스트 가드).
- 회귀 `tests/test_error_hygiene.py` 2 — 헬퍼 제네릭 바디(내부 상세 미노출)·**레포 전체
  가드**(무절단 str(e) 500이 재등장하면 CI 실패). README 버전 규칙과 같은 재발 방지 클래스.
- 라이브: app import OK · health 200 · NRestarts 0 · 400 검증 메시지는 정상 유지
  (`text is required` 등).

## 잔여

- **L4**(CSRF 빈 바디 토큰 강제): API 클라이언트(HIS·ERP·트윈) 영향 검토 선행 필요 →
  협의 후 착수.
- P2 신중(M3 테넌트키 해싱·M4 medical_bp fail-closed·M5 잡 IDOR)은 기존대로 협의 대기.
- `str(e)[:200]` 절단분(counseling·korean·medical_advanced)은 의도적 방어라 유지 —
  완전 통일을 원하면 별도로 제네릭화 가능(위험도 낮아 보류).
