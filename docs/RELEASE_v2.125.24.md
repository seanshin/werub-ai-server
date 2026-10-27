# v2.125.24 — 오케스트레이터 client.call hints 전송 구조 복구 (X7 구조 원인)

날짜: 2026-08-26 | 선행: X7(v2.125.20)이 발견한 별건 — 위임 클라이언트가 hints를 인자로 받고도 전송하지 않던 구조

## 1. 결함

`core/orchestrator/client.call(node_id, cap, payload, hints)`이 `hints`를 시그니처로 받고도
**요청에 전혀 싣지 않았다**(URL·payload·헤더 어디에도 없음). 그 결과 워커는 form/body
기본값으로 낙착했다 — STT 위임에서 `language`가 무성으로 `ko`로 떨어진 X7의 **구조 원인**.
(X7은 증상을 `_stt_local_only` 로컬 강제로 회피했고, 이 릴리즈는 원인을 고친다.)

## 2. 처치

`_encode`가 `hints`를 받아:
- **multipart(image/audio/file)**: 스칼라 hint를 `data`(form) 필드로 전송 — 워커가
  `request.form.get("language")` 등으로 읽는 기존 계약과 일치. None·비-스칼라(list/dict)는
  제외(form은 문자열만·복합 힌트는 상위에서 로컬 강제하는 계약).
- **json**: body에 병합하되 **payload 키가 우선**(`setdefault`).
`call`은 `requests.post(..., data=data, ...)`로 전달. 수신부(worker_app) 변경 불요 —
`/worker/stt`는 이미 form/body의 `language`를 읽고 있었다(발신만 빠져 있었다).

## 3. 🔑 구조 복구와 정책 완화를 분리 (효과 미확인 처치 금지)

`_stt_local_only`의 **비-ko 로컬 강제는 유지**한다. hints 전송은 복구됐지만, mlx-whisper의
비-ko 전사 품질이 GPU faster-whisper와 동등한지 **미검증**(X10 STT WER은 ko만 잰다)이라,
위임 허용은 X10 비-ko 확장 측정 후로 보류. 즉 이 릴리즈는 **전송 경로만** 올바르게 만들고,
"비-ko도 위임한다"는 정책 변경은 얹지 않는다(주석에 명시). prompt/hotwords 로컬 강제도 유지
(mlx 미수용). 자동 감지(None)는 form에서 제외되어 여전히 로컬 강제 대상.

- **detect/mark**: 현재 hints를 쓰지 않지만, 구조가 올바르므로 향후 힌트가 필요해지면
  수신부만 읽으면 된다(발신은 이제 전송).

## 4. 검증

- 회귀 6건(`tests/test_client_hints_transport.py`): multipart→form·json→body 병합(payload 우선)·
  None/비-스칼라 제외·힌트 없으면 빈 form(회귀 없음)·`call`이 `requests`에 `data`를 실제로 전달.
- 인접 74 passed(orchestrator dispatch/nodes/registry/scheduler/worker/meeting_language).

## 5. 남긴 것

- 비-ko STT 위임 허용은 **X10 STT WER 비-ko 확장** 후 `_stt_local_only` 완화로(별건).
- detect/mark 수신부의 hints 활용은 필요 시(현재 미사용).
