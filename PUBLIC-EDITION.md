# AI Server — 공개판 안내

**AI Server — about this public edition**

> **EN** — This repository is the public edition of AI Server v2.125.73. Development continues in a private repository; this edition is its final state with internal working records (reports and replies exchanged with other systems, key-rotation runbooks, server measurements) and operational details removed, licensed under MIT. Operational domains, server addresses and accounts were replaced with example values, and **every password, secret, token and key was emptied to `CHANGE_ME`** — you must set your own before running anything. The table below lists each emptied setting and where it appears.

이 저장소는 **AI Server v2.125.73 의 공개판**입니다. 개발은 비공개 저장소에서 계속하고, 공개판은 그 최종 상태에서 다음을 빼거나 바꾼 것입니다(MIT).

| 무엇 | 어떻게 |
|---|---|
| 내부 작업 기록 | 다른 시스템과 주고받은 결함 보고 · 회신, 키 교체 런북, 서버 실측 등 168개 문서를 뺐습니다. 소스 주석이 빠진 문서를 가리키는 곳이 남아 있을 수 있습니다 |
| 운영 정보 | 운영 도메인 · 서버 주소 · 서버 계정을 예시 값(`example.org` · 문서용 IP 대역 · `deploy`)으로 바꿨습니다 |
| 🔴 비밀값 | 비밀번호 · 비밀 · 토큰 · 키 **16곳**을 `CHANGE_ME` 로 비웠습니다. **실행 전에 아래 값을 직접 정하십시오** |

## 직접 설정할 값

| 설정 이름 | 있는 곳 |
|---|---|
| `api_key` | `docs/MEDICAL_AI_INTEGRATION.md` · `docs/MEDICAL_API_GUIDE.md` · `docs/RELEASE_v2.124.21.md` |
| `API_KEY` | `docs/PACS_INTEGRATION_GUIDE.md` |
| `gpu_api_key` | `docs/PLATFORM_SPEC.md` |
| `GPU_API_KEY` | `docs/WEB_INTEGRATION_GUIDE.md` |
| `HR_LAW_TENANT_KEY` | `docs/WEB_INTEGRATION_GUIDE.md` |
| `MEDICAL_AI_KEY` | `docs/MEDICAL_AI_INTEGRATION.md` |
| `OLLAMA_API_KEY` | `client/web/README.md` · `scripts/batch_poll.sh` · `scripts/batch_run.sh` · `scripts/batch_submit.sh` |
| `tenant.api_key` | `docs/WEB_INTEGRATION_GUIDE.md` |
| `token` | `docs/DASHBOARD_API_GUIDE.md` |

- 같은 이름이 여러 곳에 있으면 **모두 같은 값**이어야 하는지(서로 맞물리는 연동 비밀) 확인하십시오.
- 값은 길고 무작위로 만드십시오(예: `openssl rand -base64 32`). 저장소에 다시 커밋하지 마십시오.

## 켜기 전에 기관이 정할 보안 설정

아래 기능은 공개판에서 **설정하지 않으면 안전한 쪽으로 멈추거나 꺼져 있습니다.** 기관의 운영 절차에 맞춰 직접 정하십시오.

| 설정 | 무엇을 정하나 |
|---|---|
| `local_bypass` | 로컬(127.0.0.1) 요청을 인증 없이 관리자로 받는 설정. 공개판은 **기본 꺼짐**입니다 — 앞단 프록시가 전달 헤더(X-Forwarded-For 등)를 붙이지 않으면 바깥 요청도 로컬로 보이기 때문입니다. 저장소 문서의 「localhost 는 인증 불필요」 안내는 이 값을 켠 경우의 설명입니다. 켤 때는 프록시가 전달 헤더를 반드시 붙이는지 먼저 확인하십시오. |
| `auth_enabled` | API 인증. 끄지 마십시오. 관리 화면에서 인증을 약하게 바꾸는 요청은 바깥 세션에서 거부됩니다. |
| `API 키` | 연동 시스템(HIS · PACS · ERP · edu 등)마다 키를 따로 발급하고, 키 목록 · 발급은 관리자 계정으로만 합니다. |
| `백업` | `scripts/backup.sh` 는 작업 대기열 DB · 설정 · 로그를 **암호화하지 않고** 묶습니다. 대기열에는 요청 본문이 남을 수 있으므로 백업 파일을 암호화된 저장소에 두십시오. |
| `코드 마스터 · 법령 · 학습 데이터` | 공개판에는 들어 있지 않습니다(재배포 조건 · 운영 데이터). `scripts/ingest_*` 로 기관이 직접 받아 만듭니다. |
| `sudo 허용 범위` | 운영 스크립트는 서비스 재시작(`systemctl restart ollama` 등)만 sudo 로 허용하는 구성을 전제로 합니다. **설정 파일 · systemd drop-in 디렉터리는 root 소유로 두고** 앱 계정이 바꿀 수 없게 하십시오(둘이 겹치면 앱 계정이 root 권한을 얻는 경로가 생깁니다). |

- 코드 안의 개발용 기본값은 운영에서 반드시 환경 변수로 덮어쓰십시오.
- 이 생태계 전체의 소개 · 구축 순서: [werub-his-ecosystem](https://github.com/seanshin/werub-his-ecosystem)
