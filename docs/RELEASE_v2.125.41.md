# v2.125.41 — 서버 재편(HIS/weruby 스택 동거) 영향 검토 + Wasabi 폐지 → MinIO 전환 + ingress 점검 모드화

2026-09-09 · 회귀 신규 15

## 배경 — 이 서버가 더 이상 AI 전용 박스가 아니다

오늘 오전 운영자가 Linode(A/B)에 있던 **HIS/weruby 스택 전체를 이 GPU 박스로 이전**했다.
컨테이너 **44개**(openpacs·twin·edu·cerno·sign·lis·hospital-erp·infra·werub-minio/postgres/redis·
pulse-poc + 기존 searxng) + PM2 **21 프로세스**(next-server 다수·Caddy·cloudflared) + 강제 리붓 1회.

**유입 경로가 바뀐 것이 가장 큰 변화다.**

```
[이전] 외부 → Linode nginx(example.org) → SSH 역터널 → GPU 127.0.0.1:8585
[현재] 외부 → Cloudflare Tunnel(his-ml) → Caddy :8088 → GPU 127.0.0.1:8585   (/ollama/*)
```

`example.org` DNS는 이미 Cloudflare로 넘어갔고 `ollama-tunnel`은 09:50에 중지·비활성화됐다.

## ① 인증 우회 여부 — 가장 먼저 확인했고, 뚫리지 않았다

새 경로도 요청이 **127.0.0.1에서 도착**한다. `local_bypass`가 그대로 먹었다면 인터넷 전체가
무인증 관리자 표면(`/api/keys`·`/api/config`·`/api/delete`)을 얻는다.

`is_local_request()`가 **fail-closed**(포워딩 헤더가 하나라도 있으면 외부로 간주 — 보안검토 H1)라
Caddy가 넣는 `X-Forwarded-For`에 걸려 차단된다. 라이브 실측:

