# Release v2.79.0 — C2PA 서명(A4) + API 가이드(A3)

> 2026-06-07 | AI 생성물 표시 v2.77~2.78 시리즈 마무리: C2PA Content Credentials 서명 + 문서화

## A4. C2PA Content Credentials 서명 — `core/c2pa_sign.py`

2026 업계표준(OpenAI·Google 채택)인 **C2PA 매니페스트**를 생성 이미지에 서명·임베드.

- **서명 내용**: `c2pa.created` 액션 + `digitalSourceType = trainedAlgorithmicMedia`(생성형 AI) + claim_generator(WeRU.B-AI) + 모델명.
- **인증서**: self-hosted **2단 체인**(자체 Root CA → end-entity 서명 인증서, EKU=emailProtection, EC P-256 PKCS#8). 외부 CA/API 의존 0. 생성 스크립트 `scripts/gen_c2pa_cert.sh`.
- **통합**: `_apply_marking`에서 가시 워터마크 + PNG 메타 **다음에** C2PA 서명(매니페스트가 최종 마킹본을 해시). PNG 한정(파이프라인이 PNG로 정규화).
- **opt-in**: config `ai_marking_c2pa=false` 기본. 인증서 없으면 자동 passthrough(무중단). 활성화: 스크립트로 인증서 생성 → `ai_marking_c2pa=true` + `c2pa_cert_path`/`c2pa_key_path` 설정.
- **판별 보강**: `POST /api/detect/image`가 실제 C2PA 매니페스트를 판독(기존 바이트 휴리스틱 → 실판독), `provenance.c2pa_manifest`(active_manifest/claim_generator) 노출.
- 스레드 안전(네이티브 호출 Lock 직렬화), 서명자 캐시(인증서 변경 시 무효화).

### ⚠️ 트러스트 한계
self-signed 체인이라 판독기는 'untrusted'(트러스트 리스트 미등록)로 표시 — 정상. **매니페스트 존재·서명·변조탐지는 유효**하며 출처(WeRU.B 생성) 주장 목적엔 충분. 공개 신뢰가 필요하면 공인 CA 발급 인증서로 교체(경로만 변경).

### 기술 노트 (c2pa-python 0.32.12)
- 서명 키는 **PKCS#8**(`openssl genpkey`)이어야 함(SEC1 거부). 서명 인증서는 **비 self-signed**(end-entity)여야 함 → 2단 체인 필수.
- `verify_after_sign=false` 설정으로 self-signed 체인 서명 허용. `ta_url`은 NULL(TSA 미사용).

## A3. API 가이드 문서화 — `docs/IMAGE_API_GUIDE.md`
마킹(자동 동작/config 6키) + 판별 API(요청·응답·confidence 판정표·config 5키·면책) 섹션 추가. 외부 커뮤니티/플랫폼 연동용 계약 명세.

## 검증
- `check_syntax` 125 파일 통과. `bash -n gen_c2pa_cert.sh` OK.
- 테스트: `test_c2pa_sign.py`(passthrough/available/empty/round-trip) + 기존 → iMac 22 PASS / 3 skip(c2pa 실서명은 GPU). GPU에서 self-signed 체인 서명→판독 round-trip 검증 완료(서명 43KB, active_manifest + trainedAlgorithmicMedia).

## 배포
- `requirements.txt`에 `c2pa-python==0.32.12` 추가(GPU 설치됨). 비밀키/인증서는 `.gitignore`(secrets/, *.pem, *.key)로 커밋 제외.
- C2PA 기본 OFF → 배포만으로 동작 변화 없음. 활성화는 인증서 생성 + config 결정 후(운영 판단).

## 잔여
- 영상 마킹(ffmpeg, image2video 도입 시) / 딥페이크 얼굴조작 전용 모델 / (선택) 공인 CA 인증서 / detector 별도 프로세스 분리.
