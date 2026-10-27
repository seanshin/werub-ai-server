# WeRU.B AI — 웹 클라이언트 (이미지 마킹/판별)

메인웹(Next.js 등)에서 AI 생성물 **표시(마킹)** 와 **판별** 을 호출하는 TypeScript SDK.
근거: AI 기본법 §31 / 정보통신망법.

## 설치

`werub-ai.ts` 한 파일을 프로젝트에 복사(의존성 0, fetch 기반). 환경변수 설정:

```bash
# .env (서버 사이드 전용 — 브라우저 노출 금지)
OLLAMA_BASE_URL=https://example.org/ollama
OLLAMA_API_KEY=CHANGE_ME        # localhost에서 발급 (아래)
```

API 키 발급(GPU localhost):
```bash
curl -X POST http://localhost:8585/api/keys -H "Content-Type: application/json" -d '{"name":"main-web"}'
```

## API 요약

| 메서드 | 엔드포인트 | 설명 |
|---|---|---|
| `detectImage(blob, {ml?})` | `POST /api/detect/image` | 파일 판별 |
| `detectUrl(url, {ml?})` | `POST /api/detect/image` | URL 판별 |
| `generateImage(prompt, opts)` | `POST /api/image/generate` | 생성(결과 자동 마킹+C2PA) |
| `imageStatus(jobId)` | `GET /api/image/status/{id}` | 생성 상태/결과 |

## 1) 판별 — Next.js 서버 라우트(프록시, 권장)

키를 브라우저에 노출하지 않도록 서버 라우트를 둔다.

```typescript
// app/api/detect/route.ts
import { WerubAI } from "@/lib/werub-ai";

export async function POST(req: Request) {
  const form = await req.formData();
  const image = form.get("image");
  if (!(image instanceof Blob)) return Response.json({ error: "image required" }, { status: 400 });

  const ai = new WerubAI();                          // env에서 키 로드
  try {
    const result = await ai.detectImage(image, { ml: true });
    return Response.json(result);
  } catch (e: any) {
    return Response.json({ error: e.message }, { status: e.status ?? 500 });
  }
}
```

```typescript
// 브라우저(클라이언트 컴포넌트)
const fd = new FormData();
fd.append("image", file);                            // <input type="file">
const res = await fetch("/api/detect", { method: "POST", body: fd });
const result = await res.json();
```

## 2) 판별 결과 → UI 배지

```typescript
import { aiBadge } from "@/lib/werub-ai";

const badge = aiBadge(result);
// badge === null                            → 표시 안 함
// { label:"AI 생성물",  level:"confirmed" } → confidence high (우리 마킹/C2PA 확정)
// { label:"AI 생성 추정", level:"likely"  } → medium/low (타툴 메타·ML 추정)
```

**confidence 의미**
| 값 | 근거 | 권장 UI |
|---|---|---|
| `high` | 우리 마킹 또는 C2PA 서명 | "AI 생성물" 확정 배지 |
| `medium` | 타툴 메타데이터/소프트웨어 | "AI 생성 추정" |
| `low` | ML 화소 추정만 | "AI 생성 추정"(주의) |
| `none` | 신호 없음 | 표시 안 함 |

> ⚠️ `disclaimer` 를 함께 노출하세요. **자동 차단/삭제 금지**(오탐 가능). 게시단계 체크박스 기본값 제안 정도로 사용.

## 3) 생성 — 결과는 자동 마킹됨

```typescript
const ai = new WerubAI();
const job = await ai.generateImage("a red cube", { model: "flux-schnell", width: 512, height: 512, steps: 4 });

// 폴링
let s;
do { await new Promise(r => setTimeout(r, 3000)); s = await ai.imageStatus(job.job_id); }
while (s.status !== "completed" && s.status !== "failed");

const url = s.images?.[0]?.s3_url;   // 이 이미지는 워터마크+PNG메타+C2PA가 이미 부착됨
```

## 직접 호출(curl) 레퍼런스

```bash
# 파일 판별
curl -X POST https://example.org/ollama/api/detect/image \
  -H "Authorization: Bearer $OLLAMA_API_KEY" -F 'image=@photo.png'

# URL 판별
curl -X POST https://example.org/ollama/api/detect/image \
  -H "Authorization: Bearer $OLLAMA_API_KEY" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/p.jpg","ml":true}'
```

상세 응답 스키마: `docs/IMAGE_API_GUIDE.md` 참고.
