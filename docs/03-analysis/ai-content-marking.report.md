# PDCA 완료 보고서 — AI 생성물 표시(마킹) + 판별

> 2026-06-07 | feature: ai-content-marking | v2.77.0 → v2.79.0 (+ web SDK) | 상태: ✅ 완료·배포·라이브 검증

## 1. 배경 / 목표 (Plan)

「인공지능 기본법」 §31(2026-01-22 시행) + 정보통신망법 개정 대응. 본 서버는 ComfyUI로 이미지를 생성·제공하므로 **표시 의무 주체**.
- **목표 A**: 생성 이미지에 "AI 생성" 표시(가시 워터마크 + 기계판독 메타 + C2PA).
- **목표 B**: 임의 이미지가 AI 생성인지 판별(메타/C2PA 판독 + ML).
- 원칙: 외부 API 의존 0(self-hosted), 자동 차단 금지(면책).

## 2. 구현 (Do)

| 단계 | 산출물 | 버전 |
|---|---|---|
| A1/A2 마킹 | `core/content_marking.py`(워터마크+PNG iTXt 메타, idempotent, 실패 시 원본보존), `_apply_marking` 생성 3경로 적용, upload-raw 제외 | v2.77.0 |
| B1 판별(메타) | `POST /api/detect/image` — read_marking + detect_provenance(C2PA/타툴 휴리스틱) | v2.77.0 |
| B2/B3 판별(ML) | `core/ai_image_detector.py`(Organika/sdxl-detector, CPU ~0.1s) + 종합 confidence | v2.78.0 |
| (핫픽스) torch 캡 | ML 로드 시 nlwp 69→~50(set_num_threads) | v2.78.1 |
| A4 C2PA 서명 | `core/c2pa_sign.py`(self-hosted 2단 인증서 체인, `scripts/gen_c2pa_cert.sh`), detect 실판독 보강 | v2.79.0 |
| A3 문서 | `docs/IMAGE_API_GUIDE.md` 마킹/판별 섹션 | v2.79.0 |
| 웹 연동 | `client/web/werub-ai.ts` + README, API 키 발급(main-web) | d41eb0c |

커밋 6건(398d7f6~d41eb0c). 모델: torch 2.10+cu128 / transformers 4.57.6 / c2pa-python 0.32.12.

## 3. 검증 (Check)

- **단위테스트**: `test_content_marking.py`(14) + `test_ai_image_detector.py`(6) + `test_c2pa_sign.py`(5+RT). GPU 24 PASS/1 skip, iMac 22 PASS/3 skip(GPU 전용 분기).
- **라이브 E2E(프로덕션)**: 실제 `/api/image/generate`(flux-schnell)→ComfyUI→S3 → 다운로드 결과 **our_marking marked=True + C2PA active_manifest + trainedAlgorithmicMedia** 확인.
- **외부 경로**: `example.org/ollama/api/detect/image` — no-auth 401, 키 인증 정상.
- **안정성**: 재시작 5회+ graceful drain 무재발, health 200@<10ms, NRestarts=0, nlwp 평탄(detector 사용 워커만 ~50, 임계 90+ 미만).
- **C2PA 활성화**: config mtime 리로드로 재시작 없이 프로덕션 ON.

## 4. 배운 점 (Act / Lessons)

1. **법 해석 정정**: 초기엔 AI기본법 단일 트랙으로 봤으나, 사용자 지적으로 **정보통신망법(플랫폼/게시자) 2번째 트랙** 발견. → 의무 주체 분석이 기능 범위를 좌우. 1차 출처 확인 필수("7월 1일"은 1차 출처 미확정 — 정직 기록).
2. **C2PA 함정 3종**: PKCS#8 키 필수(SEC1 거부) / 서명 인증서 비-self-signed 필수(2단 체인) / `verify_after_sign:false`+`ta_url=NULL`. → 라이브 GPU 탐침으로 단계적 해결.
3. **torch 스레드**: ML 모델 로드가 워커 nlwp를 코어수만큼 증가 → 인시던트 진단 베이스라인 교란. set_num_threads로 부분 완화(OMP 네이티브 풀은 전역 env 필요 = whisper 트레이드오프로 미적용). 정직 기록.
4. **무중단 배포**: config mtime 캐시 무효화로 C2PA 활성화에 재시작 불필요 → 인시던트 리스크 0. 저트래픽 창 + graceful drain 준수.
5. **정확도 정직성**: ML 판별은 오탐/미탐 한계 명시(disclaimer), 확정 신호는 마킹/C2PA 우선, 자동 차단 금지.

## 5. 잔여 (백로그, 선택)

- 영상 마킹(ffmpeg) — image2video 도입 시
- 딥페이크 얼굴조작 전용 모델
- 공인 CA 인증서(공개 트러스트 필요 시) — 현재 self-signed는 'untrusted' 표시(매니페스트 유효)
- detector 별도 프로세스 분리(nlwp 완전 격리) — 현재 임계 미만이라 시급도 낮음
- React 배지 컴포넌트 / OpenAPI 조각 등 추가 연동 산출물

## 결론

AI 생성물 표시 의무(가시 워터마크+메타+C2PA 자동 부착)와 판별(메타/C2PA+ML) 요구사항을 **완결**. 프로덕션 배포·라이브 검증 완료, 메인웹 연동 SDK·API 키 제공. 법 대응 + 외부 연동 준비 완료.
