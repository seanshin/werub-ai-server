# v2.125.23 — ⑤ mlx_stt 파일명 경로 순회 봉쇄 (v2.125.18 이월분)

날짜: 2026-08-26 | 선행: v2.125.18 §0 ⑤ 부분정정·별건 이월

## 1. 결함

iMac 워커 `/worker/stt`가 클라이언트 filename(multipart `audio` 필드명 또는 JSON
`filename`)을 검증 없이 `core/mlx_stt.transcribe`로 넘기고, 거기서
`os.path.join(tmp, filename)`에 audio_bytes를 썼다. filename이 `../../x`·`/etc/y`면
**tmp 밖 임의 경로 쓰기**가 가능했다.

- 범위(v2.125.18 정정): `/worker/stt`는 `@worker_auth`(공유 토큰) 게이트라 **임의 API
  키가 아니라 워커 토큰 보유자 한정** — 위험은 협소하나 secure_filename 미적용은 결함.
- 형제 4곳은 이미 basename/ext 패턴을 쓰고 있었다(레포가 옳게 푼 패턴의 미적용 지점).

## 2. 처치

`core/mlx_stt.transcribe`가 filename에서 **basename 후 확장자만** 취해 결정적 이름
(`input{ext}`)으로 쓴다. 확장자는 ffmpeg 포맷 힌트로만 쓰이므로 그 정보만 보존하면
충분하고, `../`·절대경로·백슬래시가 있어도 tmp를 벗어날 수 없다. 확장자 형식이
`\.[A-Za-z0-9]{1,8}`가 아니면(없음·비-ASCII 등) `.bin` 폴백.

- 🔑 **봉쇄를 싱크(mlx_stt)에 둔다** — 발신부(worker_app·서버 위임)가 아니라 파일을
  실제로 쓰는 지점이라, 어느 호출 경로로 들어와도 막힌다.

## 3. 배포 격차 (명시)

iMac M1 워커는 **별도 머신**이고 이 세션에서 **SSH 셸 경로가 없다**(웹서버 역터널은
8586/8587 서비스 포트만 노출·2222는 gitea). 따라서:
- **레포 코드 처치는 완료·GPU에 배포**(GPU도 같은 `core/mlx_stt.py`를 갖지만 STT 위임
  발신측이고 워커 실행측은 iMac). 라이브 워커 반영은 **iMac 워커 launchd 재시작이 선행**
  (운영자 — feedback_deploy_workflow: iMac은 코드/설치만·배포는 GPU 전용).
- 워커 버전이 v2.84.0으로 오래됐다는 기록(project_imac_ollama)과 함께, **다음 iMac 워커
  갱신 시 이 커밋이 함께 반영**된다. 그 전까지 라이브 iMac 워커는 구 동작.

## 4. 검증

- 회귀 4건(`tests/test_mlx_stt_filename_safety.py`): 순회·절대경로·백슬래시가 tmp 안에
  갇힘(`commonpath`)·확장자 보존(ffmpeg)·이상 확장자 `.bin` 폴백·소스 스캔(raw join 재유입
  차단). mlx-whisper 미설치 환경에서도 경로 로직 단위 검증(헬퍼 재현+소스 스캔).
- 인접 회귀·게이트는 배포 시(STT 스모크는 GPU faster-whisper 경로라 이 변경과 무관).

## 5. 남긴 것

- **라이브 iMac 워커 반영**(운영자 — 워커 재시작) · git blob 이력 재작성(운영자) ·
  client.call hints 미전송(별건) · P5-1 잔여.
- v2.125.18 이월 별건은 이로써 ⑤ 코드 처치까지 완료 — 남는 것은 iMac 배포와 git blob뿐.
