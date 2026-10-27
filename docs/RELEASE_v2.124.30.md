# v2.124.30 — 보안·상태 점검 + appServer 키 단절 복구 + Qwen 신모델 검토 (ops·조사, 코드 변경 0)

2026-07-22 | 계기: 운영자 지시 ①Qwen 신모델 검색·검토 ②서버 보안·상태 점검

## 1. 상태 점검 — 전 항목 정상

uptime 47일 · ollama-admin/ollama active · NRestarts 0 · health 200@49ms ·
/api/version 2.124.29 정합 · 디스크 75%(226G 여유) · GPU 11.7/16GB·40°C ·
큐 worker alive·failed 0 · DUR 동기화 9/9 ok · ChromaDB link_lists 최대
128KB(폭주 없음) · 저널 err 3일 0건 · Worker Dead 6/30 이후 재발 0 ·
07-21 fix 2건(HR Law `is_admin_request`·auto-refine NameError) **배포 후 재발 0**.

## 2. 보안 라이브 재검증 — L1~L6 전부 통과

- 외부 무키 → 401(`/api`·`/v1` 양쪽) · localhost+XFF → 401(H1 fail-closed)
- 보안 헤더(L5) 서빙 확인: nosniff·Referrer-Policy·CSP(frame-ancestors
  'none')·XFO · 에러 위생(L1) 검증 메시지만 노출
- sshd 접속 실패 24h 0건 · 8585/11434 외부 차단 · example.org TLS 만료 9/4

## 3. 🔴 발견·조치 — appServer API 키 만료로 라이브 통합 3일 단절

- `appServer` 키(4/20 생성·90일 만료)가 **7/19 01:55 만료** — 마지막 사용이
  만료 42초 전인 활성 통합. 만료는 인증 쿼리 강제(app.py:4546)라 그 시점부터
  단절: **401 온셋 정확 일치**(7/18=0 → 7/19=6,270 → 7/21=12,481건/일,
  UA "node"·터널 경유 `POST /api/chat` 재시도 ~570/h).
- 운영자 결정으로 **1년 연장**(id=1 → 만료 2027-07-22) → 즉시 회복 검증
  (200 + 정상 응답 본문 라이브 확인). 동일 생성일 `hwpX` 키도 만료됐으나
  6/6 이후 휴면=영향 0·방치.
- **교훈**: 만료 설정 키의 임박 알림이 없어 라이브 통합이 조용히 끊김 →
  백로그 후보=`/api/health` 경보에 키 만료 임박(D-7) 합류.

## 4. 🟡 잔여 권장 (운영자·sudo 필요, 레거시 범위)

1. **포트 80 외부 노출**: nginx가 구 `dataset.weruby.co.kr`(PortalLLMServer
   잔재) 서빙 — 실체는 정적 HTML 2개(52K)·백엔드 :8001 사망(502)이라 민감
   노출 없음이나 불필요 표면. 사이트 비활성 또는 라우터 80 차단 권장.
2. **xrdp(3389) active**: 외부 차단 확인(내부 표면만) — 미사용 시 비활성 권장.

## 5. Qwen 신모델 검토 — 도입 해당 없음

- **Qwen3.8-Max**(7/19 프리뷰): 2.4T MoE 멀티모달·**API 전용**·독립 벤치 0·
  오픈웨이트 "곧"(날짜/라이선스 미정) → 외부 API 0 정책+16GB 천장으로 해당
  없음. Qwen4=9월설.
- **Qwen3.7 heavy A/B는 착수 전 중단**: 공식 오픈웨이트 부재(Ollama
  library 404·HF Qwen 조직 없음 — 3.7은 Max API 전용). 검색에 잡히는
  qwen3.7-35b는 전부 커뮤니티 재업로드(abliterated 포함)=출처·라이선스 검증
  불가 → **규제 인벤토리 규율상 A/B 부적격**. 현행 heavy `qwen3.6:35b-a3b`
  유지. **트리거**: Qwen3.8 공식 오픈웨이트 공개 시 grounded A/B(의료법
  N=40·상주 14b/현행 heavy 대비)로 평가.

## 검증

코드 변경 0 — 라이브 조치는 키 만료 연장 1건(API 경유·롤백=만료 재설정).
문서·버전 정합만 커밋.
