# 세션 보고서 2026-06-11 — 환자 아바타 R1 신규구축·라이브 + R2 PoC + 모델검토 + L4

**범위**: 커밋 ~9건(df680d7~a723f6f), 전부 GPU 배포·E2E 검증. 외부 협업(트윈 디지털 트윈팀) 4라운드 회신.
**한 줄**: 트윈 아바타 협업에서 **R1 생리모델(werub-physio-v1)을 한 세션에 기획→구현→배포→실데이터 정밀화까지 완주**하고, R2(영상→메시) 파이프라인을 AI측 전구간 자율 실증. 부수로 Qwen3.6/MiniMax 모델검토·X7 RAG검증·L4 픽스.

---

## 1. 환자 아바타 공동진화 — 트윈 협업 (메인)

GPU 없는 트윈팀이 RTX5080 AI 서버에 전신 생리(R1)·환자특이 3D 메시(R2)·심장 EP(R3) 연산을 요청. 상세 [[project_patient_avatar]], 회신서 `docs/01-plan/references/avatar-reply{,2,4}-to-twin.md`.

### R1 — werub-physio-v1 신규 구축·라이브 (종결)
- **협업 4라운드**: 1차 회신(전부 신규구축·R1 자체ODE 역제안)→트윈 답변→2차(커플링 2개 수용·R2 서명URL)→트윈 reply3(0D수식·샘플·8문항)→**R1 구현·4차 회신**.
- **구현**(cd38042): `core/physiology.py`(순수 numpy·CPU·GPU불요·결정적·비식별·미저장) + `POST /api/medical/physiology`(+`/stream` NDJSON) in `medical_advanced.py`.
  - 트윈 0D(Windkessel/renal_pk) 격상 + **계통간 커플링 2개**(트윈 0D엔 없는 차별점):
    ① `egfr_pk_cardio` — 신배설 심혈관약 → eGFR 축적비 → MAP/HR. **단조 구배**(eGFR 20→MAP 92.3/55→98.7/120→104.0).
    ② `glucose` — 경구혈당강하제 → onset 1차동역학.
  - series 8키(hr/map/sbp/dbp/rr/spo2/glucose_mgdl/cardiac_output_lmin) = 트윈 계약. 체중·키 HIS미노출→표준추정 폴백.
- **🔧 약물명 정규화**(a723f6f) — 트윈 실데이터(P2026-0001) 플래그: 한글 약물명("메트포르민")이 영문 INN 미매칭→커플링 빈배열. `_normalize_drug`(한글 17+브랜드 11→INN, 용량/제형/괄호 제거, matched_from 원문보존, 미매칭=정직). 라이브: "메트포르민"·"리시노프릴 10mg"→커플링 발화.
- **트윈측 A단계 라이브**: AIClient.physiology()+/avatar?physio=true, series→organs 매핑, 0D폴백, name/mrn 미전송. **R1 3문항 전부 합의**(8키·체중 남70/여58·8채널 충분). → **R1 사실상 종결.**
- 검증: 회귀 test_physiology 10, 샘플 A/B E2E, /stream meta+frame+done, 비식별 거부 400, openapi 노출, health 200·NRestarts 0.

### R2 — 영상→메시 PoC (AI측 전구간 실증, 트윈 CT 대기)
- **VRAM 실측**(df680d7 `r2-totalsegmentator-vram-poc.md`): 격리 venv `~/ts_poc_venv`(TotalSegmentator 2.14.0, 운영 무영향). **대형 CT 정정**: full(1.5mm) 15,759MiB(여유0.5GB=OOM임박)·fast(3mm) 14,401MiB(여유1.9GB=안전), LLM 14b 상주 기준. **정책=fast 기본·full은 온디맨드 언로드.**
- **메시 생성 E2E**(8b21475): 마스크→`skimage.marching_cubes`→`trimesh`→`.glb` 유효(177KB·V3832/F7260·CPU<0.1s·비식별). "marching cubes=CPU·VRAM무관" 실증.
- **인터페이스 확정**: 트윈 서명URL(NIfTI.gz)+비동기 202+폴링(콜백 미채택), fast 합의, 폐엽별 멀티지오메트리 .glb. **남은 것=트윈 공개 CT 1건 스테이징.**

### R3 — 심장 EP 후순위 보류(상호 동의).

---

## 2. 모델 검토 2건 (둘 다 도입 보류)

- **Qwen3.6-35B-A3B**(99→보류): 코딩 10케이스 100:100 동률이나 속도 13.5배 열세(18.3 vs qwen3-coder:30b 54.9 tok/s)+고난도(TTL LRU) 26배 열세. Q4도 24GB→로드26GB(43/57 CPU/GPU 분할, 16GB 초과). **코딩 1순위 qwen3-coder:30b 유지·삭제.** → [[project_model_benchmark]]
- **MiniMax-M2 시리즈**(검토만): 230B total/10B active MoE — active 작아도 전체 230B 상주(~115GB+) 필요라 16GB 물리적 미적재. + 비상업 라이선스. **pull 불필요·후보 제외.**

---

## 3. X7 — 팀 ns RAG 필터 라이브 검증 (✅ 팀공유 활성)
트윈 체크리스트 X7: `filter={ns:"team:*"}` 팀경계 격리 동작 확인 요청. GPU E2E: 같은 ns를 owner 다르게 색인 2건이 ns 필터에 함께 조회(팀공유✅)·owner 필터는 개인격리✅·neuro 경계 정확✅. 메커니즘=`_build_where` ns 동등조건. **트윈 실문서 `twinkb-1781133283678`이 이미 ns=team:cardio 사용 중** 확인. → [[project_his_integration]]

---

## 4. L4 — medical-law top_k 입력 안전화 (code-analyzer 백로그)
`int(data.get("top_k"))`가 비정수("abc"/5.5) 입력서 ValueError→500이던 것을 `_safe_top_k`(클램프 [1,10], 비정수→default)로 봉합. /ask·/ask/stream·/review 3경로. 회귀 +1. 라이브: top_k=abc/-5 둘다 200(과거 500). 잔여 백로그 M2/M3/L1=현 규모 무해 보류.

---

## 운영 검증
- 매 배포: push→GPU pull→import 드라이런→재시작→health 200·NRestarts 0. 회귀 test_physiology 10·test_medlaw 25 GPU 통과.
- **인시던트 교훈**: R2 세그 측정 중 ①SSH 장시간 요청 직접실행 끊김 ②세그 2개 동시실행 VRAM 측정오염+CPU후처리 행 → nohup+timeout 가드·순차 직렬·이중실행 금지 표준화.

## 잔여 / 다음
- **R1**: 종결(트윈 A 라이브). 누락 약물 발견 시 동의어 1줄 추가.
- **R2**: 트윈 공개 비식별 흉부 CT 1건 NIfTI.gz 서명URL → lungs 통합 최종확증(AI 파이프라인 실증 완료).
- **관측**: ✅ notice nightly **12:02 KST(03:02 UTC) 정상 발화 검증** — `0 ok/0 fail` 단일 발화(중복 0·에러 0·NRestarts 0). 0/0=pending 없음(immediate 안전망 소진)=건강. 사전 분석(인메모리 last_nightly_day+max_requests 재기동→재실행 가능하나 멱등 무해) 예측 일치. **잔여: medlaw 증분 14시 KST(05UTC) 관측.**
- **격리 venv 보존**: `~/ts_poc_venv`+`~/.totalsegmentator`(9.6G, lungs 본PoC 재사용).
