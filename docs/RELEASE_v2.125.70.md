# v2.125.70 — 없는 이미지는 404: `/api/image/view` 503·`/api/image/s3/url` 죽은 URL 200 봉합

기준 v2.125.69 · 발견 경위: 2026-10-07 서버 점검(읽기 전용)이 7일 5xx 중 `image/view` 503을 판독 · 지시서 `memory/project_image_view_fix_brief.md`

## 결함

1. **`GET /api/image/view/<file>` → 503(로그 0줄)** — 2026-09-28 16:56·18:43·10-03 10:30, `ollama-admin_00024/00025_.png`, UA `node`.
   그 파일은 **어디에도 없었다**: MinIO 버킷 객체 0개(09-09 Wasabi 폐지 때 `images/` 백업 누락 — v2.125.41 기록), ComfyUI output 비어 있음.
   핸들러는 S3 미스 뒤 ComfyUI로 프록시하는데 ComfyUI는 **on-demand**(이미지 생성 때만 기동 — 09-09 이후 inactive)라 연결 거부 →
   `except` → **503**. 맞는 답은 404.
2. **`GET /api/image/s3/url/<key>` → 200 + 열면 NoSuchKey** — 존재 확인 없이 presign. 같은 node 클라이언트가 Wasabi 시절
   `images/20260630_013417_ollama-admin_00292_.png`를 09-12~09-30 **9회** 받아 갔다(반복 클래스 #1).
   ⇒ v2.125.41의 "실사용 피해 0(jobs에 s3_url 0건)"은 **불완전**했다 — 구 참조를 쥔 클라이언트가 있다.
3. 잠재(실례 없음):
   - ComfyUI가 떠 있으면 **상태 코드를 안 봐서** 404 본문을 200으로 낼 수 있었다.
   - S3 탐색이 `list_objects(max_keys=200)` + **`filename in key` 부분 일치** — ComfyUI 파일 카운터가 09-09 재설치로 `00001`부터
     다시 시작해 **같은 파일명이 다른 날짜 키로 재등장하면 목록상 먼저 나온 엉뚱한 이미지로 리다이렉트** · 200개 초과분 미탐색.

## 처치

1. `core/s3_storage.find_image_key(filename)` — 업로드 규칙(`{prefix}{YYYYmmdd_HHMMSS}_{filename}`) 기준 **정확 일치**,
   여럿이면 **LastModified 최신**, `ContinuationToken` 페이지네이션(1000×10쪽 = 보관 상한 5000의 2배). 반환 `(key, error)`.
2. `core/s3_storage.object_exists(key)` — `head_object`: True / False(404·NoSuchKey·NotFound) / None(저장소 오류).
3. `/api/image/view`:
   - S3는 `find_image_key` · 조회 실패는 **로그 한 줄** 후 폴백(종전 `except: pass`).
   - ComfyUI **연결 실패**(꺼짐) → ComfyUI output **디스크를 직접 확인**해 있으면 서빙(마킹 적용), 없으면 **404** + 로그.
     ⚠️ 이미지 조회 때문에 ComfyUI를 기동하지 않는다(16GB VRAM — 상주 14b 축출).
   - ComfyUI가 응답하면 **상태 코드 존중**: 404→404, 그 외 비200→502.
4. `/api/image/s3/url`: presign 전에 `object_exists` — 없으면 **404**, 저장소 오류 **502**. 성공 계약(`{key,url,ttl}`) 불변.
5. 404 로그에 **호출자 식별**(`key=`관리 콘솔 발급 키 이름·`ua=`) — 09-28~10-03 요청은 `api_activity` 창이 짧아 키 이름이 이미 사라져
   어느 클라이언트인지 특정하지 못했다. 다음 호출 때 특정 → 해당 팀에 구 참조 정리 안내(운영자 경유).

## 같은 클래스 쓸기(#7)

- ComfyUI `/view`를 부르는 다른 2곳(`_auto_s3_upload`·`/api/image/s3/upload/<file>`)은 **이미 상태 코드를 확인**한다 — 결함은 view 한 곳.
- 디스크 서빙 형제(`/api/image/delete`·`/api/training/files/*` 3곳)는 로컬 파일만 보고 없으면 404 — 해당 없음.

## 지시서와 달라진 점

- 지시서는 "ComfyUI 꺼짐 = 없음 → 404"였으나, 이미지가 ComfyUI output **디스크에는 남아 있을 수 있어**(ComfyUI가 꺼진 동안 생성된 게 아니라 전에 생성된 것)
  디스크를 먼저 확인하고 서빙하도록 했다. 종전엔 이 경우도 503이었다.

## 검증

- 신규 `tests/test_image_view_not_found.py` **17건**: 어디에도 없음+ComfyUI 꺼짐=404·로그(키·UA) · 디스크에 있으면 서빙 · ComfyUI 404/500/200 → 404/502/200 ·
  S3 정확 키 리다이렉트(ComfyUI 미호출) · `find_image_key` 최신·2쪽·부분 일치 거부·오류 표면화 · `object_exists` 코드별 · `s3/url` 404/502/200.
- **구 코드 측정**(GPU 격리 worktree): **15 실패 / 2 통과** — 통과 2는 불변식(ComfyUI 200→200, 객체 있음→200).
- 전체 **2447 passed**(13 skipped).
- ⚠️ 작성 중 내 테스트 결함 1건: 이 파일이 `app` 임포트보다 **먼저** 임시 Flask에 `image_bp`를 등록하면, 뒤이어 `import app`이
  블루프린트에 `before_request`(탭 권한 가드)를 붙이다 Flask가 거부해 **다른 파일 3건이 실패**했다(이 파일 → path_traversal 순서에서만 결정적으로 재현 ·
  알파벳 전체 실행에선 앞선 테스트가 app을 먼저 임포트해 가려짐). 모듈 스코프 픽스처로 `app`을 먼저 임포트.

## 연동자 영향

- 없는 이미지 요청이 503 → **404**, 없는 키의 presigned URL 요청이 200 → **404**. 구 이미지 참조를 쥔 클라이언트(UA `node`, 미식별)는 오류 코드만 바뀐다.
