# Release v2.89.0 — 환자 아바타 R1 (werub-physio-v1) + R2 PoC + L4

**날짜**: 2026-06-11
**범위**: 트윈 디지털 트윈팀 협업 — 환자 아바타 생리(R1) 신규 구축·라이브, 영상→메시(R2) PoC. 부수 L4 픽스.
**세션 보고서**: `docs/SESSION_REPORT_20260611.md` · 협업 상세 `docs/01-plan/references/avatar-reply{,2,4}-to-twin.md`

---

## R1 — werub-physio-v1: 0D 다계통 생리 시뮬 + 계통간 커플링 (신규)

GPU 없는 트윈팀이 RTX5080 AI 서버에 전신 생리 연산을 요청 → 트윈 0D(Windkessel/FHN/renal_pk)를 **horizon 시계열**로 격상하고 **계통 간 커플링 2개**를 얹어 차별화.

### 엔드포인트
- `POST /api/medical/physiology` — 동기. 비식별·미저장.
- `POST /api/medical/physiology/stream` — NDJSON(`meta`→`frame`(틱별)→`done`). 아바타 실시간 매핑.

### 핵심
- **`core/physiology.py`** — 순수 numpy·**CPU(GPU/운영 LLM 무경합)**·**결정적**(난수 없음)·비식별 강제(name/mrn 입력 400)·미저장.
- **커플링 2개**(트윈 0D엔 없는 차별점):
  1. `egfr_pk_cardio` — 신배설 심혈관약(11종) → eGFR 보정 축적비(renal_pk 식) → MAP/HR. **단조 구배**(eGFR↓→약물축적↑→MAP↓: eGFR 20→평균MAP 92.3 / 55→98.7 / 120→104.0).
  2. `glucose` — 경구혈당강하제(6종) → onset 반영 1차 동역학.
- **series 8키**: hr, map, sbp, dbp, rr, spo2, glucose_mgdl, cardiac_output_lmin (+ `drug_response[]`, `coupling[]`, `inputs_resolved`, `disclaimer`).
- **체중·키 HIS 미노출 → 성/연령 표준 추정 폴백**(남70/여58kg, 고령 −3). `weight_estimated` 응답 명시.
- **약물명 정규화** `_normalize_drug` — 한글 일반명 17 + 국내 브랜드 11 → INN, 용량/제형/괄호 토큰 제거. 트윈 한국 HIS 실데이터(P2026-0001) 플래그 봉합(한글 약물명 미매칭→커플링 빈배열 방지). `matched_from` 원문 보존, 미매칭=원문 유지(정직).
- **트윈 A단계 아바타 라이브**: organs/series 매핑, AI 미가용 0D 폴백. R1 3문항(series 8키·체중 기본값·채널) 전부 합의.
- 회귀 `tests/test_physiology.py` 10.

---

## R2 — 영상→3D 메시 PoC (AI측 전구간 실증, 트윈 공개 CT 대기)

- **VRAM 정책**(`docs/01-plan/references/r2-totalsegmentator-vram-poc.md`): TotalSegmentator(격리 venv·운영 무영향), LLM 14b 상주 기준 — **fast(3mm) 14.4GB 안전(여유 1.9GB) / full(1.5mm) 15.76GB OOM임박(여유 0.5GB)** → 정책=fast 기본, full은 온디맨드 LLM 언로드.
- **메시 생성 E2E**: 세그 마스크 → `skimage.marching_cubes` → `trimesh` → glTF `.glb`(177KB·V3832/F7260·**CPU<0.1s**·DICOM태그 미임베드=비식별).
- **인터페이스**: 트윈 서명URL(NIfTI.gz)+비동기 202+폴링(콜백 미채택)·폐엽별 멀티 지오메트리 .glb. **남은 것=트윈 공개 비식별 흉부 CT 1건 스테이징.**

---

## L4 — medical-law top_k 입력 안전화 (code-analyzer 백로그)

`int(data.get("top_k"))`가 비정수("abc"/5.5) 입력서 ValueError→500이던 것을 `_safe_top_k`(클램프 [1,10], 비정수/음수→default)로 봉합. `/ask`·`/ask/stream`·`/review` 3경로. 라이브: top_k=abc/-5 둘다 200. 회귀 +1.

---

## 검증
- GPU 배포: push→pull→import 드라이런→재시작→health 200·NRestarts 0. 회귀 test_physiology 10·test_medlaw 25.
- 라이브 E2E: 샘플 A(커플링 2개)/B(비활성), /stream meta+frame+done, 비식별 거부 400, 한글 약물명 커플링 발화, openapi 노출.

## 비고
- 격리 venv `~/ts_poc_venv`+가중치 `~/.totalsegmentator`(9.6G)는 lungs 본 PoC 재사용 위해 보존(운영 무영향).
- 모델 검토 2건(Qwen3.6-35B·MiniMax-M2) 도입 보류 — `docs/SESSION_REPORT_20260611.md` 참조.
