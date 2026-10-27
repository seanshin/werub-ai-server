#!/bin/bash
# Ollama 업그레이드 후 검증 매트릭스 (runbook: docs/01-plan/features/ollama-upgrade-0.30.plan.md)
# 사용: scripts/ollama_upgrade_check.sh [기대버전(기본 0.30.11)]
# 전부 비파괴 읽기/스모크. 실패 항목 있으면 exit 1 (롤백 판단 근거).
set -u
EXPECT="${1:-0.30.11}"
OLLAMA_URL="http://127.0.0.1:11434"
ADMIN_URL="http://127.0.0.1:8585"
PASS=0; FAIL=0
ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "=== Ollama 업그레이드 검증 (기대 $EXPECT) ==="

# 1) 버전
V=$(ollama --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
[ "$V" = "$EXPECT" ] && ok "버전 $V" || bad "버전 $V (기대 $EXPECT)"

# 2) env drop-in 보존 (FLASH/KV/PARALLEL/CTX/MAX_LOADED/OVERHEAD)
ENV=$(systemctl show ollama -p Environment --value 2>/dev/null)
for k in OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NUM_PARALLEL=2 \
         OLLAMA_CONTEXT_LENGTH=8192 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_GPU_OVERHEAD=512m; do
    echo "$ENV" | grep -q "$k" && ok "env $k" || bad "env $k 누락"
done

# 3) 엔진 응답
C=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$OLLAMA_URL/")
[ "$C" = "200" ] && ok "엔진 / 200" || bad "엔진 / $C"

# 4) 모델 인식 (36개 기준, 저장소 마이그레이션 무손실)
N=$(curl -s --max-time 10 "$OLLAMA_URL/api/tags" | grep -o '"name"' | wc -l | tr -d ' ')
[ "$N" -ge 30 ] && ok "모델 인식 $N개" || bad "모델 인식 $N개 (<30, 저장소 손실 의심)"

# 5) 상주모델 warm(keep_alive -1) + 생성 스모크 + tok/s
#    ⚠️ 재시작 직후 첫 추론은 콜드로드(커널 워밍)라 tok/s 왜곡 → 워밍업 1회 선행 후 측정
curl -s -o /dev/null --max-time 300 "$OLLAMA_URL/api/generate" \
    -d '{"model":"qwen2.5:14b","prompt":"워밍업","stream":false,"keep_alive":-1,"options":{"num_predict":4}}'
R=$(curl -s --max-time 180 "$OLLAMA_URL/api/generate" \
    -d '{"model":"qwen2.5:14b","prompt":"대한민국의 수도부터 광역시를 모두 나열하세요.","stream":false,"keep_alive":-1,"options":{"temperature":0,"num_predict":64}}')
echo "$R" | grep -q '"response"' && ok "qwen2.5:14b 생성" || bad "qwen2.5:14b 생성 실패: $(echo "$R" | head -c 120)"
ED=$(echo "$R" | grep -oE '"eval_duration":[0-9]+' | grep -oE '[0-9]+')
EC=$(echo "$R" | grep -oE '"eval_count":[0-9]+' | grep -oE '[0-9]+')
if [ -n "$ED" ] && [ -n "$EC" ] && [ "$ED" -gt 0 ]; then
    TPS=$(( EC * 1000000000 / ED ))
    [ "$TPS" -ge 25 ] && ok "생성속도 ${TPS} tok/s (warm, 기준 0.21: ~89)" || bad "생성속도 ${TPS} tok/s (<25, 저하 의심)"
fi

# 6) format 제약 디코딩 (medical_router/eval 의존) — JSON 정상 파싱으로 판정(grep은 이스케이프 따옴표에 취약)
F=$(curl -s --max-time 60 "$OLLAMA_URL/api/generate" \
    -d '{"model":"qwen2.5:14b","prompt":"1+1은?","stream":false,"format":{"type":"object","properties":{"answer":{"type":"integer"}},"required":["answer"]},"options":{"temperature":0,"num_predict":16}}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.loads(d.get('response') or '{}').get('answer',''))" 2>/dev/null)
[ -n "$F" ] && ok "format JSON 스키마 제약 (answer=$F)" || bad "format 제약 실패"

# 7) 상주 확인
curl -s --max-time 10 "$OLLAMA_URL/api/ps" | grep -q 'qwen2.5:14b' && ok "qwen2.5:14b 상주" || bad "상주 안 됨"

# 8) 임베딩 (RAG bge-m3, num_ctx 옵션 경로)
E=$(curl -s --max-time 60 "$OLLAMA_URL/api/embed" \
    -d '{"model":"bge-m3","input":"테스트","options":{"num_ctx":2048}}' | grep -o '"embeddings"' )
[ -n "$E" ] && ok "bge-m3 임베딩" || bad "임베딩 실패"

# 9) 앱 통합 (health + status)
H=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$ADMIN_URL/api/health")
[ "$H" = "200" ] && ok "app health 200" || bad "app health $H"
S=$(curl -s --max-time 15 "$ADMIN_URL/api/status" | grep -o '"running": *true' | head -1)
[ -n "$S" ] && ok "app /api/status running" || bad "app status 이상"

# ---------------------------------------------------------------------------
# 10~14) v2.114.0 이후 추가된 표면 — 이 스크립트 작성 시점엔 없던 기능들이라
#        업그레이드가 조용히 깨뜨릴 수 있는 지점. (비전·멀티모달·도구호출·digest)
# ---------------------------------------------------------------------------

# 테스트용 이미지(문자 포함 PNG)를 base64로 생성 — 비전 경로 판독 확인용
IMG_B64=$(python3 - <<'PYIMG'
import base64, io
from PIL import Image, ImageDraw
img = Image.new("RGB", (560, 160), "white")
ImageDraw.Draw(img).text((30, 60), "OLLAMA CHECK 4917", fill="black")
buf = io.BytesIO(); img.save(buf, format="PNG")
print(base64.b64encode(buf.getvalue()).decode())
PYIMG
)

# 10) 비전 — 앱 /api/generate + images (v2.124.50에서 봉합한 '조용히 무시' 경로)
#     ⚠️ Ollama에 **직접** 쏘면 안 된다: qwen3-vl은 thinking 모델이라 토큰이 `thinking`으로
#     가고 `response`는 빈 채 done_reason=length가 된다(2026-07-29 실측. num_predict를 키워도
#     동일·think:false도 0.30.11에선 무효). 앱 경로는 시스템 프롬프트를 주입하고 num_predict를
#     묶지 않아 정상 응답한다 — **우리 계약은 앱 경로**이므로 그쪽을 검증한다.
VG=$(curl -s --max-time 240 "$ADMIN_URL/api/generate" -H "Content-Type: application/json" \
    -d "{\"model\":\"qwen3-vl:8b\",\"prompt\":\"이미지의 글자를 그대로 읽어줘\",\"images\":[\"$IMG_B64\"]}" \
    | python3 -c "
import sys, json
out = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        out.append(json.loads(line).get('response') or '')
    except ValueError:
        pass
print(''.join(out).strip())" 2>/dev/null)
echo "$VG" | grep -q "4917" && ok "비전 /api/generate+images 판독" || bad "비전 /api/generate+images 실패: $(echo "$VG" | head -c 80)"

# 11) 비전 — 앱 /v1 멀티모달(OpenAI image_url → images 변환)
VV=$(curl -s --max-time 240 "$ADMIN_URL/v1/chat/completions" -H "Content-Type: application/json" \
    -d "{\"model\":\"qwen3-vl:8b\",\"stream\":false,\"messages\":[{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"이미지의 글자를 그대로 읽어줘\"},{\"type\":\"image_url\",\"image_url\":{\"url\":\"data:image/png;base64,$IMG_B64\"}}]}]}" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);print((d.get('choices') or [{}])[0].get('message',{}).get('content',''))" 2>/dev/null)
echo "$VV" | grep -q "4917" && ok "비전 /v1 멀티모달 판독" || bad "비전 /v1 멀티모달 실패: $(echo "$VV" | head -c 80)"

# 12) 도구 호출 — /v1 tool_calls (OpenPencil 연동 계약. 0.32.3에 tool call 수정 이력)
TC=$(curl -s --max-time 120 "$ADMIN_URL/v1/chat/completions" -H "Content-Type: application/json" \
    -d '{"model":"qwen3:8b","stream":false,"messages":[{"role":"user","content":"서울 날씨 알려줘"}],"tools":[{"type":"function","function":{"name":"get_weather","description":"날씨 조회","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]}' \
    | python3 -c "
import sys,json
d=json.load(sys.stdin); m=(d.get('choices') or [{}])[0].get('message',{})
tc=m.get('tool_calls') or []
print('OK' if tc and isinstance((tc[0].get('function') or {}).get('arguments'), str) else 'NO')" 2>/dev/null)
[ "$TC" = "OK" ] && ok "/v1 tool_calls (arguments=JSON 문자열)" || bad "/v1 tool_calls 이상($TC)"

# 13) 모델 digest 안정성 — 규제 트랙(트윈)이 digest를 고정 참조하므로 변하면 통지 대상
DG=$(curl -s --max-time 15 "$OLLAMA_URL/api/tags" | python3 -c "
import sys,json
for m in json.load(sys.stdin).get('models',[]):
    if m['name'].startswith('qwen2.5:14b'): print(m.get('digest','')[:12])" 2>/dev/null)
if [ -n "$DG" ]; then
    [ "$DG" = "${EXPECT_DIGEST:-$DG}" ] && ok "qwen2.5:14b digest $DG" || bad "digest 변경: $DG (기대 $EXPECT_DIGEST) → 규제 트랙 통지 필요"
else
    bad "digest 조회 실패"
fi

# 14) 앱 의료 라우팅(fail-closed 가드가 정상 응답을 막지 않는지)
MR=$(curl -s -o /dev/null -w "%{http_code}" --max-time 30 "$ADMIN_URL/api/medical/models/routing")
[ "$MR" = "200" ] && ok "의료 라우팅 200(로컬 bypass)" || bad "의료 라우팅 $MR"

echo
echo "=== 결과: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] && echo "→ GO (soak 24h: worker_churn/latency_p95 알림 + 야간 eval 배치 관측)" \
                  || echo "→ NO-GO: 롤백 절차 실행 (runbook §롤백)"
exit $FAIL
