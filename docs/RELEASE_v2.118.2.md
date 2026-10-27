# RELEASE v2.118.2 — 일반 비전 minicpm-v 전환 + VERSION 정합 + 키캐시 즉시폐기

**날짜**: 2026-07-05
**헤드라인**: "가능한 부분들" 일괄 처리 — ①일반 비전 모델 qwen3-vl→minicpm-v 전환(빈 응답 버그
fix) ②app.py VERSION 상수 현재 정합 ③보안 P3-L2(키 검증 캐시 즉시 폐기). 부수: 의료 데이터
스팟체크(정상)·Tier B RAM 증설 기획서.

---

## ① 일반 비전 minicpm-v 전환 (기능 버그 fix)
- **증상**: `POST /api/vision/describe`(일반 이미지 설명)가 **빈 응답**. 근본원인=`VISION_MODEL="qwen3-vl:8b"`가
  이 Ollama(0.30.11)에서 vision 응답 `response`를 빈 문자열로 반환(`done_reason=length`·reasoning형 채널
  미포착). 200이라 기존 err_resp 폴백도 미발동 → **무성 실패**.
- **진단**: minicpm-v(폴백)는 정상("분홍색"). qwen3-vl은 /chat·/generate·think=False 전부 빈 응답 재현.
- **fix**(`routes/vision.py`): `VISION_MODEL="minicpm-v:latest"`(안정 동작)로 전환·`VISION_FALLBACK_MODEL=None`.
  + **빈 응답 502 가드**(`response` 공백 시 무성 반환 대신 명시 에러+로깅). 의료 비전(medgemma)은 무관.
- ⚠️ qwen3-vl은 이 Ollama서 vision 비호환(향후 Ollama 업그레이드/모델 갱신 시 재평가). 텍스트 용도는 무관.

## ② app.py VERSION 정합 (위생)
- `VERSION="2.90.0"`(v2.91 이후 동결) → **`2.118.2`**로 정합해 `/api/version` 미스리딩 제거.
- CHANGELOG는 전체 백필(28버전) 대신 **정본 포인터 브리지 엔트리**(docs/RELEASE_v2.*.md + CLAUDE.md가
  v2.91+ 정본, 최근 4버전 요약). docs-canonical 관례 유지.

## ③ 보안 P3-L2 — 키 검증 캐시 즉시 폐기
- 키 검증 캐시(`_key_cache`, TTL 60s)가 `toggle`/`delete`서 미clear → 폐기/비활성 최대 60s 지연.
  → `clear_key_cache` 주입 후 `delete_key`·`toggle_key`서 즉시 clear(폐기 즉시 반영). create/expiry는 기존 clear.
- ⏸️ **P3 잔여 defer**: L1(str(e) 59곳·대규모)·L3(LIKE ESCAPE·매칭거동)·L4(CSRF·클라 파손위험)·L5(CSP/HSTS·UI
  파손위험)·L6(프롬프트 캡·정상요청 파손위험) = 저가치 or 신중 대상 → 별도 보안 세션 권장.

## 부수
- **의료 데이터 스팟체크**(라이브): fee_codes·drug_master 크로스워크(바라크루드 성분코드487202ATB·ATC J05AF10)·
  drug_substitution·DUR·e약은요·의료법 RAG **전부 정상**(DUR salt-명은 v2.118.1서 봉합).
- **Tier B 기획서**(`docs/01-plan/features/tier-b-ram-upgrade.plan.md`): RAM 증설(128GB→235B급) 탑재
  매트릭스·비용·**선입증 게이트**(투자 전 grounded eval)·증설 후 절차.

## 검증
- 로컬 py_compile(app.py·routes/ollama.py·routes/vision.py). graceful HUP·NRestarts 0·health 200.
- 라이브: `/api/vision/describe` minicpm-v 정상 응답·`/api/version`=2.118.2·키 toggle/delete 캐시 즉시 반영.

## 영향 / 하위호환
- 비전: 일반 describe만 모델 변경(의료 비전·기타 무관). 빈 응답이 502로 명시화(무성 실패 제거).
- VERSION: /api/version 정확도만 개선. 키캐시: 폐기 반영 빨라짐(기존 60s→즉시).
