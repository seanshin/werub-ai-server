# WeRU.B AI Server Admin

> **v2.125.41** | RTX 5080 16GB | 627+ API | 43 Models | 36 Blueprint + 64 Core | LLM 품질 관리(종합 지수·검색 품질·judge 일관성·**judge가 못 보는 결정적 축 2종**·실시간 모니터) · task 라우팅 단일화(61 task) · 보안 인증 경계 강화 · **API 표면 전수 감사**(미선언 태그 0·`security` 누락 0·`responses` 없음 0) · **연동 진입점**(`/api/medical/catalog` · `X-API-Version`/`X-API-Contract` 헤더) · **일반 AI 표면 `/api/general/*`**(의료 AI와 분리)

**개발자**: 신현묵 (Shin Hyunmook) · 8508132+seanshin@users.noreply.github.com  
**라이선스**: Proprietary — 상세는 [`LICENSE`](LICENSE)  
**개발자 가이드**: [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) — 아키텍처·**Ollama 대비 개선점 상세**·함정·배포 절차  
**서빙 구성 설계 참고**: [`docs/DESIGN_MODEL_SERVING.md`](docs/DESIGN_MODEL_SERVING.md) — VRAM 예산·상주 정책·적재 비용 **실측 기준선**(증설·재설계 시 여기서 출발)

Ubuntu GPU 서버(RTX 5080) 기반 통합 AI 시스템 — LLM 추론 · 이미지 생성 · 의료 AI · HR 법률 · 게임 NPC · 글쓰기/소설 · 심리 상담 · 회의 분석(STT+화자분리+Vision) · MCP 서버 · **OpenAI 호환 `/v1` 엔드포인트**.

### 최신 릴리즈