| 경로 | `/api/version` | `/api/keys` | `/api/config` | `/api/models` |
|---|---|---|---|---|
| Caddy 경유(Host: example.org) | 401 | 401 | 401 | 401 |
| 직접 8585(진짜 로컬) | 200 | 200 | 200 | 200 |
| 인터넷(https://example.org/ollama/…) | — | **401** | — | — |

🔑 **설계가 미리 옳았던 것이지 오늘 고친 게 아니다.** v2.125.x 보안검토가 "CF 헤더 단일 의존"을
제거해 두지 않았다면 이 이전만으로 전면 우회가 열렸다.

## ② Wasabi 유료 해제 → MinIO 전환

`/api/image/s3/status` → `AccountProblem: Cannot find user for the access key` (계정 폐지 확진).

대체는 사내 **werub-minio**(127.0.0.1:9000). boto3 계약은 그대로지만 두 가지가 새로 필요했다.

- **`s3_public_endpoint`** — MinIO는 루프백 바인딩이라 그 주소로 서명한 presigned URL은 서버
  밖에서 안 열린다. SigV4는 **host 헤더를 서명에 포함**하므로 외부가 접속할 호스트로 서명해야
  한다. 업로드·목록·삭제는 내부 엔드포인트, **서명만 공개 엔드포인트**로 클라이언트를 갈랐다.
- **`s3_addressing_style`(기본 path)** — 가상호스트 스타일(`버킷.호스트`)은 DNS가 없어 MinIO에
  못 닿는다. Wasabi에선 auto가 우연히 동작했다.

미설정 시 종전 동작(내부 엔드포인트로 서명) 유지 — 조용히 바뀌지 않는다.

프로비저닝: 버킷 `werub-ai-images` 신설 + **전용 계정 `werub-ai`**(정책=그 버킷 한정 RW.
jitsi 녹화 버킷과 자격증명을 공유하지 않는다). 공개 경로는 Caddy `handle /werub-ai-images/*`
→ 9000 (Host 보존이 서명 검증 조건이라 `header_up Host`를 넣지 않는다).

**외부 실측**(이 맥 → Cloudflare → Caddy → MinIO): presigned **200·70B** · 미서명 **403**.

⚠️ 정직 기록: 09-08 Wasabi 백업은 `werub-storage`·`werub-mail` 두 버킷만 받았고 우리 `images/`
접두는 **포함되지 않았다** — 계정 폐지로 구 객체는 복구 불가. 다만 `jobs.response`에 `s3_url`을
가진 행이 **0건**이라 실사용 피해는 없다(로컬 자산 sana_out 7·ComfyUI output 1은 그대로).

## ③ 🔴 같은 실패를 `/api/image/s3/list`는 200으로 흘리고 있었다

상태 엔드포인트는 정직하게 `accessible:false`를 말하는데 목록 엔드포인트는 **동일한 예외를 빈
리스트로 삼켜** `200 {"count":0}`을 반환했다(결함 클래스 1 — "200인데 빈 결과"). 저장소 교체와
**실패가 보이게 만드는 것은 별개의 처치**라 함께 했다.

- `list_objects_ex()` 신설(items+error) → 사용자 표면은 **502**로 드러낸다.
- 폴백 호출자(`/api/image/view` → 디스크 서빙)는 종전 `list_objects()` 계약 유지 — 여기서 예외를
  올리면 **이미지 조회 자체가 죽는다**(폴백이 존재 이유).

## ④ 은퇴한 터널이 만든 위양성 경보 — 검사를 지우지 않고 모드를 갈랐다

`ollama-tunnel` 은퇴 직후부터 `/api/health`가 **critical 경보 132건(당일)**, watchdog이 5분마다
WARN을 쌓았다. 검사를 삭제하면 **진짜로 유입이 끊겼을 때도 조용해진다**.

`ingress_mode` 신설: `ssh_tunnel`(기본 — iMac autossh 구성 보호) | `proxy` | `none`.

- `proxy`는 엣지 리스너에 **TCP 연결만** 한다(30s 캐시). 🔑 자기 HTTP를 되쏘지 않는 이유:
  `/api/health`는 대시보드가 5초마다 폴링하므로 자기요청이 gthread를 두 배로 먹는다
  (SSE 스레드 고갈 인시던트와 같은 모양).
- `scripts/watchdog.sh`·`health_check.sh`도 **같은 스위치**를 본다 — 코드만 고치면 5분 주기
  WARN이 계속 쌓인다(v2.125.16의 "릴리즈 노트가 코드보다 앞선다" 교훈).

GPU 설정: `ingress_mode=proxy`·`ingress_probe_addr=127.0.0.1:8088`.
결과: 배포(14:19) 이후 tunnel/ingress 경보 **0건**, watchdog WARN **정지**(마지막 14:05).

## ⑤ 강제 리붓(11:01) 사후 점검 — 이상 없음

운영자 강제 재부팅이었고 종료 로그가 끊겨 있다(journald가 `corrupted or uncleanly shut down`
으로 저널 2개 교체 — 자가 복구·무해). 확인한 축:

- 커널 7.0.0-31 · 드라이버 580.173.02 · 상주 14b **size_vram 10.4GB**(CPU 폴백 없음)
- EXT4 오류 **0** · 루트 rw 정상 · `post_reboot_verify` **RESULT gpu=OK**(11:03)
- **SQLite 22개 전부 `quick_check` OK**(queue 255MB·notice 124MB 포함·WAL 잔재 정상)
- **ChromaDB 14 컬렉션 전부 판독 가능** — medlaw 13,452 · notice 4,964 · medical_pub 15,285

## ⑥ 동거 영향 — 지금 문제 없는 축과 지켜볼 축

| 축 | 실측 | 판정 |
|---|---|---|
| GPU VRAM | 컴퓨트 프로세스 = llama-server 1개(10.2GB/16GB) | 새 서비스의 GPU 점유 **0** |
| Ollama 11434 | 바인딩 127.0.0.1·접속자 전부 우리 gunicorn | 외부 컨테이너의 **모델 축출 위험 없음** |
| 컨테이너 RAM | 합계 **2.6GB** / 30GB | 여유 있음 |
| 포트 | 8585·8189(sana)·8888(searxng) 충돌 없음 | OK |
| 디스크 | 265G → **182G**(도커 이미지 28G+볼륨 7.4G·his-backup 24G·백업 28G) | 여유 있으나 **일일 백업 3건이 계속 증가**(03:00·03:30·03:45) — 추적 필요 |
| RAM 총량 | 30GB·가용 19GB·스왑 5.1G→**8G(100%)** | heavy tier는 **실측 통과**(⑦) · ⚠️스왑 고갈은 별건 관측 |
| cron | 우리 5건 + HIS 5건(03:00 백업·03:45 MinIO 미러·04:00 롤링 리프레시) | 스코프 분리 확인 — 우리 서비스 미접촉 |

부수 확인: ComfyUI가 내려가 있는 것은 정상(`GPUResourceManager`가 요청 시 기동하는 온디맨드
구성) · `ollama-worker-bridge`도 09:50에 비활성화됐으나 **iMac 자체가 09-06부터 다운**이라
기능 변화는 없다(불변식: 미도달=GPU 로컬 100%).

## 검증

- 회귀 신규 15 — `tests/test_s3_minio_migration.py` 8(서명 호스트·path 스타일·오류 표면화·
  폴백 계약 유지·라우트 502·소스 동결) · `tests/test_ingress_mode.py` 7(모드별 근거·자기요청
  금지 소스 동결·스크립트 스위치 일치)
- 배포 게이트 통과(`deploy.sh` → `OK d8453e0`)
- 라이브: S3 status accessible·업로드·목록·**외부 presigned 200**·미서명 403·health tunnel
  `{"mode":"proxy","ok":true}`

## 산출물

- 정본: 이 문서 · `CLAUDE.md`(헤더·최근 릴리즈·유입 경로 절·백로그) · `README.md` ·
  `docs/RELEASE_DIGEST.md`(2026-09-09 세션 절) · `docs/AI_ARCHITECTURE_WHITEPAPER.md`(§5 토폴로지·
  §3.8 스토리지·§12 인증 경계) · `docs/01-plan/references/operator-pending-2026-09-03.md` §2-B.
- 백서 아티팩트 재게시(v2.125.41 기준):
  https://claude.ai/code/artifact/1aa5733f-ff39-4904-9a90-e380fc08cc08

## 운영자 대기 (변경분)

- ~~Wasabi 해제 대응~~ ✅ 완결. **단 `hospital-jitsi` 등 다른 서비스도 Wasabi를 보고 있다면**
  같은 전환이 필요하다(우리 표면만 확인했다).
- Caddy `admin off` — 무중단 reload가 막혀 있어 오늘 반영에 **his-proxy 재시작 1회**(~1초 중단)가
  필요했다. 상시 운영이라면 `admin localhost:2019` 허용 검토.

## ⑦ heavy tier 동거 실측 — 통과 (추정을 사실로 바꿈)

"가용 RAM 19GB에 22GB 모델"이라 얇아 보였으나 **1회 실측 결과 압력이 거의 없었다**.
(⚠️ 이제 OOM은 우리만의 문제가 아니라 HIS 컨테이너까지 건드리므로 추정으로 두지 않았다.)

| 지표 | 기준선 | 최악값 | 사후 |
|---|---|---|---|
| MemAvailable | 17,869MB | **17,850MB**(−19MB) | 22GB |
| 스왑 사용 | 7,843MB | 8,191MB(+348MB) | 8.0G |
| VRAM | 11,051MiB | **15,137MiB** | 상주 14b 10.4GB 복원 |
| 컨테이너 | 44(healthy 17) | — | **44(healthy 17) 무변화** |
| OOM kill | — | — | **0**(oomd·커널 둘 다) |

응답 18초(로드 15.6s + 추론 2.5s)·한국어 정상. 🔑 **MoE라 RAM 상주가 아니라 대부분 VRAM에
올라간다**(15.1GB/16GB) — 22GB "전량 RAM 필요"라는 직관이 틀렸다.

⚠️ **별건으로 드러난 것 — 스왑 8GB가 100% 찼다**(오늘 5.1G→8.0G). MemAvailable은 23GB라
시스템이 굶은 상태는 아니지만 **스왑 헤드룸이 0**이라 다음 스파이크는 곧장 캐시 회수/OOM으로
간다. 점유 상위 = 우리 gunicorn **1,080MB**(콜드 페이지·재접근 시 지연 스파이크 요인) ·
next-server 계열 합계 ~1.9GB · firefox 428MB · gnome-shell 313MB(데스크톱 세션).
조치는 안 했다(효과 미확인 처치 금지) — **관측 항목으로 등록**하고, 필요 시 스왑 증설 또는
데스크톱 세션 정리가 후보다.
