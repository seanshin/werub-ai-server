# Release v2.124.46 — API 키 평문 저장 폐지 + 배치 STT A/B 하네스

날짜: 2026-07-29 | 종류: security + tooling | 배포: GPU graceful HUP(무재시작·NRestarts 0)

## 1. API 키 평문 저장 폐지 (security)

`queue.db` `api_keys.key_value`에 **활성 키 16개가 평문**으로 있었다. 조사 결과 잔재가
아니라 Admin 콘솔 **'Show'(키 재확인) 버튼의 저장소**였다. 방어는 파일 권한 600·admin
가드·ufw뿐이라 **DB 파일이나 백업이 유출되면 16개 키가 한 번에 재사용 가능**했다
(제2 로컬 계정 sakpung 존재). 문서화된 계약("Save this key - it won't be shown again")과도
어긋나고, HR Law 테넌트 키는 이미 sha256 저장(v2.124.21)이라 규율을 통일했다.

**운영자 결정**으로 편의(Show)보다 유출 내성을 택했다.

- `generate_api_key`: INSERT에서 `key_value` 제거 — **해시·prefix만 저장**.
- `GET /api/keys/<id>/reveal` → **410 Gone**(404 아님: 구 클라이언트가 '없는 키'로 오인하지
  않도록) + 재발급 안내. Admin 콘솔 Show 버튼·reveal 행·`copyRevealedKey` 제거.
- 마이그레이션 목록에서 제외(신규 DB엔 컬럼 자체가 없음). 기존 컬럼은 **UPDATE로 비움(16행)**.
- 회귀 가드 `test_no_plaintext_api_key_storage` — 코드가 다시 `key_value`를 읽거나 쓰면 CI 실패.

**검증**: 기존 키로 외부 경유 인증 **200**(해시 기반이라 동작 불변) · reveal **410** ·
신규 발급 행의 `key_value` **None** · 평문 잔여 **0행**.

⚠️ 되돌릴 수 없는 작업이라 실행 전 백업을 만들어 두었다:
`/home/deploy/api_keys_plaintext_backup_20260729.json` (0600). **필요한 키를 확보한 뒤
삭제할 것** — 남겨두면 이번 조치의 의미가 사라진다.

## 2. 배치 STT 설정 A/B 하네스 (tooling)

edu 동시 처리 상향 요청 검증용 `scripts/eval_stt_ab.py`. 실측상 large-v3 float16은
3.84GB/인스턴스라 현 여유(5.61GB)에서 2개가 안 올라가므로(OOM 실증), 증설 전에 **같은
VRAM으로 처리량을 올리는 두 레버**를 실제 강의 파일로 비교한다.

- 변형 4종(base/batched/int8/int8batch)을 **변형마다 별도 프로세스**로 실행(같은 프로세스
  재적재는 이전 인스턴스 VRAM이 남아 두 번째가 OOM).
- `--check`: 적재 전 VRAM 사전 점검(변형별 필요치 대비 여유·부족 시 반납 안내).
- 출력: 처리 시간·배속·세그먼트 수·**base 대비 전사 일치율**·CER(정답 전사 제공 시).
- 판독 규율 명시: 시간 단축이 전사 일치를 깨지 않는지가 핵심(일치 0.98 미만이면 원문 대조).
- ⚠️ 우리 쪽에 실제 한국어 강의 음성이 없어 **하네스만 선작성**. edu 파일(185초·기준선
  STT 8.5초)이 오면 즉시 수치 산출 가능. GPU에서 `--check`·인자 검증 스모크 통과.