| 버전 | 요약 | 문서 |
|------|------|------|
| **v2.125.41** | **서버 재편 동거 검토 + Wasabi→MinIO + ingress 모드화** — HIS/weruby 스택(컨테이너 44·PM2 21)이 같은 박스로 이전, 유입이 Cloudflare Tunnel→Caddy→8585 로컬 직결로. 인증 우회 없음 실증(엣지 경유 401·직접 200) · Wasabi 폐지 → MinIO 전환(공개 호스트 서명·외부 presigned 실측) · `/api/image/s3/list` 무성 실패 제거 · 은퇴 터널 위양성 경보 132건/일 정지 · 강제 리붓 사후 점검(DB 22개·Chroma 14 전부 OK) | [RELEASE](docs/RELEASE_v2.125.41.md) |
| **v2.125.40** | **운영 세션: 커널 -31 재부팅 완결 + 디스크 +31G + 아키텍처 백서 신설** — 재부팅 전 dkms -31 빌드 실측 확인·사후 검증 전 축 OK. 디스크 234→265G(HF 고아·anaconda3 — sana_poc은 라이브 확인으로 보존). 🔴iMac 링크 전체 다운 발견(워커 재시작 전 현장 확인 선행). 구조·설계 백서 18절 신설 | [RELEASE](docs/RELEASE_v2.125.40.md) |
| **v2.125.39** | **401 폴링 폭주 백오프** — 만료 세션 탭이 2시간 5,760건 401(api_activity 창을 28분으로 축소시킨 원인). 대시보드 3면에 5분 백오프+자동 복구 | [RELEASE](docs/RELEASE_v2.125.39.md) |
| **v2.125.38** | **잔여 전수 의사결정 + M6 레이트리밋 + heavy 백업 정리** — 실측 피크 6배+ 임계로 15경로 개별 리밋(STT는 의도적 제외·동결). heavy 백업 태그는 구판 vs 신판 A/B 10/10 동률 확인 후 삭제(23GB) | [RELEASE](docs/RELEASE_v2.125.38.md) |
| **v2.125.37** | **medlaw OCR 폴백 + 최신판 가중치 측정 종결** — 파싱 붕괴 PDF를 deepseek-ocr로 구제(사전 등록 측정 4/4 채택·재현율 0.983·폭주 정리기 동반). 최신판 가중치는 실측 A=2%·B=0으로 기각(기준선 기록) | [RELEASE](docs/RELEASE_v2.125.37.md) |
| **v2.125.36** | **ambiguous 4쌍 판정 종결 + 파싱 붕괴 색인 교체·가드** — 원문 판독으로 보존 3쌍·중복 1쌍 삭제. 🔴한글 2.1% 쓰레기 색인 발견(toUnicode 결손 PDF)→hwpx 교체+`index_files` 가드(≥500자 & 한글<5% 차단·임계는 146파일 전수 실측) | [RELEASE](docs/RELEASE_v2.125.36.md) |
| **v2.125.35** | **medlaw dedup churn 봉쇄** — 08-31 삭제분이 이틀 만에 전량 재색인(known=컬렉션 파생)됨을 확진 → **tombstone**(state)+재삭제 140청크+라이브 실증 `tombstone_skipped:2`. ⚠️테스트가 라이브 state 오염한 2차 사고→conftest 전역 격리 | [RELEASE](docs/RELEASE_v2.125.35.md) |
| **v2.125.34** | **일반 AI 표면 신설(의료와 분리) + HyperCLOVA 커뮤니티 모델 + heavy 제자리 갱신** — `/api/general/*` 4종(모델 allowlist·의료 모델 400)·`hyperclova-seed:1.5b`(Omni-8B는 라이선스 의료 금지+GGUF 불가로 기각)·heavy 35b-a3b 갱신(grounded 프로브 IDENTICAL·백업 태그 롤백 확보) | [RELEASE](docs/RELEASE_v2.125.34.md) |
| **v2.125.33** | **응답 스키마 2차: 호출 함수 추적 + Korean** — `jsonify(변수)` 121건에 한 단계 추적기(누적 dict 패턴 포함) 적용했으나 **8건뿐**(나머지는 추측 거부하고 중단=설계). 자동화 한계선 긋고 Korean 9건 수작업. spellcheck는 500자 초과 시 응답 형태가 갈림을 명시 | [RELEASE](docs/RELEASE_v2.125.33.md) |
| **v2.125.32** | **응답 본문 스키마** — AST로 성공 반환 분류(정적 확정 86 / 불가 121). 자동 78건은 필드명만, 타입은 추론된 114개만. 연동 표면 5건은 core 함수까지 읽어 수동 — `medical-law`에 **`disclaimer` 필드 명시**(스펙에 없으면 화면이 고지를 렌더 안 함) | [RELEASE](docs/RELEASE_v2.125.32.md) |
| **v2.125.31** | **`responses` 131→0 + 고빈도 경로 설명** — AST로 실제 `return …, <code>`와 에러 리터럴 문구까지 추출. description은 `api_activity` 실측 호출량으로 우선순위. `/api/generate`에 raw 통과 계약, `/api/counseling/chat`에 `is_crisis`+1393 명시 | [RELEASE](docs/RELEASE_v2.125.31.md) |
| **v2.125.30** | **연동 표면 설명 보강 + flasgger 판단 정정** — 🔴"description 없음은 도구 동작"이 틀렸다(flasgger는 `---` 앞 본문을 싣는다). 560건은 진짜 공백. 카탈로그 연동 23경로 중 결손 13건 보강 | [RELEASE](docs/RELEASE_v2.125.30.md) |
| **v2.125.29** | **전체 API 표면 감사 + 버전 응답 기능** — 561경로/591 오퍼레이션 전수. 미선언 태그 381→0·`security` 누락 17→0. 🔴`/api/version` 98KB→203B. 🆕 모든 응답에 `X-API-Version`/**`X-API-Contract`**(빌드와 계약을 분리) | [RELEASE](docs/RELEASE_v2.125.29.md) |
| **v2.125.28** | **의료 데이터 카탈로그 + Swagger 배치** — `GET /api/medical/catalog`(16셋·453,081행·서빙 엔드포인트 동반). 의료 API가 26개 태그로 흩어져 발견 지점이 없던 문제를 태그 선언·정렬로 해소(라우트 docstring 미변경) | [RELEASE](docs/RELEASE_v2.125.28.md) |
| **v2.125.27** | **medlaw dedup이 최신 개정분을 지우고 있었다** — 탐지기를 통제군에 걸어 전제 붕괴 확인(전문과 일부개정은 상보 관계인데 한 그룹으로 묶여 최신 개정분이 삭제 대상). 계획 3,017→139청크·`apply=False` 기본 | [RELEASE](docs/RELEASE_v2.125.27.md) |
| **v2.125.26** | **원격 노드 프록시 502가 자기 건전성 지표를 오염** — iMac 미도달 54시간 동안 `/api/mac/*` 502가 31,117건 발생해 `error_rate` 33%·경보 534건. `5xx_upstream` 분리(버리지 않고 다른 이름으로) | [RELEASE](docs/RELEASE_v2.125.26.md) |
| **v2.125.25** | **triage 증상 파싱 오염 봉쇄 + 무성 실패 로깅 이행** — 쉼표 분리 폴백이 폭주 잔해를 증상으로 내보내던 것 처치. P5-1 재시도는 사전 등록 측정으로 **기각**(전환 0/8). v2.125.16이 적은 로깅이 triage에선 미이행이던 것 적발 | [RELEASE](docs/RELEASE_v2.125.25.md) |
| **v2.125.24** | **오케스트레이터 client.call hints 전송 구조 복구** — hints를 받고도 전송 안 하던 것(STT language 무성 ko 낙착의 X7 구조 원인)을 form/body 병합으로. 비-ko 위임 허용은 X10 미검증이라 로컬 강제 유지 | [RELEASE](docs/RELEASE_v2.125.24.md) |
| **v2.125.23** | **mlx_stt 파일명 경로 순회 봉쇄** — iMac 워커 STT가 클라이언트 filename을 tmp 경로에 직결(순회 쓰기 가능·워커 토큰 한정). basename+확장자만 취해 봉쇄. iMac 워커 반영은 운영자 재시작 선행 | [RELEASE](docs/RELEASE_v2.125.23.md) |
| **v2.125.22** | **CORS 로컬 오리진 반사 절 제거** — 터널 연 개발자 브라우저에서 반사 CORS+로컬 bypass 결합 교차 출처 읽기 봉쇄(v2.125.18 ⑦ 이월). 화이트리스트+/v1만 반사 | [RELEASE](docs/RELEASE_v2.125.22.md) |
| **v2.125.21** | **`/api/chat` 스트림 저장분 정화를 문자 단위로** — 실측(N=8)이 확진한 캐시-라이브 분기(코드 571→167자·영어 답변 저장 스킵) 봉합. 클라이언트 raw 계약 불변·혼입 한자 정화 효용 보존 | [RELEASE](docs/RELEASE_v2.125.21.md) |
| **v2.125.20** | **잊힌 작업 3건 마감(X7·X9·X11)** — meeting language 무성 소실(위임 시 비-ko 전부 ko 낙착) 처치·numbers_match 8→11=위양성 축 노이즈로 규명(H2 확정)·문서-코드 불일치 4건 정정. §9 잔여=X10 하나 | [RELEASE](docs/RELEASE_v2.125.20.md) |
| **v2.125.19** | **잊힌 작업 4건 마감** — ERP 공문 확약 API 2종 구현(`/api/notice/changes`·`/api/notice/sync/verify`)·embed 스케줄러 가드 fail-closed·X6 "한국어 필터 소실"은 인과 오류로 정정(raw=계약·고정 테스트)·v2.109.2 blob 주장 부분 허위 정정 | [RELEASE](docs/RELEASE_v2.125.19.md) |
| **v2.125.18** | **미검증 보안 7건 재현·판정 → 확진 3건 처치** — medical 테넌트 플레이스홀더(`hashed:{id}`로 PHI 우회·로테이션 불가)·RAG 컬렉션 격리 우회·fleet 삭제 무-admin. 에이전트 결론 3건은 재현해보니 **부풀려져 있었다**(직접 검증이 유효) | [RELEASE](docs/RELEASE_v2.125.18.md) |
| **v2.125.17** | **보안 — 라이브 재현된 유출 3건 봉합** — 경로 순회 2건(`config.json` 무인증 노출·`SECRET_KEY` 유출→세션 위조)+오케스트레이터 `ai:` allowlist(위조 localhost 권한으로 관리자 API 실행 가능했다) | [RELEASE](docs/RELEASE_v2.125.17.md) |
| **v2.125.16** | **무성 실패 일괄 2차(7지점)** — 릴리즈 노트를 인벤토리로 삼아 1차가 불완전했다. writing이 HTTP 오류를 **브레이커 success로** 세고 chat_memory는 **빈 요약 저장+원본 삭제**(이력 소실) | [RELEASE](docs/RELEASE_v2.125.16.md) |
| **v2.125.15** | **후단·단서 오독 탐지기(E1)+무성 실패 8지점+게이트 커버리지 복구** — judge가 못 보는 축 신설(관측만)·`/tmp` 의존 게이트 이미지가 재부팅으로 사라져 커버리지가 조용히 줄고 있었다(177→173→복구) | [RELEASE](docs/RELEASE_v2.125.15.md) |
| **v2.125.14** | **D2 `repeat_penalty 1.1` 어댑터 핀** — 엔진 기본값 의존 탈피(0.32.10이 기본값을 바꿨다). Modelfile 자체 값 모델은 비핀·조회 실패는 fail-safe·**seed 고정 출력 diff 0** | [RELEASE](docs/RELEASE_v2.125.14.md) |
| **v2.125.12~13** | **D1 router 이관 완료(49곳)** — 직접 Ollama 호출 → `core/model_router` 단일 통로(표 **59 task**). 이관이 드러낸 실결함: `/api/rag/query`가 HTTP 오류를 빈 answer+200으로 흘리고 있었다 | [RELEASE](docs/RELEASE_v2.125.13.md) |
| **v2.125.8~11** | **법령명 오귀속 축 신설·기준선 0/40** — 충실도 judge는 주장 *내용*만 보고 **어느 법률인가는 판정 축에 없다**(오귀속 8건 중 7건이 faith 1.000 통과). 결정적 계산으로 계량 | [RELEASE](docs/RELEASE_v2.125.9.md) |
| **v2.125.10** | **일반 비전 qwen3-vl:8b 복귀** — "빈 응답"의 정체는 비호환이 아니라 **사고 예산**(사고 토큰이 `num_predict`를 먹는다). 한글 문단 OCR 유사도 0.99 vs 0.19 | [RELEASE](docs/RELEASE_v2.125.10.md) |
| **v2.124.26** | **보안 L4 — 세션 Origin 검증(CSRF 방어심층)** — SameSite=Lax 주 방어에 Origin 화이트리스트 이중화(API 클라 영향 0) | [RELEASE](docs/RELEASE_v2.124.26.md) |
| **v2.124.25** | **보안 L1 — 응답 에러 위생** — 무절단 str(e) 500 노출 39건 제네릭화(헬퍼+개별)·레포 가드로 재발 차단 | [RELEASE](docs/RELEASE_v2.124.25.md) |
| **v2.124.24** | **moderation auto-refine NameError 봉합** — 백그라운드 잡이 미정의 함수 호출로 매시간 조용히 실패(HR Law와 동일 클래스, 휴면이라 미발현) | [RELEASE](docs/RELEASE_v2.124.24.md) |
| **v2.124.23** | **성능·SQL 검토 라운드2** — 위성 DB·pragma·ANALYZE 실측(병목 0), 대량 sync 후 WAL 고수위 축소(디스크 위생·비대칭 해소) | [RELEASE](docs/RELEASE_v2.124.23.md) |
| **v2.124.22** | **성능·SQL 검토(측정 선행)** — 병목 0·튜닝 불요(fee_codes 120K에도 0.01ms), auto_purge가 completed_at NULL 잡을 영구 잔존시키던 결함 수정 | [RELEASE](docs/RELEASE_v2.124.22.md) |
| **v2.124.21** | **HR Law 보안 감사·조치** — admin 가드 6곳 NameError로 무동작·테넌트 자가등록·usage fail-open 3건 실증 수정+테넌트 키 해싱(지연 마이그레이션) | [RELEASE](docs/RELEASE_v2.124.21.md) |
| **v2.124.20** | **14b 비결정성 원인 규명(트윈 S6)** — 배칭 가설을 A/B로 기각하고 **KV/프리픽스 캐시 콜드/웜**으로 확정, 워밍업 1회 후 2048토큰 N=6 완전 결정(코드 변경 0) | [RELEASE](docs/RELEASE_v2.124.20.md) |
| **v2.124.19** | **보안 P3 방어심층(L3·L5·L6)** — LIKE 이스케이프·앱 보안 헤더(CSP/HSTS)·프롬프트 20만자 캡. 라이브 검증이 패치 누락 1건 포착 | [RELEASE](docs/RELEASE_v2.124.19.md) |
| **v2.124.18** | **근거 관련성 게이트+출처 노출(트윈 S2/S1)** — relevance가 RRF 순위 점수라 임계가 무효였던 결함 봉합(코사인 게이트로 무의미 입력 5→0건)+sources[] source/version/confidence 노출 | [RELEASE](docs/RELEASE_v2.124.18.md) |
| **v2.124.17** | **검색 슬롯 중복 잠식 봉합** — 고시 판본 간 동일 원문(24%)이 top-K를 잠식해 법령 슬롯이 발간물에 밀리던 현상, 과다 인출→dedup→절단으로 복원(26→29)+id134 재청킹 백로그 종결 | [RELEASE](docs/RELEASE_v2.124.17.md) |
| **v2.124.16** | **주장 분해 정합(폐루프 12)** — 07-21 배치 지수 91.1(A)·환각 0 드릴다운: 근거 메타 진술이 규칙 준수 답변을 감점시키던 구조적 충돌 봉합+주장 언어 드리프트 재추출 | [RELEASE](docs/RELEASE_v2.124.16.md) |
| **v2.124.15** | **치료재료 정본 적재 — 청구 코드 3축 완성** — data.go.kr 활용신청 승인으로 휴면 해제, API 실동작 2건 실측 수정(검색 조건 필수→`itmNm=%` LIKE·page 60 410→세분화 폴백)로 **91,211건 전량 적재**(수가 7,138·약가 21,959·치료재료 91,211) | [RELEASE](docs/RELEASE_v2.124.15.md) |
| **v2.124.14** | **규제 가드 활성화(ops)** — 자체 모델 전 자동 경로 미서빙 구조 보장 완성(라우팅 API 라이브 검증)+bespoke NC 잔존물 제거(4.7GB)+HAI-DEF 하류 고지. AI서버측 규제 트랙 이행 완결 | [RELEASE](docs/RELEASE_v2.124.14.md) |
| **v2.124.13** | **교차검증 백엔드 교체** — bespoke-minicheck(CC BY-NC)→qwen-entail(qwen2.5:7b·Apache-2.0): PoC 12페어 실측 12/12>bespoke 11/12, 프로토콜·지표 스키마 불변(추세 주석). 평가 스택 전체 Apache-2.0화 | [RELEASE](docs/RELEASE_v2.124.13.md) |
| **v2.124.12** | **GET /api/models/routing** — 경로별 서빙 모델·digest 라이브 조회(규제 R2 확장): 실제 라우팅 함수 재사용=가드·슬롯 변경 즉시 반영, 폴링만으로 모델 교체 감지 | [RELEASE](docs/RELEASE_v2.124.12.md) |
| **v2.124.11** | **규칙2 개정·규칙4 확장·캡 절단 마커** — 07-20 배치 폐루프 11: 근거 밖 벌칙 창작 금지(규칙2↔6 긴장 근본 봉합)+반대 방향 총괄 요약 금지+무표시 절단 마커. 재측정 3문항 전부 1.000·환각 0 | [RELEASE](docs/RELEASE_v2.124.11.md) |
| **v2.124.10** | **규제 라우팅 가드+모델 카드 2종** — 식약처 LLM 가이드라인 대응(동일 법인 확정): `medical_pin_public_models`(opt-in) 자동 라우팅서 자체 모델 배제 구조 보장 + werubmedv2·werub-medical:7b 모델 카드(QLoRA 232건·합성 데이터·한계 명시) | [RELEASE](docs/RELEASE_v2.124.10.md) |
| **v2.124.9** | **의료법 벌칙 오귀속 근본 봉합** — 무면허 벌칙 제90조(500만원) 오귀속을 3차 계측 폐루프로 정정(제87조의2 5년/5천만): 규칙 8·9+벌칙 교차참조 주석(`_penalty_xref_note`)+행위 조문 보강(`_act_context_chunks`). judge 위양성을 MC가 잡는 역방향 규율(EVAL_GUIDE §7) | [RELEASE](docs/RELEASE_v2.124.9.md) |
| **v2.124.8** | **의료법 RAG 거리 공간 정정(L2→cosine)+벌칙 역참조** — 본 법령 컬렉션만 L2로 생성돼 score=2cos−1 압축(권위역전·게이트 전멸의 공통 뿌리) → 저장 임베딩 재사용 재구축(12,686청크 9.4s)+처벌 질의 벌칙 조문 결정적 역참조+발간물 문서제목 오귀속 봉합(규칙7). 플래그 6문항 재측정 **전부 1.000·환각 0** | [RELEASE](docs/RELEASE_v2.124.8.md) |
| **v2.124.7** | **결정적 질의 확장** — id175 용어 충돌 검색 갭(방사선→방사성의약품 점령) 조문 표제어 보조검색 봉합(/ask+eval 양쪽), EVAL_GUIDE 거부형 strict 위음성 규율 | [RELEASE](docs/RELEASE_v2.124.7.md) |
| **v2.124.6** | **청크 캡 600→1500+규칙5 열거 확장** — 생성/판정 근거 비대칭(캡 밖 호를 기억으로 완성) 봉합, 플래그 2문항 faith 1.000 회복 | [RELEASE](docs/RELEASE_v2.124.6.md) |
| **v2.124.5** | **서식 오염 필터** — HIRA 진료비확인 서식이 법령을 밀어내던 근거 오염 정화(`_is_form_chunk`, eval+프로덕션 단일 적용) | [RELEASE](docs/RELEASE_v2.124.5.md) |
| **v2.124.4** | **heavy_model qwen3.6:35b-a3b**(grounded A/B at-parity)+iMac 연계 복구+의료영상 보조 추론 사이드카 착수(스캐폴딩) | [RELEASE](docs/RELEASE_v2.124.4.md) |
| **v2.124.3** | **품질 지수 하락 알림** — health perf 합류(임계 80·쿨다운 6h)+**전체 회귀 823/7s**(실패 0) | [RELEASE](docs/RELEASE_v2.124.1.md) |
| **v2.124.2** | **통합 이슈 리스트** — 전 지표 검토 항목(환각·불일치·검색·체크·judge) 심각도순 단일 패널+드릴다운 연결+텍스트 복사 | [RELEASE](docs/RELEASE_v2.124.1.md) |
| **v2.124.1** | **KPI 타일 클릭 상세** — 지표별 모달(정의·판독 규율 상주·질문별 분해 나쁜 값 우선·드릴다운 연결·지수 기여 분해) | [RELEASE](docs/RELEASE_v2.124.1.md) |
| **v2.124.0** | **검색 품질 축+judge 자기일관성** — context precision·근거 충분성(검색 vs 생성 분리, 드릴다운 청크 테이블)+반복 채점 flip rate(채점자 신뢰도 정량화·불안정 항목 특정) | [RELEASE](docs/RELEASE_v2.124.0.md) |
| **v2.123.0** | **품질 종합 지수** — 세밀 지표 결정적 합성(qi-1.0, 0~100·등급 A~D·의료 안전 가중·산식 공개)+배치별 시계열+모니터 히어로/카드 타일. 라이브 **94.0(A)** | [RELEASE](docs/RELEASE_v2.123.0.md) |
| **v2.122.0** | **평가 질의 풀 확대** — core 5+pool 35(의료법 전 영역) 일자 결정적 회전(배치 10문항·7일 풀 커버), 근거 가용성 사전 검증 **35/35**, 문항 관리=JSON 무재배포 | [RELEASE](docs/RELEASE_v2.122.0.md) |
| **v2.121.1** | **의료법 규칙6(근거 밖 파생 창작 금지)** — 환각 플래그를 드릴다운으로 원문 확정("수령"까지 규정된 조문에 전달·구매 창작)→규칙 추가→**0.833→1.000·환각 0/5** 복구. Q4 불일치는 MiniCheck 위음성으로 리뷰 종결 | [RELEASE](docs/RELEASE_v2.121.1.md) |
| **v2.121.0** | **질문별 드릴다운 상세 검토** — 러너 상세 영속화(답변·주장별 판정·차원 사유·체크 12항목)+`/api/eval/{detail,history}`+모니터 행 클릭 모달(judge vs MiniCheck ⚠불일치·체크 칩·근거 발췌·배치 이력) | [RELEASE](docs/RELEASE_v2.121.0.md) |
| **v2.120.3** | **품질 실시간 모니터 새창** — `/eval-monitor` 전용 페이지(KPI 12타일·시계열 4차트 대형·질문별 진단 상시·자동 갱신 5s~5m)+카드 "실시간 모니터 ↗" 버튼 | [RELEASE](docs/RELEASE_v2.120.3.md) |
| **v2.120.2** | **품질 관리 영역 확대** — 세밀 지표 전부 시계열화(배치별 strict·교차 일치·차원별·체크리스트·리뷰 트리거) → 관리 차트 2×2(신뢰성·품질 차원·체크리스트·트리거 막대). 팔레트 계산 검증(CVD/대비 PASS) | [RELEASE](docs/RELEASE_v2.120.2.md) |
| **v2.120.1** | **품질 카드 세밀 지표 표시** — 교차검증 일치·리뷰 트리거·strict·체크리스트 통과 타일+"개선 지점" 빈도 랭킹+질문별 세부 진단 테이블. 첫 배치서 두 세밀 축이 동일 문항(Q5) 지목·`conditions_kept` 3/5 실패 표면화 | [RELEASE](docs/RELEASE_v2.120.1.md) |
| **v2.120.0** | **LLM 평가 세밀화** — MiniCheck 주장 단위 교차검증(한국어 PoC **10/10**·mc_agree/conflicts/strict 지표·청킹 프로토콜)+체크리스트 분해 judge(12 이진 항목·실패=개선 지점)+2025~26 논문/오픈소스 조사(K-HALU=AI-HUB 신청 대기) | [RELEASE](docs/RELEASE_v2.120.0.md) |
| **v2.119.0** | **수집 로그+주기 수집보고서** — 외부 정본 수집(HIRA 게시판·data.go.kr 12소스) 전 시도 이력 로그+주기 보고서(집계·신규판·"점검 필요" 자동 플래그)+retention 관리+Admin 카드. `GET/POST /api/notice/collect/{logs,report,reports,purge}` | [RELEASE](docs/RELEASE_v2.119.0.md) |
| **v2.118.5** | **HIRA 동기화 다중소스 확장** — 저가약 대체조제 **자동 동기화**(월별 게시판→clear 스냅샷, E2E 10,980건)+**치료재료 API 동기화 준비**(청구 코드 3축 완성 대비, data.go.kr 활용신청 대기 휴면)+`sync` source 파라미터 | [RELEASE](docs/RELEASE_v2.118.5.md) |
| **v2.118.4** | **의료 데이터 정기 갱신** — 저가약 대체조제 '26.6월판(2026.6.1시행) **clear 스냅샷 교체**(stale eligible 잔존 방지)+DUR 8유형·e약은요 재동기화 9/9 ok+크로스워크·의료법 코퍼스 최신 확인 | [RELEASE](docs/RELEASE_v2.118.4.md) |
| **v2.118.3** | **HIRA 약가목록 자동 동기화**(hira.or.kr 게시판 월별 최신판→fee_codes DRUG 안전 교체, 2026.7.1판 21,959건 적재·`/api/notice/hira-drug/sync{,-status}`·주기 스케줄러) + **의료법 생성 정밀도 규칙 2종**(Q5 친족 열람권 contradicted 봉합 — 5질의 충실도 전부 1.000·환각 0/5) | [RELEASE](docs/RELEASE_v2.118.3.md) |
| **v2.118.2** | 일반비전 `minicpm-v` 전환(qwen3-vl vision 빈 응답 fix)+빈 응답 502 가드 · app.py VERSION 정합 · 보안 P3-L2 키캐시 즉시폐기 · Tier B RAM증설 기획서 | [RELEASE](docs/RELEASE_v2.118.2.md) |
| **v2.118.1** | **DUR 병용금기 일반명 검출 fix**(의료 안전) — a측 성분명 빈값+b측 salt 정식명이라 일반명('셀레길린')이 미검출되던 것 salt 관대 해석(_strip_salt+접두 LIKE)으로 수정. 라이브 파록세틴+셀레길린→검출 | [RELEASE](docs/RELEASE_v2.118.1.md) |
| **v2.118.0** | **Heavy Tier** — 16GB 초과 대형 모델을 GPU+RAM 하이브리드(부분 오프로드)로 on-demand 구동. 상주 fast-lane 분리·응답 후 상주 자동복원·single-flight·`/api/chat/heavy`+status+settings. 기본 opt-in(무변경) | [RELEASE](docs/RELEASE_v2.118.0.md) |
| **v2.117.0** | **SANA 잔여 폴리시 완결** — CN↔ComfyUI 양방향 상호배제(CN 마진 ~30MB OOM 방지)·`GET /api/image/sana/status`(사이드카 health 프록시)·Admin "SANA 이미지" 카드·회귀 `tests/test_sana.py` 15. 계약 불변 | [RELEASE](docs/RELEASE_v2.117.0.md) |
| **v2.116.1** | SANA 사이드카 언로드를 `sana_enabled` 게이트로 — 기능 비활성(기본) 시 기존 ComfyUI 경로 100% 무변경 강제(하위호환 강화) | [RELEASE](docs/RELEASE_v2.116.1.md) |
| **v2.116.0** | **SANA ControlNet 구조 제어**(엣지/윤곽대로 프롬프트 렌더링 — 사이드카 gen↔cn 모드 스왑+PIL 엣지 전처리+`/api/image/sana/controlnet` 동기 엔드포인트(마킹§31). warm 1.66s co-resident 무스왑. gc.collect로 모드 스왑 OOM 봉합) | [RELEASE](docs/RELEASE_v2.116.0.md) |
| **v2.115.0** | **NVIDIA SANA 무스왑 이미지 fast-lane**(SANA-0.6B 사이드카 통합 — 상주 LLM 안 밀고 co-resident 1024² warm 1.14s 생성, 현 무기고 유일 무스왑. systemd 서비스+engine=sana 분기+마킹/S3 재사용+opt-in 토글. warm 결정타=단일 ThreadPoolExecutor로 cuDNN 워밍유지) | [RELEASE](docs/RELEASE_v2.115.0.md) |
| **v2.114.1** | 의료법 retrieval 튜닝(정답 조문 게이트 탈락 봉합 → 충실도 0.600→0.893) + KorMedMCQA-V 영상 벤치(MedGemma1.5 26.7%≈chance, vision 가치 약함 확인) | [RELEASE](docs/RELEASE_v2.114.1.md) |
| **v2.114.0** | **Ollama 0.21→0.30.11 업그레이드**(16/16 검증·warm 86tok/s 등가·37모델 무손실·**prefix 캐시 prefill 98%↓** 실측) + **의료 A/B**(실경로: med42 단계 기여 없음 — 객관 동률·judge 3.90>3.10·지연 우세 → HIS single_model 전환 권고 근거) + SelfCheckGPT(비-RAG 환각 검출)+시드조문 근거+품질 스파크라인 + ERP index-status 폴링 버그 대응(완료잡 22일 폴링→활동로그 제외+Cache-Control+통지문서) | [RELEASE](docs/RELEASE_v2.114.0.md) |
| **v2.113.0** | LLM 평가 완결 — Phase2 RAG 충실도(`core/faithfulness.py` LLM-judge 2단계, 게이트 4/4 vs HHEM 2/4) + **폐루프 실증**(측정이 라이브 실오류 포착→`MEDICAL_LAW_SYSTEM` 수치 재그룹 금지→**충실도 0.600→0.933·환각 0/5**) + Phase3 품질 judge(G-Eval 4차원, 4.10/5) + Phase4 야간 cron+`/api/eval/status`+Admin 품질 카드 + `docs/EVAL_GUIDE.md` | [RELEASE](docs/RELEASE_v2.113.0.md) |
| **v2.112.0** | LLM 평가 프레임워크 — 성능·할루시네이션·가치(외부 API 0·자체호스팅). P0 측정선행(HHEM-2.1-Open NO-GO=512토큰 한계→LLM-judge 전환) + Phase1 벤치 하네스 `eval_bench.py`(Ollama 제약 디코딩). **KorMedMCQA 400문항: qwen2.5:14b 67.5% > qwen7b 60.5% > med42 52.5% > MedGemma 35.2%**(범용>의료특화, 내부수치 역전). 해석한계=MCQA≠RAG프로덕션, 모델위계 재평가 플래그. | [RELEASE](docs/RELEASE_v2.112.0.md) |
| **v2.111.0** | 성능 모니터링 내부확장 — 신호수집(`core/metrics_reservoir.py` 지연 p50/95/99·5xx 에러율·GPU/스레드/SSE/스왑 샘플)·시계열영속(`stats_history` +14컬럼·`monitoring_meta` recycle 카운터·`/metrics` 신규 게이지)·임계알림(vram/temp/p95/error/thread>80/**worker_churn**(DUR class 자동포착)+worker_dead 오탐수정)·Admin 성능카드. 외부 데몬 0(P0 측정선행: 스크랩0→히스토리 척추). | [RELEASE](docs/RELEASE_v2.111.0.md) |
| **v2.110.4** | 운영 하드닝 — DUR 외부API 과다동기화 봉합(워커 재생성마다 식약처 풀스캔→`_sync_due` staleness 게이트) + 배치 검토 3건(파이프라인 실패 시 하위 waiting 고아 cascade-fail·재시도 백오프 `next_retry_at` 실적용·워커 재생성 in-flight 이중처리 race 차단) | [RELEASE](docs/RELEASE_v2.110.4.md) |
| **v2.110.3** | 비활성 챗 세션 retention purge(PHI 최소화 — `chat_sessions` 무한보존 갭 봉합, `last_active` 기준 기본 90일 전향적·`_auto_purge` 합류·`POST /api/chat/purge` 수동+opt-in VACUUM) | [RELEASE](docs/RELEASE_v2.110.3.md) |
| **v2.110.2** | AI 생성물 워터마크 폰트 축소(높이비례→짧은변 `min(W,H)*0.022`+폭상한, 세로 웹툰 120px→17px) + 요청별 `watermark:false` 토글(가시만 생략·메타/C2PA 유지=§31 충족) | [RELEASE](docs/RELEASE_v2.110.2.md) |
| **v2.110.1** | 보안성 검토 — 신규 외부 `/v1` 표면 하드닝(인증 fail-closed·CORS·SSRF 라이브 검증, CRITICAL/HIGH 0) + 경미 패치 2건(`num_predict` 16384 캡·업스트림 에러 일반화) | [RELEASE](docs/RELEASE_v2.110.1.md) |
| **v2.110.0** | OpenAI 호환 `/v1/chat/completions`+`/v1/models` (OpenPencil 연동) — `/api/chat` 변환 프록시로 요청별 `num_ctx` 주입(전역 8192 무성 절단 회피), tool_calls·SSE 양방향 변환 | [RELEASE](docs/RELEASE_v2.110.0.md) |
| **v2.109.1~3** | 전면 보안 보완 P0/P1/P2(시크릿/PHI git 이력 purge·SSRF 차단·인증 fail-closed·레이트리밋·S3 prefix) + 측정 선행 성능/SQL 튜닝(TTLCache 50×·busy_timeout·ANALYZE) | [RELEASE](docs/RELEASE_v2.109.2.md) |
| **v2.106~109** | 아바타 R3 심장 EP(eikonal LAT + 12-lead ECG glTF)·EP 대리모델 R&D(A/B·미분가능 역문제)·의료 영상 판독 Q8 | [RELEASE](docs/RELEASE_v2.108.0.md) |
| **v2.105.0** | 실시간 STT+번역 위임(`/api/translate`·`/api/stt/chunk`, 워밍 faster-whisper) + AI 기술백서 | [RELEASE](docs/RELEASE_v2.105.0.md) |
| **v2.103~104** | 아바타 R2 영상→장기 메시(glTF)·컨시어지 응답속도 보완(`/chat/enhanced` single_model·depth=fast) | [RELEASE](docs/RELEASE_v2.104.0.md) |
| **v2.94~102** | 의약품 데이터 스택(DUR 8유형·e약은요·약가목록·대체조제·코드 크로스워크 305K) + 결정적 의료 라우터 | [RELEASE](docs/RELEASE_v2.100.0.md) |
| **v2.88~93** | 의료법 RAG 자문(법령 3단비교 + KHIDI 고시)·ERP 사전심사(검색+삭감위험 평가)·환자 아바타 R1 생리 | [RELEASE](docs/RELEASE_v2.90.0.md) |

> 전체 버전별 상세는 [`docs/RELEASE_*.md`](docs/) 및 하단 [Changelog](#changelog) 참조.

GPU AI 서버 관리 시스템. LLM 추론, 이미지 생성, 의료 AI, HR 법률 AI, 게임 NPC AI, 글쓰기 AI, **심리 상담 AI**, **화상회의 AI 분석 (화자 분리+슬라이드 Vision)**, **MCP 서버 (외부 AI 에이전트 연동)**, **Tool Calling + 실시간 웹 검색**을 통합 관리합니다.
FHIR R4 표준 지원 + HIS 연동 SDK + 파인튜닝 의료 모델(werub-medical:7b) 탑재.
**한국어 품질 엔진** — im-not-ai v2.0 이식, 22 메트릭(번역투/AI관용구/구조/리듬), KatFish baseline, S1 18종 자동 재생성, Ollama 맞춤법 검사.
**소설 일관성 엔진** — NovelWriter 패턴 이식, 캐릭터 경험 추적(감정/성장 누적), 챕터 스냅샷, 자동 요약, 8계층 컨텍스트 빌더.
**의료 모델 11종** — 도메인별 자동 라우팅 (진단/약물/영상/연구/환자교육), 한/영 언어 감지 분기, 모델 폴백 체인.
**회의 분석 AI** — 화상회의 녹화본(MP4) 자동 분석. faster-whisper STT → qwen3:8b 요약/액션아이템 → 콜백. hospital-jitsi 연동.
**상담 AI** — mindwell(CBT/마인드풀니스) + qwen2.5(한국어 자연화) 오케스트레이션. 위기 키워드 감지 시 자살예방상담전화(1393) 자동 안내.
**강화 API** — `/api/chat/enhanced` (도메인별 다중 모델 오케스트레이션) + `/api/writing/craft` (3모델 순차 고품질 글쓰기) + `/api/writing/craft/stream` (SSE 스트리밍).
**LibriScribe 연동** — 외부 컨텍스트 주입, 프로젝트 스냅샷, 웹소설 메트릭, 메타데이터 추출 (7건 확장).
**오케스트레이션 플랫폼** — 커스텀 파이프라인 CRUD + 테스트(validate/dry_run/full_test) + 실행 이력 + A/B 비교 + 버전 관리 + 복제.
**Admin Console** — 3개 독립 콘솔 (Medical AI / HR Law / Game NPC) + Model Manager + Orchestration Editor. Medical: 실시간 대시보드 + 추론 체인 모델비교 + 예측 차트.
**Image Gen Desktop** — Tauri v2 데스크톱 클라이언트 (ComfyUI 연동).
**멀티서버 모니터링** — GPU/iMac/HIS/Prod 4서버 실시간 대시보드.

## Architecture

```
[Web Apps]                     [Production Server]           [GPU Server - RTX 5080]
┌─────────────────┐           ┌──────────────────┐          ┌────────────────────────────┐
│ werub-deploy    │   HTTPS   │ Nginx+Cloudflare │   SSH    │ WeRUBLLMManager v2.110.2    │
│ (Next.js)       │──────────>│ example.org       │─────────>│ Flask + Gunicorn :8585     │
├─────────────────┤           │ /ollama/* → :8585│  터널    │                            │
│ Admin Console   │           └──────────────────┘          │ ┌──────────────────────┐   │
│ (Next.js 3콘솔) │                                         │ │ app.py (9.5K줄)        │   │
├─────────────────┤                                         │ │ ├─ routes/ (34 BP)   │   │
│ WeRuB ImageGen  │                                         │ │ └─ core/ (48 모듈)   │   │
│ (Tauri)         │                                         │ └──────────┬───────────┘   │
└─────────────────┘                                         │ ┌──────────v───────────┐   │
                                                            │ │ Ollama :11434        │   │
[iMac M1 - Metal]                                             │ │ qwen2.5:14b (메인)   │   │
┌─────────────────┐                                         │ │ exaone3.5:7.8b (fb)  │   │
│ Ollama (Metal)  │<─── SSH 터널 ───────────────────────────│ └──────────────────────┘   │
│ 3b 오프로드+워커│                                         │ ┌──────────────────────┐   │
└─────────────────┘                                         │ │ ComfyUI :8188        │   │
                                                            │ │ (on-demand, 기본 OFF) │   │
                                                            │ │ 체크포인트 9종 (78GB) │   │
                                                            │ └──────────────────────┘   │
                                                            │ ChromaDB + SQLite (WAL)    │
                                                            │ chat_sessions/messages DB  │
                                                            └────────────────────────────┘
```

## Project Structure

```
WeRUBLLMManager/
├── app.py              # Flask 메인 (VERSION 상수 = 현재 버전 · 릴리즈 정본은 docs/RELEASE_v2.*.md)
├── core/               # 프레임워크 무관 공유 모듈 (64)
│   ├── model_router.py #   🔑 task → 모델 단일 라우팅(59 task) — 코드에 모델명을 쓰지 않는다
│   ├── engine_defaults.py #  엔진 기본값 핀(repeat_penalty 1.1) — 엔진 업그레이드에 생성이 흔들리지 않게
│   ├── law_attribution.py #  법령명 오귀속 탐지(순수 함수) — judge가 못 보는 축
│   ├── proviso_misread.py #  후단·단서 오독 탐지(순수 함수) — 관측 축
│   ├── config.py       #   설정 관리
│   ├── cache.py        #   TTL 캐시
│   ├── korean.py       #   한국어 필터 + 품질 검증
│   ├── korean_humanize.py #  한국어 자연화 22메트릭 (im-not-ai v2.0 이식)
│   ├── korean_spellcheck.py # 맞춤법 검사 (Ollama LLM 자체)
│   ├── security.py     #   API 키 생성/검증
│   ├── queue_engine.py #   ChatFairQueue
│   ├── chat_memory.py  #   대화 세션/메시지 저장, LLM 압축
│   ├── logger.py       #   로깅 설정
│   ├── reasoning_chain.py    # P4-1: 5단계 추론 체인 엔진
│   ├── gpu_scheduler.py      # P3-1: 시간대별 GPU 모델 스케줄링
│   ├── priority_queue.py     # P3-2: 도메인별 우선순위 큐
│   ├── websocket_handler.py  # P3-3: SSE 스트리밍 + GPU/Priority API
│   ├── drug_batch_scheduler.py # 약물 배치 스케줄러 + 야간 자동 모드
│   ├── drug_collector.py     # 약물 수집 어댑터 (LLM_SEED, Manual, MFDS)
│   ├── drug_enricher.py      # LLM 배치 정제 + JSON 파싱
│   ├── drug_validator.py     # 4-Layer 검증 (Schema/Dup/Clinical/CrossRef)
│   ├── web_search.py        # SearXNG 실시간 웹 검색 래퍼 (v2.65.0)
│   ├── meeting_diarize.py   # SpeechBrain ECAPA-TDNN 화자 분리 (v2.66.0)
│   └── meeting_vision.py    # 키프레임 추출 + Vision LLM 슬라이드 분석 (v2.66.0)
├── routes/             # Flask Blueprint (34개)
│   ├── meeting.py      #   (6) Meeting Analysis Phase 2 (STT+화자분리+Vision+요약, hospital-jitsi)
│   ├── search.py       #   (5) Web Search (SearXNG 실시간 검색, general/news/science/it)
│   ├── mcp.py          #   (1) MCP Server (Model Context Protocol, 외부 AI 에이전트 도구 연동)
│   ├── counseling.py   #   (6) Counseling Orchestration (mindwell+qwen2.5, 위기감지)
│   ├── korean.py       #  (24) Korean Language API (독립 Blueprint, 분석+교정+변환+진단)
│   ├── dashboard_api.py #   (9) 통합 대시보드 API (외부 시스템용, ETag/304/캐시, 품질 메트릭)
│   ├── story.py        #  (31) Story Engine (quality_metrics 자동 적용)
│   ├── novel.py        #  (82) Novel Writer (7027줄, 캐릭터 경험 추적, 스냅샷)
│   ├── writing.py      #  (11) Writing AI (자연화+맞춤법 통합)
│   ├── ollama.py       #  (35+) API키, Ollama 프록시, 채팅 도메인 라우팅, 오케스트레이션 13API
│   ├── queue.py        #  (16) 작업 큐 CRUD
│   ├── image.py        #  (15) 이미지 생성, 캐릭터 일관성
│   ├── hr_law.py       #  (23) 근로기준법 AI, 멀티테넌트
│   ├── game.py         #  (40) NPC 대화/성장, 히로인, 시나리오
│   ├── training.py     #  (32) QLoRA 학습, 사용자 관리
│   ├── system.py       #  (14) 헬스체크, 벤치마크, 모니터링
│   ├── activity.py     #   (6) 활동 로그, 캐시, Prometheus
│   ├── rag.py          #   (5) RAG 검색/색인/질의
│   ├── vision.py       #   (7) 이미지 설명, 의료 영상 판독, 이중 판독 (6종 모델)
│   ├── medical_advanced.py # (14) 추론 체인, 예측, clinical-qa, drug-analysis, research, patient-education
│   ├── fhir.py         #   (6) FHIR R4 래퍼 (DiagnosticReport, RiskAssessment 등)
│   ├── drug_batch.py   #  (12) 약물 배치 파이프라인 + 스테이징 관리
│   └── provenance.py   #   (6) 출처 추적 (타임라인, 요약, 검색)
├── client/             # Python SDK
│   ├── ollama_batch.py #   배치 클라이언트
│   └── his_client.py   #   HIS 연동 클라이언트 (일반 API + FHIR)
├── templates/          # Jinja2 (admin, login, drug_pipeline, model_manager, orchestration_editor)
├── static/             # JS, CSS
├── admin-console/      # Next.js 관리 콘솔 (별도 빌드)
├── custom_pipelines.json # 커스텀 오케스트레이션 파이프라인 (자동 생성)
├── docs/               # 기획서, API 가이드
└── gunicorn.conf.py    # Gunicorn 설정 (gthread, 1W×96T, preload_app=False)
```

## Models

### 엔진 — Ollama 0.32.5 (2026-07-29 업그레이드 · 2026-08-25 기준 유지 판정)

현행 **0.32.5**(0.21.0 → 0.30.11 → 0.32.5). **핵심 실익은 prefix 캐시** — 생성 속도(tok/s)는
등가이고 반복 프리픽스 경로가 극적으로 빨라진다(아래 표는 0.30.11 도입 시 실측이며 이후에도 유효).

⚠️ **최신(0.32.15)으로 올리지 않는 이유**: 이 레포는 트리거 없는 업그레이드를 하지 않는다(v2.114.0 규율).
0.32.10이 `repeat_penalty` 기본값을 1.1→1.0으로 바꿨는데 우리 33/42 모델이 그 기본값에 의존하고 있었고,
사전 등록 A/B(N=40)가 **1.1 유지**로 판정했다 → v2.125.14가 값을 어댑터에 **명시**해 의존을 끊었다.
그래서 업그레이드 선행 조건은 닫혔고 **트리거만 대기** 중이다(관측된 증상·보안 공지·신모델 게이트).
검토 정본 `docs/01-plan/references/model-engine-scan-2026-08-25.md`.

| 항목 | 0.21.0 | 0.30.11 (기준선 측정) | 개선 |
|---|---|---|---|
| **prefill — 반복 프리픽스**(4K tok급) | 매번 ~1.2s 재계산 | **0.02s** | **~98%↓ (약 50배)** |
| 반복 호출 전체 응답(wall) | 1.66s | 0.20s | ~85%↓ |
| VRAM footprint (14b 상주) | 12.2GB | 10.5GB | **−1.7GB** (MedGemma·bge-m3·MiniCheck 공존 여유) |
| warm 생성 속도 (qwen2.5:14b) | ~89 tok/s | **86 tok/s** | 등가(오차 범위) |
| 컨텍스트 초과 동작 | 무성 절단 | 명시 에러 | 신뢰성(품질 사고 예방) |

수혜 경로 = 동일 시스템 프롬프트/도구 스키마 반복 호출(오케스트레이터 tool-calling·의료
파이프라인·`/v1` OpenAI 호환·야간 평가 judge). 프롬프트 구조(시스템·도구 선두)가 이미 캐시
친화적이라 리팩터링 0. 부가=NVFP4 양자화 지원(모델 대기)·신 스케줄러.
상세: `docs/RELEASE_v2.114.0.md` · 런북 `docs/01-plan/features/ollama-upgrade-0.30.plan.md`.

### LLM Models (Ollama)

| Model | Size | Speed | Use Case |
|-------|------|-------|----------|
| **werub-medical:7b** | 4.7GB | ~113 tok/s | **의료 메인 v1** — QLoRA (53건), SOAP 85.5, 감별 64.0 |
| werubmedv2 | 4.7GB | ~113 tok/s | 의료 v2 (232건, 벤치마크 40.8 — 구조화 약점, 재학습 필요) |
| **Med42 8B** | 8.5GB | — | **임상 QA 최강** — USMLE급 추론, 감별진단/약물/예측 (v2.48.0) |
| **MedGemma 27B** | 17GB | — | Google 의료 27B — 심층 임상 추론, 야간 배치 전용 (v2.48.0) |
| **OpenBioLLM 8B** | 4.9GB | — | PubMedQA/BioASQ — 바이오의학 연구 문헌 (v2.48.0) |
| **MediChat 8B** | 4.9GB | — | 환자 교육/건강 상담 전문 (v2.48.0) |
| **gpt-oss:20b** | 13GB | — | **OpenAI o3-mini급 추론** — HealthBench 최상위, 의료 교차검증 (v2.69.0) |
| **exaone-deep:7.8b** | ~5GB | — | **한국어 추론 글쓰기** — 구조적 콘텐츠, 논리 구성 (v2.48.0) |
| **dnotitia/dna:8b** | ~5GB | — | **한국어 생성 SOTA** — 블로그/SEO 최적화 (v2.48.0) |
| **gemma4** (e4b MoE) | 9.6GB | — | Google MoE — Tool Calling+Vision 네이티브 (v2.48.0) |
| **gemma4:26b** (MoE) | 17GB | — | Google 26B MoE — agent 도메인 전용 (v2.69.0) |
| **qwen3-vl:8b** | 6.1GB | — | **일반 비전 기본**(v2.125.10 복귀) — 이미지 분석·OCR. ⚠️`think:false`를 무시하고 사고 토큰이 `num_predict`를 먹으므로 **예산 ≥2048** 필요 |
| minicpm-v:8b | 5.5GB | — | 일반 비전 **폴백**. ⚠️한글 명함에서 이름·직함·회사를 **창작**한 이력 (v2.125.9) — 기본에서 내린 이유 |
| **deepseek-ocr:3b** | 6.7GB | — | 문서/이미지 OCR, 마크다운 변환 (v2.48.0) |
| **qwen2.5:14b** | 8.4GB | 86 tok/s | **범용 메인** — 한국어 99%, 상주(balanced) · 평가 judge 겸용 |
| exaone3.5:7.8b | 4.8GB | 35 tok/s | fallback — LG AI, 한/영 이중 |
| medllama2 | 3.8GB | — | 의료진 임상 분석 (영어, 참고문헌) |
| meditron | 3.8GB | 188 tok/s | 의료 약리학/가이드라인 (영어) |
| medgemma:4b | 3.3GB | — | 의료 영상 Vision (X-ray, 피부, 병리) |
| **MedGemma 1.5 4B** | ~3.3GB | — | **의료 텍스트 89.7** — clinical_qa/reasoning 2순위, EHR/FHIR (1순위 med42 유지, v2.88.3) |
| **qwen3:14b** | 9.3GB | — | **추론 업그레이드** — reasoning(ko) 도메인, counseling 자연화 (v2.69.0) |
| **qwen3:8b** | 5.2GB | — | Tool Calling 기본, thinking 모드 (v2.62) |
| **phi4-reasoning** | 11GB | — | **의료 추론** — 교차검증 3번째 모델 (v2.62) |
| **ALIENTELLIGENCE/mindwell** | 9.3GB | — | **상담 전문** — CBT/마인드풀니스 (v2.62) |
| qwen2.5:7b | 4.4GB | — | 배치/분류 |
| **qwen3-coder:30b** | ~4GB | — | **코딩 메인** — MoE 활성 3.3B, 에이전틱 코딩 특화 (v2.70.0) |
| llama3.2:3b | 1.9GB | — | 경량 범용 |

> 2026-08-03 제거: `deepseek-r1:14b`·`phi4:14b`(코드 배선 0 — 이 표에만 존재했음). 디스크 정리 103GB 회수.
>
> ⚠️ **정정(2026-08-25)**: 위 줄은 한때 "`qwen3.6:35b-a3b` 제거 · 초대형은 AirLLM으로 이관"이라고
> 적고 있었으나 **둘 다 뒤집혔다** — AirLLM·llama.cpp는 3트랙 판정에서 **탈락**했고(`project_airllm_70b`),
> `qwen3.6:35b-a3b`는 **heavy tier 기본값으로 라이브**다. 다만 heavy의 품질 우위는 **근거가 없다**:
> grounded 독해 N=40 무승부(v2.125.5)에 이어 **창작·장문 N=40에서도 현상 유지**(G1 · p=0.70 ·
> 지시 신호 준수는 오히려 97.5% vs 87.5%로 상주 14b 우세 · 3.1배 느림). heavy는 on-demand라
> 안 쓰면 비용 0이지만, **"품질이 더 좋다"를 전제로 배선하지 말 것**.
> 정본 `docs/01-plan/features/heavy-creative-longform-eval.plan.md` · 원자료 `docs/01-plan/references/heavy-creative-eval-result.json`.

### Embedding Models

| Model | Size | Dim | Use Case |
|-------|------|-----|----------|
| **bge-m3** | 1.1GB | 1024 | **RAG 메인** — 다국어, 14b와 VRAM 공존(num_ctx=2048), client.rag 단일화 (v2.88.2) |
| qwen3-embedding:8b | 4.7GB | 4096 | 대형 임베딩 (레거시 — bge-m3로 단일화하며 미사용 전환, v2.88.2) |
| mxbai-embed-large | 0.6GB | 1024 | 영문 특화 |
| nomic-embed-text | 0.3GB | 768 | 코딩 RAG 폴백 |

### Image Models (ComfyUI, on-demand)

FLUX.1 Schnell/Dev, SDXL, Juggernaut XL, DreamShaper XL, Animagine XL 등 9종
스타일 프리셋 15종, LoRA 4종 (pixel-art, LCM, detail, realism)

---

## Batch Mode (v2.70.0)

iMac 등 CPU 서버에서 배치 전용으로 운영 시 사용. 실시간 API 20개를 비활성화하고 배치 핵심 API만 운영.

```bash
# 배치 모드 실행
BATCH_MODE=1 gunicorn -c gunicorn_batch.conf.py app:app

# 일반 모드 (GPU 서버, 기본)
gunicorn -c gunicorn.conf.py app:app
```

| 항목 | 일반 모드 | 배치 모드 |
|------|----------|----------|
| Blueprint | 34개 전체 | 9개 (queue/ollama/system/korean/writing/coding/search/dashboard/activity) |
| threads | 32 | 4 |
| timeout | 300초 | 600초 (CPU 추론 대응) |
| 비활성 API | 없음 | 503 + 안내 메시지 |

---

## Quick Start

### 접속

| 위치 | URL |
|------|-----|
| Admin UI | https://example.org/ollama/ |
| Admin Console | https://example.org/ollama/admin-console/ |
| Medical AI | https://example.org/ollama/admin-console/medical/dashboard/ |
| HR Law | https://example.org/ollama/admin-console/hr-law/chat/ |
| Game NPC | https://example.org/ollama/admin-console/game/dashboard/ |
| **Drug Pipeline** | **https://example.org/ollama/drug-pipeline** |
| **Model Manager** | **https://example.org/ollama/model-manager** |
| **Dashboard Monitor** | **https://example.org/ollama/monitor** |
| **Orchestration Editor** | **https://example.org/ollama/orchestration-editor** |
| **Dashboard API** | **https://example.org/ollama/api/dashboard/summary** |
| Swagger | https://example.org/ollama/docs |
| Prometheus | https://example.org/ollama/metrics |
| Health Check | https://example.org/ollama/api/health |

### 로그인

| 항목 | 값 |
|------|-----|
| 아이디 | `admin` |
| 비밀번호 | users.db 설정값 |

---

## API 카테고리 (620+)

### LLM 품질 평가 (5) — 세밀 지표·시계열·드릴다운 (v2.120~123)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/eval/status` | 최신 배치 집계+세밀 지표(교차검증·체크리스트)+시계열+**품질 종합 지수(qi-1.0)** |
| GET | `/api/eval/detail?kind=&id=` | 단건 상세 — 답변 전문·주장별 판정(judge vs MiniCheck)·차원 사유·체크 항목 |
| GET | `/api/eval/history?question=` | 같은 질문의 배치 이력 |
| GET | `/eval-monitor` | **실시간 품질 모니터 새창**(히어로 지수·KPI 12타일·시계열 4차트·행 클릭 드릴다운) |
| — | 야간 cron 03:30 | 질의 풀 40문항(core 5+일자 회전 5)/배치 자동 측정 → `eval.db` |

정본 가이드: `docs/EVAL_GUIDE.md` (산식·판독 규율·폐루프 실증 포함)

### 수집 로그·보고서 (4) — 외부 정본 수집 관측 (v2.119)

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/notice/collect/logs` | 수집 이력(HIRA 게시판·data.go.kr 12소스, 필터) |
| GET/POST | `/api/notice/collect/report` | 주기 수집보고서 최신 조회/즉시 생성 |
| GET | `/api/notice/collect/reports` | 보고서 목록 |
| POST | `/api/notice/collect/purge` | 로그 retention 정리 |

### Dashboard API (8) — 외부 통합 관리용
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/dashboard/summary` | **전체 현황 요약** (서버+모델+큐+파이프라인, 10s 캐시) |
| GET | `/api/dashboard/server` | 단일 서버 상세 (CPU/GPU/RAM/디스크/프로세스) |
| GET | `/api/dashboard/servers` | **4서버 병렬 비교** (local/gpu/prod/his/imac) |
| GET | `/api/dashboard/models` | 모델 목록 + GPU 리소스 |
| GET | `/api/dashboard/queue` | 큐 상태 + 성능 통계 + ETA |
| GET | `/api/dashboard/orchestration` | 워크플로우 목록 + 실행 이력 |
| GET | `/api/dashboard/pipeline` | 약물 파이프라인 상태 + 통계 |
| GET | `/api/dashboard/stream` | **통합 SSE 실시간 스트림** |

> ETag/304 최적화 + `?fields=` 필터링 + `Cache-Control` 헤더. 상세: [Dashboard API Guide](docs/DASHBOARD_API_GUIDE.md)

### Ollama Proxy (22)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Ollama 상태 |
| GET | `/api/models` | 설치된 모델 목록 |
| POST | `/api/generate` | 텍스트 생성 (스트리밍) |
| POST | `/api/chat` | 채팅 (스트리밍) |
| POST | `/api/embed` | 임베딩 생성 |
| POST | `/api/embeddings` | 임베딩 (레거시) |
| POST | `/api/pull` | 모델 다운로드 |
| POST | `/api/delete` | 모델 삭제 |
| GET | `/api/version` | 버전 + 체인지로그 |
| GET/POST | `/api/config` | 설정 조회/변경 |
| GET/POST | `/api/ollama/perf` | Ollama 성능 설정 |
| POST | `/api/ollama/restart` | Ollama 재시작 |
| GET | `/api/gpu/status` | GPU 상태 |
| POST | `/api/service/<action>` | 서비스 제어 |
| GET | `/api/chat/queue/stats` | 채팅 큐 통계 |
| CRUD | `/api/keys` | API 키 관리 |
| POST | `/api/ai/fact-check` | 팩트 체크 |

### Queue (16)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/queue/submit` | 작업 제출 |
| POST | `/api/queue/batch` | 배치 제출 |
| POST | `/api/queue/batch/upload` | JSONL 배치 업로드 |
| GET | `/api/queue/job/<id>` | 작업 상태 |
| GET | `/api/queue/status` | 큐 상태 |
| GET | `/api/queue/stats` | 큐 통계 + ETA |
| GET | `/api/queue/jobs` | 작업 검색/필터 |
| GET | `/api/queue/batches` | 배치 목록 |
| POST | `/api/queue/pause` | 큐 일시정지 |
| POST | `/api/queue/resume` | 큐 재개 |
| POST | `/api/queue/retry/<id>` | 실패 작업 재시도 |
| POST | `/api/queue/cancel/<id>` | 작업 취소 |
| POST | `/api/queue/purge` | 완료/실패 정리 |
| POST | `/api/queue/pipeline` | **파이프라인 제출** (다단계 순차, `{{prev.response}}` 체인) |
| GET | `/api/queue/pipeline/<id>` | 파이프라인 상태/결과 조회 |

### Image (15)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/image/generate` | 이미지 생성 |
| GET | `/api/image/status/<id>` | 생성 상태 |
| GET | `/api/image/view/<file>` | 이미지 조회 |
| GET | `/api/image/models` | 모델/스타일/LoRA 목록 |
| GET | `/api/image/storage` | 스토리지 통계 |
| POST | `/api/image/cleanup` | 이미지 정리 |
| GET | `/api/image/gallery` | 갤러리 |
| DELETE | `/api/image/delete/<file>` | 이미지 삭제 |
| POST | `/api/image/settings` | 스토리지 설정 |
| POST | `/api/image/character/create` | 캐릭터 등록 |
| POST | `/api/image/s3/upload/{file}` | **S3 업로드** (ComfyUI→Wasabi) |
| POST | `/api/image/s3/upload-raw` | **S3 직접 업로드** (multipart) |
| GET | `/api/image/s3/list` | S3 이미지 목록 |
| GET | `/api/image/s3/url/{key}` | Presigned URL 발급 |
| DELETE | `/api/image/s3/delete/{key}` | S3 이미지 삭제 |
| GET | `/api/image/s3/status` | S3 연결 상태 |
| GET | `/api/image/characters` | 캐릭터 목록 |
| GET/DELETE | `/api/image/character/<id>` | 캐릭터 상세/삭제 |

### HR Law (23)
질의응답, 스트리밍, 법률 검색, 테넌트 관리, 취업규칙, 법위반 검토, 연차/초과근무/퇴직금 계산

### Game NPC (40)
테넌트 등록, NPC 템플릿/인스턴스, 대화(1:1/그룹), 성장, 시나리오, 일러스트, 세계관, 공유 NPC, 히로인 시스템

### Training (32)
QLoRA 학습 시작/중지/모니터링, 데이터 업로드, 모더레이션, 사용자/역할 관리

### System (14)
로그, 헬스체크, 서버 통계, 벤치마크, 초기화 설정

### Activity (6)
활동 로그, 서버별 통계, 캐시 관리, iMac 상태, Prometheus 메트릭

### RAG (5)
문서 색인, 검색, 질의응답, 삭제, 통계

### Medical AI (30+)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/tenant/register` | 병원 등록 |
| GET | `/api/medical/tenants` | 병원 목록 |
| POST | `/api/medical/summarize` | 진료 기록 요약 (SOAP) |
| POST | `/api/medical/summarize/stream` | 요약 스트리밍 |
| POST | `/api/medical/analyze` | 감별진단 (한/영 모델 자동 분기) |
| POST | `/api/medical/reference` | 의학 참고 (RAG, OpenBioLLM) |
| POST | `/api/medical/explain` | 환자 설명문 (MediChat) |
| POST | `/api/medical/draft` | 의무기록 초안 |
| POST | `/api/medical/clinical/analyze` | 의료진 임상분석 (Med42) |
| POST | `/api/medical/clinical/drug` | 약물 정보 (meditron) |
| POST | `/api/medical/clinical/translate` | 영→한 의학 번역 |
| POST | `/api/medical/clinical-qa` | **의료인 전문 QA (Med42 8B)** |
| POST | `/api/medical/drug-analysis` | **약물 심층 분석 (3 depth: quick/standard/comprehensive)** |
| POST | `/api/medical/research` | **바이오의학 연구 질의 (OpenBioLLM)** |
| POST | `/api/medical/patient-education` | **환자 교육 설명 (MediChat)** |
| POST | `/api/medical/model/compare` | **다중 모델 비교 (최대 3개)** |
| POST | `/api/medical/model/benchmark` | **10 표준 증례 벤치마크** |
| GET | `/api/medical/models` | **의료 모델 레지스트리 (11종)** |
| GET | `/api/medical/models/routing` | **도메인→모델 라우팅 테이블** |
| POST | `/api/medical/models/recommend` | **질의 기반 모델 추천** |
| POST | `/api/medical/knowledge/index` | 의학 지식 색인 |
| POST | `/api/medical/knowledge/search` | 지식 검색 |
| GET | `/api/medical/knowledge/stats` | 지식 통계 |

### Coding AI (16)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/code/generate` | 코드 생성 |
| POST | `/api/code/generate/stream` | 코드 생성 (스트리밍) |
| POST | `/api/code/review` | 코드 리뷰 (버그/보안/성능) |
| POST | `/api/code/refactor` | 리팩토링 |
| POST | `/api/code/debug` | 디버깅 (에러 분석) |
| POST | `/api/code/test` | 테스트 생성 |
| POST | `/api/code/explain` | 코드 설명 (한국어) |
| POST | `/api/code/convert` | 언어 변환 |
| POST | `/api/code/architect` | 아키텍처 설계 |
| POST | `/api/code/chat` | 코딩 채팅 (멀티턴) |
| GET | `/api/code/models` | 코딩 모델 목록 |

### Orchestration Platform (13)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orchestration/pipelines` | 파이프라인 목록 (builtin + custom) |
| GET | `/api/orchestration/pipeline/<name>` | 파이프라인 상세 |
| POST | `/api/orchestration/pipeline` | 파이프라인 생성 (최대 5단계) |
| PUT | `/api/orchestration/pipeline/<name>` | 파이프라인 수정 (자동 버전 백업) |
| DELETE | `/api/orchestration/pipeline/<name>` | 파이프라인 삭제 |
| POST | `/api/orchestration/run` | **파이프라인 실행** (debug/에러처리/이력) |
| POST | `/api/orchestration/test` | **테스트** (validate/dry_run/full_test) |
| GET | `/api/orchestration/runs` | 실행 이력 조회 |
| GET | `/api/orchestration/run/<id>` | 실행 결과 상세 |
| POST | `/api/orchestration/compare` | **A/B 파이프라인 비교** |
| GET | `/api/orchestration/pipeline/<name>/versions` | 버전 목록 |
| POST | `/api/orchestration/pipeline/<name>/rollback` | 이전 버전 복원 |
| POST | `/api/orchestration/pipeline/<name>/clone` | 파이프라인 복제 |

### Enhanced Chat & Writing (2)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/chat/enhanced` | **강화 채팅** — 도메인별 다중 모델 오케스트레이션 |
| POST | `/api/writing/craft` | **3모델 고품질 글쓰기** — 초안→구조→다듬기 |

### Symptom Triage (4)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/medical/triage` | 증상->진료과 추천 (Top 3 + 응급 감지) |
| POST | `/api/medical/triage/follow-up` | 대화형 추가 질문 |
| GET | `/api/medical/triage/departments` | 18개 진료과 목록 |
| GET | `/api/medical/triage/common-symptoms` | 14개 자주 묻는 증상 |

### Vision (6)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/vision/describe` | 이미지 설명 생성 (moondream:1.8b) |
| POST | `/api/vision/medical/analyze` | 의료 영상 분석 — 소견/판독/권고 (MedGemma 4B) |
| POST | `/api/vision/medical/dual-read` | **이중 판독 (MedGemma + LLaVA-Med)** |
| GET | `/api/vision/medical/models` | 의료 영상 모델 목록 |
| POST | `/api/vision/medical/report` | 비교 판독 (현재 vs 이전 영상) |
| GET | `/api/vision/models` | 비전 모델 목록 (3종) |

### Drug Batch Pipeline (12)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/drug-batch/run` | 배치 파이프라인 실행 |
| POST | `/api/drug-batch/run-sync` | 동기 실행 (테스트용) |
| POST | `/api/drug-batch/stop` | 진행 중 배치 중단 |
| GET | `/api/drug-batch/status` | 실행 상태 + 모드(Day/Night) |
| GET | `/api/drug-batch/history` | 실행 이력 목록 |
| GET | `/api/drug-batch/history/<id>` | 실행 상세 (staging + provenance) |
| GET | `/api/drug-batch/staging` | 스테이징 데이터 조회 |
| POST | `/api/drug-batch/staging/<id>/approve` | 수동 승인 |
| POST | `/api/drug-batch/staging/<id>/reject` | 수동 거부 |
| GET | `/api/drug-batch/stats` | 데이터 증가 통계 |
| GET/PUT | `/api/drug-batch/schedule` | 스케줄 + Night Mode 설정 |
| POST | `/api/drug-batch/night-mode` | 야간 모드 ON/OFF 토글 |

### Provenance (6)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/provenance/<table>/<key>` | 레코드 출처 이력 |
| GET | `/api/provenance/<table>/<key>/timeline` | 타임라인 (UI 최적화) |
| GET | `/api/provenance/summary` | 전체 출처 분포 통계 |
| GET | `/api/provenance/recent` | 최근 변경 이력 |
| GET | `/api/provenance/search` | 조건부 검색 |
| GET | `/api/provenance/batch/<run_id>` | 배치별 이력 |

---

## Security Posture

외부 표면은 **SSH + HTTP(80)** 둘뿐이고, 620+ API는 전부 SSH 리버스 터널을 통해서만 도달한다.
인증 경계는 아래 계층으로 서 있다.

| 계층 | 방어 | 근거 |
|---|---|---|
| 전송 | SSH 공개키 전용(`PasswordAuthentication no`)·fail2ban·비표준 포트 | 라이브 점검 2026-08-25 |
| 인증 | Bearer API 키(외부 필수·무키 401) · `local_bypass`는 **의도된 설계**(localhost 한정) | `core/security.py` |
| 인가 | 관리자 엔드포인트(키·설정·모델 삭제·서비스 제어)는 `_is_admin_request()` fail-closed | v2.125.18 |
| 인가(도메인) | 의료·HR Law 테넌트는 키 해시 + `_tenant_kb_filter` 격리 | v2.124.21·v2.125.18 |
| 오케스트레이션 | 워크플로 `ai:` 액션은 **allowlist**(위조 localhost 권한 봉쇄) | v2.125.17 |
| 입력 | 경로 순회 basename/realpath 봉쇄 · SSRF 2단(요청 시점 + 다운로드 워커 fail-closed) · 20MB 캡 | v2.125.17·meeting |
| 출력 | 응답 에러 위생(무절단 str(e) 금지) · 세션 쿠키 HTTPONLY/SECURE/SameSite | v2.124.25 |
| 콘텐츠 | AI 생성물 C2PA 서명 + 워터마크(AI 기본법 §31) | v2.77~110 |

**규율 하나**: 이 레포에서 나온 보안 결함 대부분은 새 취약점이 아니라 **이미 옳게 푼 패턴의 미적용
지점**이었다(`basename` 4곳 적용/2곳 누락, 플레이스홀더 제외 절 hr_law 적용/medical 누락 등).
그래서 개별 처치가 아니라 **패턴별 인벤토리를 떠서 잔여를 0으로** 만들고, 회귀도 개별 라우트가 아니라
**패턴 자체**를 고정한다(예: "가드 없는 `<path:>` 조립이 레포에 0곳"·"모든 `ai:` 액션이 allowlist 안에").

> 운영자 대기(sudo·정책): 재부팅(커널 7.0.0-30) · OS 보안 업데이트(curl 계열 4건) · nginx :80 노출 ·
> API 키 위생. 정본 `docs/01-plan/references/operator-pending-2026-08-25.md`.

---

## Key Features

### ChatFairQueue (v2.24.0)
글로벌 동시 3건 + 유저별 1건 제한. FIFO 대기열 20건, 60초 타임아웃.

### GPUResourceManager (v2.25.0)
ComfyUI on-demand — 기본 OFF, 이미지 요청 시 자동 시작, 5분 idle 후 종료.

### Korean Quality (v2.26.0 → v2.34.0)
`core/korean.py` — 중국어/일본어 필터, 한글 비율 검증, 최대 2회 자동 재시도.

### Drug Data Pipeline (v2.45.0)
약물 데이터 자동 수집→LLM 정제→4-Layer 검증→DB 반영 파이프라인.
4-Tier 출처 추적(Provenance), 야간 자동 모드 (22:00~06:00, limit 200, qwen2.5:14b).
전용 UI `/drug-pipeline` (5탭: Overview/Pipeline/Staging/Provenance/Settings).

### Module Split (v2.34.0)
app.py 18,380줄 → 9,000줄 (-51%). 10 Blueprint + 6 core 모듈.

### Fact Check (v2.34.0)
`/api/ai/fact-check` — 글 내 수치/날짜/사실관계 LLM 검증.

### Orchestration Platform (v2.50.0)
커스텀 다중 모델 파이프라인 — 생성/실행/테스트/비교/버전 관리.

**Builtin 파이프라인 4종:**
- `medical` — werub(정규화) → Med42(감별진단) → werub(한국어 종합)
- `coding` — qwen3-coder:30b(MoE 코드) → qwen2.5-coder:14b(폴백)
- `writing` — qwen2.5:14b(초안) → exaone-deep(구조) → dna(다듬기)
- `legal` — qwen2.5:14b(법률) → exaone-deep(논증 보강)

**커스텀 파이프라인 생성:**
```bash
curl -X POST /api/orchestration/pipeline -d '{
  "name": "blog-seo",
  "description": "SEO 최적화 블로그 글쓰기",
  "stages": [
    {"model": "qwen2.5:14b", "role": "SEO 키워드 + 초안", "system": "SEO 전문가입니다..."},
    {"model": "exaone-deep:7.8b", "role": "구조 강화", "system": "편집자입니다..."},
    {"model": "dnotitia/dna:latest", "role": "한국어 다듬기", "system": "문체 전문가..."}
  ]
}'
```

**실행 (debug 모드):**
```bash
curl -X POST /api/orchestration/run -d '{
  "pipeline": "blog-seo",
  "input": "AI 트렌드 블로그 글",
  "debug": true
}'
# → stages: [{stage:1, output:"...", tokens:447, duration_ms:5420}, ...]
```

**테스트 (3 모드):**
```bash
# 모델 설치 + VRAM 확인
curl -X POST /api/orchestration/test -d '{"pipeline":"blog-seo","mode":"validate"}'

# Stage 1만 실행
curl -X POST /api/orchestration/test -d '{"pipeline":"blog-seo","input":"AI","mode":"dry_run"}'

# 전체 실행 + 품질 메트릭
curl -X POST /api/orchestration/test -d '{"pipeline":"blog-seo","input":"AI","mode":"full_test"}'
```

**에러 정책:**
```json
{"model": "med42:8b", "role": "감별진단",
 "on_fail": "continue",
 "retry": 1,
 "fallback_model": "meditron:latest"}
```
- `on_fail`: abort(중단) / continue(계속) / skip(건너뜀)
- `retry`: 실패 시 재시도 횟수
- `fallback_model`: 재시도 실패 시 대체 모델

**A/B 비교:**
```bash
curl -X POST /api/orchestration/compare -d '{
  "pipelines": ["medical", "medical-v2"],
  "input": "흉통 55세 남성"
}'
# → fastest, most_tokens, longest_output 판정
```

**버전 관리:**
```bash
PUT /pipeline/<name>          # 수정 (이전 버전 @v1로 자동 백업)
GET /pipeline/<name>/versions # 버전 목록
POST /pipeline/<name>/rollback?version=1  # 복원
POST /pipeline/<name>/clone   # 복제
```

**안정성:** Semaphore(1) 동시 실행 제한 + 30일 자동 정리 + 실행 이력 DB.

### Chat Domain Routing (v2.49.0)
채팅 도메인 자동 감지 — 6도메인(medical/legal/coding/writing/vision/general) → 12+ 전문 모델 자동 라우팅.
한국어/영어 입력 자동 감지 + 도메인별 시스템 프롬프트 + 컨텍스트 윈도우 관리 + 멀티모달(images).
`/api/chat/enhanced` — 도메인별 다중 모델 오케스트레이션 (medical 3단계, coding 2단계 등).
`/api/writing/craft` — 3모델 고품질 글쓰기 (qwen→exaone-deep→dna, ~25초).

---

## Ollama Configuration

```bash
# systemd drop-in: /etc/systemd/system/ollama.service.d/environment.conf
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KV_CACHE_TYPE=q8_0        # 정밀도↑ (q4_0 대비)
OLLAMA_NUM_PARALLEL=2            # KV = num_ctx × parallel — OpenPencil 32K 컨텍스트 GPU 완전적재 위해 4→2 (v2.110.0)
OLLAMA_CONTEXT_LENGTH=8192       # 전역 기본 (요청별 num_ctx 주입으로 오버라이드)
OLLAMA_KEEP_ALIVE=5m
OLLAMA_MAX_LOADED_MODELS=1       # 16GB VRAM 천장 — 단일 모델 상주, 스왑 최소화
```

> 드롭인이 `/etc/default/ollama`(EnvironmentFile)보다 우선. 상세: `docs/` 및 메모리 `reference_ollama_gpu_tuning`.

## Server Info

| Item | Value |
|------|-------|
| OS | Ubuntu 24.04.3 LTS |
| CPU | AMD Ryzen 9 7900 (12C/24T) |
| RAM | 30GB |
| GPU | NVIDIA RTX 5080 16GB |
| Disk | NVMe 915GB |
| SSH | `ssh deploy@203.0.113.10` |
| Ollama | v0.21.0 (NUM_PARALLEL=2, MAX_LOADED=1, KV q8_0) |
| Python | 3.12 |
| Framework | Flask + Gunicorn (gthread, 1W×96T, preload_app=False) |

## Documentation

| Document | Description |
|----------|-------------|
| [Image API Guide](docs/IMAGE_API_GUIDE.md) | 이미지 생성 상세 가이드 |
| [Image API Quick Ref](docs/IMAGE_API_QUICKREF.md) | 이미지 API 빠른 참조 |
| [Web Integration Guide](docs/WEB_INTEGRATION_GUIDE.md) | 웹 연동 가이드 |
| [Model Upgrade Report](docs/MODEL_UPGRADE_REPORT.md) | 한국어 모델 벤치마크 (6종) |
| [Medical Imaging Models](docs/MEDICAL_IMAGING_MODELS.md) | 의료 영상 AI 모델 검토 보고서 |
| [**아키텍처 & 기능 명세**](docs/ARCHITECTURE.md) | **시스템 구조, 16 Blueprint, 개선 로드맵** |
| [**통합 API 레퍼런스**](docs/API_REFERENCE.md) | **전체 ~255 API 통합 문서** |
| [**Dashboard API Guide**](docs/DASHBOARD_API_GUIDE.md) | **통합 대시보드 8 API (외부 시스템 연동, ETag/304/SSE)** |
| [Coding API Guide](docs/CODING_API_GUIDE.md) | Coding AI 16 API 레퍼런스 (생성/리뷰/RAG) |
| [Medical API Guide](docs/MEDICAL_API_GUIDE.md) | 의료 API 레퍼런스 (22 API, 사용 예제) |
| [Benchmark v2](docs/benchmark_v2.json) | 의료 모델 벤치마크 — v1(57.1) vs qwen(56.0) vs v2(40.8) |

## Changelog

> **v2.68.0 ~ v2.110.0**: 상단 [최신 릴리즈](#최신-릴리즈) 표 + 버전별 상세 `docs/RELEASE_v2.*.md` 참조.
> 아래는 v2.67.0 이하 히스토리. 전체 체인지로그는 `GET /api/version`.

### v2.67.0 (2026-06-02) — MCP 서버 노출 (Model Context Protocol)

**MCP Server (routes/mcp.py, POST /mcp)**
- MCP Streamable HTTP (JSON-RPC 2.0) — 외부 AI 에이전트 연동
- config/tools.json 20개 도구를 MCP inputSchema로 자동 변환
- initialize, tools/list, tools/call, resources/list, resources/read, ping
- Claude Desktop/Code: `claude mcp add werub-ai --transport http https://example.org/ollama/mcp`
- 상세 가이드: `docs/MCP_SERVER_GUIDE.md`

### v2.66.0 (2026-06-02) — Meeting Phase 2 + Search Blueprint 독립

**Meeting Phase 2 (core/meeting_diarize.py, core/meeting_vision.py)**
- SpeechBrain ECAPA-TDNN 화자 분리 (HF 토큰 불필요, GPU 가속)
- 키프레임 추출 (scene detect + interval) + qwen3-vl:8b Vision 슬라이드 분석
- 파이프라인 확장: STT → 화자분리(병렬) → Vision(병렬) → LLM 종합 요약
- DB 스키마 확장: speaker_segments, speakers_detected, diarize_duration_ms
- Phase 1 하위 호환: diarize/vision 옵션 false면 기존 동작

**Search Blueprint (routes/search.py, 5 API)**
- orchestrator.py에서 독립 분리 → 30번째 Blueprint
- POST /api/search (일반), /api/search/news, /api/search/science, /api/search/it
- GET /api/search/status

### v2.65.0 (2026-06-02) — SearXNG 실시간 웹 검색 연동

**SearXNG Web Search (core/web_search.py, Docker)**
- GPU 서버에 SearXNG Docker 설치 (localhost:8888, Google/Bing/DuckDuckGo 집계)
- core/web_search.py: SearXNG JSON API 래퍼 (검색/상태확인)
- routes/orchestrator.py: `search://` 프로토콜 + `/api/search`, `/api/search/status` API
- config/tools.json: `web_search` 도구 등록 (21번째 도구)
- Tool Calling에서 qwen3:8b가 실시간 정보 질의 시 자동 선택
- GPU 터널 plist: 8888 포트 포워딩 추가

### v2.64.0 (2026-06-02) — Tool Calling 네이티브 지원 + force_korean_json

**Tool Calling (config/tools.json, core/tool_registry.py + core/tool_executor.py)**
- Orchestrator에 LLM Tool Calling 통합: 3단계 폴백 (워크플로우→Tool Calling→General Chat)
- config/tools.json: 도구 20개 설정 파일 기반 관리 (의료8+한국어5+글쓰기4+유틸3)
- core/tool_registry.py: ToolRegistry 클래스 (로드/Ollama 변환/reload/enabled 필터)
- core/tool_executor.py: LLM↔도구 반복 루프 (동기+SSE, max_tool_calls=5, qwen3:8b)
- GET /api/orchestrator/tools, POST /api/orchestrator/tools/reload API
- _safe_force_korean: Tool Calling 응답 과잉 제거 방지

**force_korean_json 선별 후처리**
- core/korean.py: force_korean_json() 헬퍼 (JSON 내 한국어 텍스트 필드만 선별 적용)
- medical_advanced.py: 12개 엔드포인트 적용 (auto-record, predict 3종, cross-validate 등)
- core/reasoning_chain.py: 5단계 추론 + 앙상블 결과에 force_korean_json 적용
- routes/orchestrator.py: chat/stream/run 워크플로우 결과에 force_korean/force_korean_json 적용

### v2.63.0 (2026-06-02) — Meeting Analysis API (화상회의 녹화본 AI 분석)

**Meeting Analysis Blueprint (routes/meeting.py, 6 API)**
- 화상회의 녹화본(MP4) AI 분석: STT(faster-whisper) → LLM 요약(qwen3:8b) → 콜백
- hospital-jitsi 연동: S3 Presigned URL 다운로드 + callback POST
- core/meeting_stt.py — faster-whisper 래퍼 (large-v3, GPU, 30분 청크 분할)
- core/meeting_pipeline.py — 순차 파이프라인 오케스트레이터
- 동시 작업 2건 세마포어, 콜백 3회 지수 백오프 재시도
- 한국어 후처리 force_korean 적용 (요약/액션아이템/하이라이트)

### v2.62.0 (2026-06-02) — Counseling API + 신규 모델 3종 + 한국어 후처리 전면 보강

**Counseling Blueprint (routes/counseling.py, 6 API)**
- mindwell(상담) → qwen3:8b(자연화) → force_korean → korean_humanize 오케스트레이션
- 위기 키워드 감지 → 자살예방상담전화(1393) 자동 안내
- 세션 관리 (24시간 TTL), NDJSON 스트리밍, 서킷 브레이커

**신규 모델 3종 병행 운영**
- qwen3:8b (5.2GB) — counseling 자연화, thinking 모드 추론
- phi4-reasoning (11GB) — 의료 교차검증 (cross-validate 3번째 모델)
- qwen3-embedding:8b (4.7GB) — RAG 임베딩 4096차원 (bge-m3 폴백)

**한국어 후처리 전면 보강**
- `postprocess_korean()` 통합 함수 추가 (core/korean.py)
- 10+ 엔드포인트 force_korean 적용 (ollama/chat/writing/story/novel/hr_law/game/triage)
- 캐시/세션 오염 수정: 스트리밍 응답 force_korean 후 저장
- validate≠filter 혼동 수정: force_korean → quality_check 순서 통일

### v2.61.0 (2026-06-01) — Korean Language API 24건 독립 Blueprint + 소설 분석 강화

**Korean Language Blueprint (routes/korean.py, 24 API)**
- 독립 Blueprint로 분리 (story.py에서 이관 + 14건 신규)
- `POST /api/korean/pipeline` — 전체 파이프라인 (필터→분석→교정→가독성→응집성)
- `POST /api/korean/novel-diagnosis` — 소설 종합 진단 8항목 일괄 + A~D 등급
- `POST /api/korean/stats` — 어휘 빈도 TOP-20, 문장 유형, 대화 비율, 어휘 풍부도
- `POST /api/korean/cohesion` — 응집성 분석 (TAACO: ASO/접속사/주제 일관성)
- `POST /api/korean/honorific` — 경어법 5단계 판별 + 혼용 감지
- `POST /api/korean/repetition` — 반복 탐지 (중복 문장/어구/어미 단조)
- `POST /api/korean/pov` — 시점 판별 (1인칭/3인칭/혼합)
- `POST /api/korean/sensory` — 감각 묘사 5감 + Show Don't Tell 점수
- `POST /api/korean/expressiveness` — 의성어 50종 + 의태어 40종 생동감 분석
- `POST /api/korean/style-transfer` — 문체 변환 6종 (존댓말/반말/문어/구어/나레이션/대화)
- `POST /api/korean/dialogue-analysis` — 대화문 대사/묘사/내면 비율 + 화자 식별
- `POST /api/korean/character-voice` — 캐릭터 말투 일관성 (종결어미 + 격식/비격식)
- `POST /api/korean/diff` — 텍스트 비교 (문장 단위 diff + 변경률 가드)
- `POST /api/korean/feedback` — AI 5항목 글쓰기 피드백 (정량 분석 선행 + LLM)

**통합 포인트 확대**
- /api/chat (ollama.py) humanize 적용 누락 수정
- medical_law api_ask humanize 추가
- 총 21곳 적용 (story 2, novel 8, writing 2, chat 2, medical 5, dashboard 2)

**문서**
- `docs/KOREAN_API_GUIDE.md` — 24건 전체 명세 + 아키텍처 + 통합 포인트 + 출처

### v2.60.0 (2026-06-01) — KSS 문장 분리기 + 가독성 점수

**KSS 문장 분리기 통합**
- `_split_sentences()`를 KSS pecab 백엔드로 업그레이드 (경어/방언/신조어 대응)
- 22개 메트릭 전체 정확도 향상, 미설치 시 regex fallback

**한국어 가독성 점수 (Korean Readability Index)**
- `POST /api/korean/readability` — 6개 지표 기반 가독성 분석
- 5등급: 초등(80+) / 중등(65+) / 고등(50+) / 전문(35+) / 학술
- 개선 제안 자동 생성 (문장 길이, 한자어 비율, 종결 다양성 등)

### v2.59.0 (2026-06-01) — 의료 교차 검증 + 심층 분석 + 소설 퇴고 고도화

**의료 교차 검증 (Cross-Validation)**
- `POST /api/medical/cross-validate` — 2~3개 모델 병렬 독립 진단 + 합의 도출 (agreement_rate %)
- 기본: werub-medical + Med42 + qwen2.5:14b, ThreadPoolExecutor 병렬

**MedGemma 심층 분석**
- `POST /api/medical/deep-analysis` — 4종 분석 (comprehensive/differential/treatment_plan/literature_review)
- medgemma:27b 기본, ICD-10 포함 구조화 JSON, 야간/비피크 권장

**의료 한국어 자연화 적용**
- medical_summarize, medical_explain, medical_research, patient_education — quality.humanize 필드

**소설 퇴고 고도화**
- intensity 3단계 (gentle/standard/aggressive) + focus 영역 선택
- 장르별 퇴고 규칙 7종 자동 적용 (fantasy~web_novel)
- 변경률 가드 (30% 주의, 50% 과윤문 경고) + 등급 판정 A~D
- 퇴고 전후 quality 비교 (risk_score_delta, interference_delta)

### v2.58.0 (2026-06-01) — 한국어 품질 엔진 + 소설 일관성 엔진

**한국어 자연화 메트릭 (im-not-ai v2.0 MIT 이식)**
- `core/korean_humanize.py` — 22 메트릭 함수 (v1.6 8종 + v2.0 post-editese 14종)
- KatFish 코퍼스 baseline (인간 470편 vs AI 1,624편), 연결어미 쉼표 4.84배 최강 신호
- S1 18종 + S2 12종 패턴 탐지 (번역투/AI관용구/구조/리듬/hedging/접속사)
- `risk_band=high` 시 S1 규칙 삽입 후 자동 1회 재생성
- 적용: story(2), novel(8), chat(1), writing(2), dashboard(2) — 총 15 통합 포인트

**한국어 맞춤법 검사 (Ollama LLM 자체, 외부 API 없음)**
- `core/korean_spellcheck.py` — qwen2.5:7b 기반 로컬 교정
- 장문 자동 분할 (500자 단위), 교정 전후 diff 분석

**NovelWriter 3패턴 이식 (KudoShusak, MIT)**
- 캐릭터 경험 추적 — 챕터별 각 캐릭터의 경험/감정/성장 자동 추출·누적
- 챕터 스냅샷 — 집필 시점의 프로젝트 상태 자동 저장
- 컨텍스트 빌더 L4 강화 — 원문 잘라내기 → 자동 요약 + 캐릭터 경험 + 원문 보조 3계층

**신규 API (5건)**
- `POST /api/korean/analyze` — 텍스트 품질 전체 분석 (22 메트릭 + z-score)
- `GET /api/korean/rules` — S1/S2 규칙 목록
- `POST /api/korean/spellcheck` — 맞춤법 검사
- `GET /api/novel/character/experiences/{id}` — 캐릭터 경험 추적
- `GET /api/novel/chapter/snapshot/{id}/{num}` — 챕터 스냅샷

**Dashboard 확장**
- `GET /api/dashboard/quality` — 한국어 품질 시스템 현황
- summary에 `korean_quality` 섹션 추가

**문서**
- `docs/01-plan/features/korean-humanize.plan.md` — 기획서
- `docs/02-design/features/korean-humanize.design.md` — 설계서
- `docs/03-analysis/humanize-kr-analysis.md` — im-not-ai 심층 분석 (12장)
- `docs/03-analysis/humanize-kr-changelog.md` — 업스트림 추적 로그

### v2.57.0 (2026-05-31) — 통합 대시보드 API + 외부 시스템 연동

**통합 Dashboard API (8 신규 엔드포인트)**
- `GET /api/dashboard/summary` — 서버/모델/큐/파이프라인 전체 현황 한눈에
- `GET /api/dashboard/server` — CPU/GPU/RAM/디스크/프로세스 상세
- `GET /api/dashboard/servers` — 4서버(GPU/iMac/Prod/HIS) 병렬 비교 (ThreadPoolExecutor)
- `GET /api/dashboard/models` — Ollama 모델 + GPU 리소스 매니저
- `GET /api/dashboard/queue` — 큐 상태/통계/ETA + 모델별 tok/s
- `GET /api/dashboard/orchestration` — 워크플로우 목록 + 실행 이력
- `GET /api/dashboard/pipeline` — 약물 파이프라인 상태/통계/스케줄
- `GET /api/dashboard/stream` — 통합 SSE 실시간 스트림 (monitor + 주기적 stats)

**전송 최적화**
- ETag + 304 Not Modified — 데이터 변경 없으면 body 전송 0
- `?fields=server,queue` — 필요한 필드만 선택 전송
- `Cache-Control` 헤더 — 브라우저/프록시 레벨 캐시
- TTL 메모리 캐시 (3~60초) — DB/GPU 반복 조회 부하 방지

**인증 체계 정리**
- 기존 대시보드 HTML (세션 로그인) vs API 엔드포인트 (Bearer 토큰) 이원 구조 문서화
- 기존 40+ 개별 API도 Bearer 토큰으로 외부 호출 가능함을 가이드에 명시

**문서**
- `docs/DASHBOARD_API_GUIDE.md` — Python/JS/cURL 예제, 인증 비교표, 혼합 사용 가이드

### v2.56.0 (2026-05-26) — 글쓰기 AI 보강 + LibriScribe 연동 + 테스트 63건

**글쓰기 AI 보강 (3 신규 API)**
- `POST /api/writing/craft/stream` — 3모델 SSE 스트리밍 (단계별 진행률)
- `POST /api/writing/translate` — 7개 언어 번역 (formal/casual/literary 톤)
- `GET /api/writing/languages` — 지원 언어 목록
- SEO 폴백 스키마 강화, story.py 분기 한국어 필터

**LibriScribe 연동 확장 (7건, 100% 하위 호환)**
- E1. `chapter/write` — `external_context` 파라미터 (외부 컨텍스트 주입)
- E2. `GET /api/novel/project/:id/snapshot` — 프로젝트 전체 상태 1회 조회
- E3. `score` — `include_web_novel_metrics` (hook/cliffhanger/pacing/growth 4축)
- E4. `revise/consistency` — `focus_chapter` + `context_summary` (단일 회차 빠른 검증)
- E5. `POST /api/novel/extract/metadata` — 텍스트 엔티티 자동 추출
- E6. `chapter/write` — `episode_structure` (회차 구조 지시)
- E7. `_orchestrated_write` — `return_detail` (단계별 타이밍/토큰 상세)

**테스트 스위트 구축 (63건)**
- Core 모듈 16건 + API 28건 + Novel 확장 19건, GPU 서버 전체 통과
- `requirements.txt` 신규 생성, 문서 갱신 (API_REFERENCE + ARCHITECTURE)

### v2.55.0 (2026-05-26) — Admin Console 확장 (Model Manager + Orchestration Editor)

**독립 관리 페이지 2종 추가**
- `/model-manager` — Ollama 모델 목록/Pull/삭제/GPU 상태 (4탭: Overview/Pull/GPU/Settings)
- `/orchestration-editor` — 워크플로우 CRUD/실행/이력 (4탭: Workflows/Editor/History/Run)
- admin.html 탭 바에 Model Manager ↗, Orchestration ↗ 링크 추가

**타임아웃 강화**
- `core/llm_resilience.py` — gemma4(180s), exaone-deep(90s), dnotitia/dna(90s) 추가

### v2.54.0 (2026-05-22) — Novel Writer Phase 2 완료 (+62 API)

**Novel Writer 대규모 확장 (16 → 78 API)**
- 챕터 버전 관리 (생성/비교/복원/삭제)
- 챕터 요약 자동 생성 + CRUD
- 타임라인 이벤트 관리 (생성/수정/삭제/범위 조회)
- 시리즈/볼륨 관리 (시리즈 CRUD + 볼륨 연결/순서/삭제)
- 소스 자료 관리 (등록/수정/삭제/검색)
- 컴포넌트 라이브러리 (등록/수정/삭제/검색/사용 추적)
- 레시피 시스템 (생성/수정/삭제/실행/복제)
- 프로젝트 참조 + 레시피 연결
- story.db 14개 신규 테이블 (총 22 테이블: story 8 + novel 14)
- routes/novel.py 1,576줄 → 6,393줄
- Admin Console Story 섹션 8페이지 (editor, world, novel, decompose, series, analyze, recipe, assistant)

**E2E 테스트 + 버그 수정 (5건)**
- `_parse_json_response` 3단계 폴백 강화 (regex/strip/raw)
- scaffold: ≤12챕터 시 FAST_MODEL 사용 (속도 최적화)
- JSON 출력 API에서 force_korean 제거 (JSON 구조 보호)

### v2.50.2 (2026-05-22) — 한국어 후처리 루틴 보강

**enhanced chat + 오케스트레이션 한국어 처리 추가**
- `/api/chat/enhanced`: 단계별 `force_korean` + 최종 `korean_quality_check` 반환
- `/api/orchestration/run`: 한국어 입력 감지 → 단계별 + 최종 `force_korean`
- `_run_single_stage()`: `apply_korean` 옵션 — EN 전용 모델(Med42 등) 자동 제외
- 중국어/일본어 혼입 방지 검증 통과 (4건 테스트, cn=0 jp=0)

### v2.50.1 (2026-05-22) — 오케스트레이션 안정성 + 문서 정비

**안정성 강화**
- Semaphore(1) 동시 실행 제한 — run/test/compare API (VRAM OOM 방지)
- DB 30일 자동 정리 — orchestration_runs 10% 확률 트리거
- try/finally Semaphore release 보장 (데드락 방지)

**문서 정비**
- API_REFERENCE 본문: Orchestration Platform 13 API + Enhanced 2 API 전체 추가
- README Key Features: 오케스트레이션 상세 사용법 (생성/실행/테스트/에러정책/A/B/버전)
- Chat Domain Routing 요약 추가
- 기획서 2건: 오케스트레이션 v2 (SSE/분기/병렬/스케줄/AI관리), 검토 보완 (8개 기술과제)

### v2.50.0 (2026-05-22) — 오케스트레이션 플랫폼 + 강화 API + 34 Models

**오케스트레이션 플랫폼 (13 API)**
- 커스텀 파이프라인 CRUD — 최대 5단계, on_fail/retry/fallback_model 에러 정책
- `POST /test` — validate(모델+VRAM 확인) / dry_run(1단계) / full_test(전체+품질)
- `POST /run` — debug=true 단계별 중간 output, 실행 이력 DB 자동 기록
- `POST /compare` — 파이프라인 A/B 비교 (속도/토큰/출력)
- `PUT /pipeline` + `GET /versions` + `POST /rollback` — 수정 시 자동 버전 백업 + 복원
- `POST /clone` — 파이프라인 복제 (builtin 포함)

**강화 API (다중 모델 오케스트레이션)**
- `POST /api/chat/enhanced` — medical(3단계)/coding(2단계)/writing(3단계)/legal(2단계)
- `POST /api/writing/craft` — qwen2.5:14b(초안) → exaone-deep(구조) → dna(다듬기)

**채팅 도메인 라우팅**
- 6도메인 자동 감지 + 12+ 모델 라우팅 + 멀티모달
- 한국어 검증 스킵, 컨텍스트 윈도우 관리, 도메인별 시스템 프롬프트

**모델 6종 신규 (총 34 모델)**
- 글쓰기: exaone-deep:7.8b, dnotitia/dna:8b, gemma4 (26B MoE)
- 비전: qwen3-vl:8b, minicpm-v:8b, deepseek-ocr:3b

### v2.49.0 (2026-05-22) — 채팅 도메인 라우팅 + 강화 API + 글쓰기/비전 모델 6종

**채팅 도메인 라우팅**
- 6도메인 자동 감지 (medical/legal/coding/writing/vision/general)
- 도메인별 전문 모델 자동 라우팅 (2개 → 12+ 모델)
- 도메인별 시스템 프롬프트, 한국어 검증 스킵, 컨텍스트 윈도우 관리
- 멀티모달 — images 필드 감지 시 비전 모델 자동 전환

**강화 API (다중 모델 오케스트레이션)**
- `POST /api/chat/enhanced` — 도메인별 2~3모델 순차 파이프라인
  - medical: werub → Med42 → werub (3단계, ~25초)
  - coding: qwen3-coder:30b → qwen2.5-coder:14b (폴백, MoE ~5초)
  - writing: qwen2.5:14b → exaone-deep → dna (3단계, ~25초)
  - legal: qwen2.5:14b → exaone-deep (2단계, ~15초)
- `POST /api/writing/craft` — 3모델 고품질 글쓰기 (초안→구조→다듬기)

**모델 6종 신규 설치 (총 34 모델)**
- 글쓰기: exaone-deep:7.8b, dnotitia/dna:8b, gemma4 (26B MoE)
- 비전: qwen3-vl:8b, minicpm-v:8b, deepseek-ocr:3b

### v2.48.0 (2026-05-22) — 의료 모델 11종 + 도메인 라우팅 + 신규 API 5개

**의료 모델 6종 신규 설치**
- Med42 8B (임상 QA), OpenBioLLM 8B (PubMed), BioMistral 7B (연구)
- LLaVA-Med 7B (의료 영상 VLM), MediChat 8B (환자 교육), MedGemma 27B (심층 추론)

**도메인 라우팅 시스템**
- MEDICAL_MODELS 레지스트리 11개 + DOMAIN_MODEL_ROUTING 20도메인
- 한국어/영어 입력 자동 감지 → 최적 모델 분기
- 모델 폴백 체인 + 설치 확인 캐시 (5분 TTL)

**신규 API**
- `POST /api/medical/clinical-qa` — Med42 기반 의료인 전문 QA
- `POST /api/medical/drug-analysis` — 약물 심층 분석 (quick/standard/comprehensive 3 depth)
- `POST /api/vision/medical/dual-read` — MedGemma + LLaVA-Med 이중 판독
- `POST /api/medical/model/compare` — 다중 모델 비교 (최대 3개)
- `POST /api/medical/model/benchmark` — 10 표준 증례 ICD 정확도 벤치마크

**기존 API 개선**
- `/api/medical/analyze` — Med42로 감별진단 강화 (영어 입력 시)
- `/api/medical/explain` — MediChat으로 환자 설명 전문화
- `/api/medical/reference` — OpenBioLLM으로 문헌 참조 강화
- GPU 스케줄러 MODEL_VRAM_ESTIMATE 11개 모델 추가

**벤치마크 실측 (10 표준 증례 ICD 정확도)**
- Med42 8B: **80%** (8/10) — 감별진단 1순위 확정
- werub-medical:7b: **60%** (6/10) — 한국어, 속도 30% 우위
- meditron 7B: 0% — ICD 미포함 (서술형), 약리학 전용
- Med42 + werub 합집합: **90%** (9/10) — 상호 보완 실증

### v2.47.0 (2026-05-22) — Admin Console 고도화 + 3콘솔 분리 + Image Fuzzy Match

**Admin Console — 3개 독립 콘솔 분리**
- Medical AI / HR Law / Game NPC 각각 전용 사이드바 + 독립 진입점
- 하단 Switch 메뉴로 콘솔 간 이동, 홈은 3개 콘솔 선택 화면
- 메인 대시보드에 Medical AI ↗ / HR Law ↗ / Game NPC ↗ 개별 링크

**Medical Dashboard 고도화**
- GPU/VRAM 상태, Queue 상태, Drug Pipeline 상태 실시간 폴링 (10초)
- 5개 메트릭 카드 (Active Model, Drug DB, Interactions, Queue, Throughput)
- Pipeline History 테이블, Medical 모델 목록 (active/loaded 뱃지)

**Reasoning Chain 강화**
- 모델 선택 드롭다운 (werubmedv2, v1, qwen2.5, medgemma 등 6종)
- 세션 내 히스토리 저장 + 이전 결과 재보기
- A/B 모델 비교 뷰 (주진단, 긴급도, ICD, 소요시간, 토큰)

**Predict Dashboard 개선**
- SVG 원형 게이지로 리스크 점수 시각화
- Risk Factor 기여도 바 차트 (정렬 + 색상 구분)
- 예측 히스토리 패널 (클릭으로 결과 재로드)

**이미지 모델명 fuzzy 매칭**
- 3단계 매칭: exact id → exact checkpoint → fuzzy prefix
- 미매칭 시 400 에러 + 사용 가능 모델 목록 반환

**기타**
- Admin Console 정적 파일 rate limit 제외 (@limiter.exempt)

### v2.46.0 (2026-05-21) — Admin Console + Image Gen Desktop + Multi-Server Monitoring

**Admin Console — Medical AI 5페이지 (신규)**
- `/medical/dashboard`: 모델 상태, Drug DB 통계, 최근 활동, Quick Access
- `/medical/reasoning`: 5단계 추론 체인 (SSE 스트리밍 + 동기), 파이프라인 시각화
- `/medical/drugs`: 약물 검색 + Tier 뱃지 + 상호작용 조회
- `/medical/predict`: 재입원/합병증/약물부작용 예측 3종 탭 + 위험도 게이지
- `/medical/triage`: 증상 → 진료과 추천 + 대화형 추가 질문
- Sidebar에 Medical AI 그룹 최상단 배치 (총 21페이지)
- 컴포넌트: ReasoningPipeline, StepAccordion

**Image Gen 데스크톱앱 (신규, scaffold)**
- Tauri v2 + Vite + vanilla JS
- API client: generate/poll/gallery/models/characters/storage
- 서버 URL/API Key localStorage 저장
- 갤러리 그리드 + 이미지 프리뷰 모달

**멀티서버 모니터링 (신규)**
- `dashboard_all.html`: GPU + iMac + HIS/PACS + Production 4서버 그리드
- `monitor.html`: HIS/PACS + Production 상태바 추가
- `server-stats.py`: 원격 서버용 경량 모니터링 에이전트
- `deploy-stats-agent.sh`: stats agent 원격 배포 스크립트

**인프라**
- SSH 터널 launchd plist 3개: GPU(9102), HIS(9101), Prod
- `tunnel.env` HIS 서버 설정 추가, `setup-his-tunnel.sh`

**벤치마크 v2**
- werub-medical:7b(v1) 57.1 > qwen2.5:7b 56.0 > werubmedv2(v2) 40.8
- v2 문제: max_tokens 도달 11/14건, JSON 구조화 실패 → v1 유지 권장

**파인튜닝 학습 데이터**
- `training/data/medical/` train.jsonl + eval.jsonl (232건)

### v2.45.0 (2026-05-21) — Drug Batch Pipeline + Provenance + Night Mode

**Drug Data Pipeline (신규)**
- 자동 수집→LLM 정제→4-Layer 검증→DB 반영 파이프라인
- core 4모듈: `drug_batch_scheduler`, `drug_collector`, `drug_enricher`, `drug_validator`
- Blueprint 2개: `drug_batch` (12 API), `provenance` (6 API)
- 전용 페이지 `/drug-pipeline` (5탭 SPA: Overview/Pipeline/Staging/Provenance/Settings)

**Data Provenance (신규)**
- 4-Tier 출처 분류: T1(공인기관) → T2(가이드라인) → T3(수동) → T4(AI생성)
- 신뢰도 점수 (0.0~1.0): source_tier × enrich_method × verify_bonus
- `data_provenance` 테이블: 모든 데이터 변경 이력 추적
- drug_codes 출처 8컬럼 추가 + 기존 400건 소급 매핑
- 원소스 URL 링크 (MFDS, FDA, ESC, ACC/AHA 등 10개 기관)

**Night Mode (신규)**
- 야간 자동 실행 (22:00~06:00): limit 200, model qwen2.5:14b, priority 3
- 30분 간격 자동 재실행, 야간 최대 10회
- GPU off_hours 프로파일 연동
- Settings 탭에서 ON/OFF 토글

**Drug Data (확충)**
- 약물 코드 159→402건 (+243, 310개 카테고리)
- 항암/표적, 면역/류마티스, 마취/수술, 항결핵 등 신규 계열
- 원소스 세분화: MFDS 331, NCCN/FDA 24, ACR/EULAR 9, ASA 7, ESC 5, ADA/KDA 4

**Fine-tuning v2 (재학습)**
- 학습 데이터 53→232건 (약물 100, 감별진단 41, 상호작용 40, SOAP 20, 설명문 14)
- QLoRA 3 epochs, loss 2.38→0.70, 115초 완료
- `werubmedv2:latest` Ollama 등록 (Q4_K_M, 4.5GB)
- 약물 JSON 구조화 출력 v1 실패→v2 정상

**기타**
- CSRF 예외: `/login`, `/logout` form POST 허용
- Pipeline Live Progress: 4-Phase 아이콘+화살표+애니메이션+경과시간
- Run History 확장: Model, Enrich, Skip, Tokens 컬럼
- admin.html에 Drug Pipeline 탭 링크 추가

### v2.40.0 (2026-05-20) — Writing AI + Medical Precheck + Performance

**신규 기능 (19건)**
- **Writing AI Blueprint** (6 API) — 톤앤매너 6종, SEO 분석, 글쓰기 코칭, 스트리밍
- **Medical Image Precheck** — 의료 영상 사전 검증 (품질/분류/모달리티)
- **Medical Batch API** — 최대 20건 동시 처리
- **Vision Streaming** — SSE 토큰 단위 영상 판독
- **Fine-tuning 관리 UI** — /medical-training (데이터 준비/학습/배포)
- **Fine-tuning 파이프라인** — QLoRA + GGUF + Ollama deploy

**성능 개선 (7건)**
- SOAP 병렬 생성 (12초→4초)
- 의료 프롬프트 캐싱 (TTLCache 200, 1h)
- 경량 모델 라우팅 (explain/reference → 7b)
- RAG 임베딩 캐시 (500ms 절감)
- gzip 응답 압축 (70-80% 감소)
- Gunicorn threads 16→24
- Vision 에러 코드 10종

**품질 52→93/100 (11건)**
- 보안 @require_auth 전체 적용
- bare except 46→0, silent pass 34→0
- conn.close() try/finally ~90건
- datetime.utcnow() 29→0

**데이터 보충**
- 약물 상호작용 61→97건, 삭감사유 34→75건
- Triage Structured Output (force_json_array)

### v2.39.0 (2026-05-18) — P5~P8: 17건 품질/성능/의료/보안 개선
- **P8-2 Chat History Injection**: 스트리밍/비스트리밍 모두 세션 히스토리 LLM 주입
- **P8-3 SOAP Split Generation**: S/O/A/P 각각 별도 LLM 호출 (split_soap=true)
- **P8-4 Code Security Scan**: 13개 OWASP 패턴 자동 감지 (generate + review)
- **P8-5 Streaming Session Save**: 스트리밍 모드에서도 세션 저장
- **P8-6 Multilingual Disclaimers**: 의료 면책 5개 언어 (ko/en/zh/vi/ja)
- 스트리밍 히스토리 주입 + 코드 리뷰 보안 스캔

### v2.38.0 (2026-05-18) — P5~P7: 품질 + 성능 + 의료 10건 개선
- **P5-1 Structured Output**: generate_with_validation 검증+재시도 (medical/coding 4개 엔드포인트)
- **P5-2 RAG Smart Chunking**: 5가지 청킹 전략 (paragraph/sentence/fixed/code/law) + 병합 검색
- **P5-3 Orchestrator Direct Call**: test_request_context 우회 (32ms→<1ms/호출)
- **P6-1 Triage Multi-symptom**: 순서 무관 조합 매칭 + 15개 조합 룰 추가
- **P6-2 ComfyUI Preload**: 3 모드 (on-demand/preload/manual) + idle timeout 설정
- **P7-1 ICD-10 Validation**: KCD 469건 DB 대조 + fuzzy prefix 매칭
- **P7-2 Drug Interactions**: 61건 DB + POST /api/medical/drug-interactions
- **P7-3 Orchestrator Expansion**: summarize/analyze 직접 호출 추가
- **P7-4 Code Syntax Validation**: ast.parse + 재시도 (Python 코드 생성)
- **P7-5 Chat Session API**: GET /api/chat/sessions, messages, DELETE

### v2.36.0 (2026-05-14) — P4: Chat Quality + Server Memory
- **P4-1 Chat Quality**: 한국어 검증 (30%), 모델 자동선택, 재시도, mtime 캐싱
- **P4-2 Chat Server Memory**: 세션 관리, 메시지 저장, LLM 압축 (core/chat_memory.py)
- DB: `chat_sessions`, `chat_messages` 테이블 + 6 인덱스
- API: `/api/chat`에 `session_id`/`user_id` 선택 파라미터 추가 (하위 호환)
- Match Rate: P4-1 94.2%, P4-2 98.7%

See `/api/version` for full changelog.


---

## Backlog (확장 시 진행)

| 항목 | 트리거 | 기획서 |
|------|--------|--------|
| werubmedv2 v3 재학습 | JSON 구조화 50%+, num_predict 제한 | [benchmark](docs/benchmark_v2.json) |
| Image Gen 데스크톱앱 개발 | 캐릭터/갤러리 기능 | image-gen/ |
| HIS/PACS 통합 | 병원 연동 수요 | 6-8주 |
