# 세션 작업 보고서 — 2026-06-10

> 범위: GPU `84e96c6` → `79a5ff0` (커밋 13건, 8파일 +853/-5)
> 주제: **ERP 사전심사 통합(검색·평가)** + **HIS 연동(키 로테이션·PHI 비저장)** 두 외부 통합을 끝까지 마무리 + medical.db 손상 복구
> 라이브: 다회 재시작 모두 health 200, 무중단 절차 준수

---

## 1. ERP 사전심사 통합 (검색 P0 + 평가 P0.5)

ERP가 weve에 **역방향 질의 채널**(공시 검색 + 삭감위험 평가) 제공을 요청(PR #86). 회신→키 발급→색인 처리기→검색→평가까지 한 세션에 가동.

| 단계 | 산출 | 검증 |
|------|------|------|
| 회신 | `ai-prescreening-reply-to-erp.md` (역량평가, Q3 라이브 실측=공시 0건) | — |
| 키 | ERP Bearer `erp-notice-worker`(key_id=12, **무제한**) → 블로커(워커 키 미설정) 해소 | ERP 9건 발신 |
| **색인 처리기** | `core/notice_processor.py` — chunk→bge-m3→`notice_knowledge` 색인, immediate 백그라운드 + nightly 스케줄러(3시 UTC) + 수동 `process-jobs` + `index-status` 폴링 | 9건 색인(0 fail) |
| **P0 검색** | `POST /api/notice/search` — active_only·notice_type·effective_on(진료일 시행여부) 필터 | E2E: type필터·진료일 06-25/07-05 경계 |
| **P0.5 평가** | `POST /api/notice/diagnose` — 진료일 시행 심사기준 RAG → qwen2.5:14b 구조화 평가, 위험밴드 HIGH≥0.70, 근거인용 강제(has_evidence) | E2E: 재진 중복산정 HIGH·근거인용·재검토 |

**핵심 발견(정정)**: `erp-notice-rag` Day1-2(수명주기 API)는 잡 생성만 했고 **Day3 색인 처리기가 미구현** → 9건이 무한 pending. 이번에 처리기 신설로 해소(이전 "검색 인프라 가동" 주장 = 범용 RAG 기준이었음을 ERP에 정정).

**ERP-블록 잔여**: ① 실 고시 본문 immediate push(자동 색인) ② P0.5 섀도 캘리브레이션용 실삭감 라벨(ERP G1 통보서 파서 후) → 위험밴드 임계 보정 + diagnose 프롬프트 일관성 튜닝 → P1 노출. 코드→notice_type 매핑표(엔드포인트는 이미 type 수용).

---

## 2. HIS 연동 (키 로테이션 + PHI 비저장 + RAG 격리)

HIS 5건 요청 처리. **모두 완료**, 양측 ack(HIS 위탁 적법성 근거로 기록).

| # | 항목 | 처리 |
|---|------|------|
| 1 | 키 로테이션(구키 git 노출) | 신키 **병행 발급**(med-ede34c28 tenant `f56918c3-3d` / Bearer `his-2026` id=13 무제한) → HIS 전환 통보 → **구키 폐기**(구 med-bc98 라이브 401, Bearer his id=7 off) |
| 2 | 키 체계 | med=`/api/medical/*` 진료 / Bearer=`/api/chat` LLM / batch=둘다. 구 his_ac54 폐기됨 |
| 3 | SLA | 스트리밍 TTFB 0.14s·14b 89tok/s / 비스트림 summarize **실측 20s**(30s 타임아웃 근접)→스트리밍 권고 |
| 4 | **PHI 비저장** | `medical_phi_store` 토글(기본 False) → consultation 전문 미저장·감사요약 미보존, `/api/chat` store opt-out. **라이브 E2E**: 실호출 후 consultations 0·summary 공란. 기존 98행/103건 파기. **확인서 발급**(서명 가능) |
| 5 | 모델·엔드포인트 명세 | 회신서 표 |
| +α | **RAG 지식베이스 테넌트 격리** | medical_knowledge 검색 5경로 post-filter(`_kb_tenant_ok`) — 타 테넌트 비공개 제외, 공유/레거시 보존(현재 전건 shared=예방) |

**🔧 부수 — medical.db 손상 복구**: PHI purge 중 `medical_consultations` 테이블만 malformed 발견(타 테이블 정상). 비파괴 재구축→교체, **백업 2종 보존**(`.bak-malformed-20260610`·`.malformed-old`). 무손실(tenant3·knowledge156·audit103).

**발견(백로그)**: 의료 RAG 이중 store — 색인=`_get_chroma`(chromadb_data) vs 검색=client.rag(CHROMA_PATH) 분리, client.rag medical_knowledge=0건이라 SQL 폴백(이미 tenant필터) 의존 중. 통합 별도 과제.

---

## 3. 신규 엔드포인트 (5)
- `POST /api/notice/search` — 공시 검색(필터 3종)
- `POST /api/notice/diagnose` — 삭감위험 평가
- `POST /api/notice/process-jobs` — 색인 잡 수동 처리
- `GET /api/notice/<id>/index-status` — 색인 상태 폴링(?client_request_id)
- (하드닝) PHI 비저장 토글 + chat store opt-out + RAG 테넌트 격리

## 4. 운영 메모
- 배포: 매회 commit→push→GPU pull→**full app import 드라이런**→systemctl restart→health/E2E (preload_app=False).
- 보안: 신규 키 시크릿은 문서/메모리 미기록(채팅 1회 전달).
- ⚠️ 오늘 프로덕션 의료/HIS 다회 재시작 — 추가 튜닝(diagnose 프롬프트 등)은 섀도 캘리브레이션 시점에 묶어 반영 권장.

## 5. 다음 세션
- ERP: 실 고시 적재 시작 시 자동 색인 확인 + 코드매핑 연결 / 실삭감 라벨 수령 시 캘리브레이션
- HIS: 의료 일일한도 500 유지/상향 결정
- 백로그: 의료 RAG 이중 store 통합, notice nightly 스케줄러 실가동 로그 확인(다음 03:00 UTC)
