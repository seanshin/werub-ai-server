# PACS 연동 가이드 — 의료 영상 AI 분석 API

> Version: v2.40.0 | 2026-05-20  
> Model: MedGemma 4B (Google DeepMind)  
> Base URL: `https://example.org/ollama`  
> 인증: `Authorization: Bearer <API_KEY>`

---

## 1. 개요

PACS에서 DICOM 영상을 JPG/PNG로 Export한 후, REST API로 AI 분석을 요청합니다.

```
PACS ──(DICOM Export → JPG/PNG)──→ POST /api/vision/medical/analyze
                                    ←── JSON (소견/판독/권고) ──┘
                                    
HIS ──── 결과를 EMR에 기록 ────────┘
```

### 지원 영상 종류

| 모달리티 | modality 값 | 지원 부위 |
|----------|:----------:|----------|
| X-ray | `xray` | chest, abdomen, extremity, spine |
| CT | `ct` | brain, chest, abdomen, pelvis |
| MRI | `mri` | brain, spine, knee, shoulder |
| 초음파 | `ultrasound` | abdomen, thyroid, breast |
| 병리 | `pathology` | biopsy, cytology |
| 안저 | `fundus` | retina |
| 피부경 | `dermoscopy` | skin lesion |

### 지원 이미지 형식

| 형식 | MIME | 최대 크기 |
|------|------|:---------:|
| JPEG | image/jpeg | 20MB |
| PNG | image/png | 20MB |
| WebP | image/webp | 20MB |

---

## 2. 인증

### API 키 발급 (관리자)

```bash
curl -X POST https://example.org/ollama/api/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "PACS-Server"}'
```

```json
{"key": "sk-abc123...", "name": "PACS-Server"}
```

### 모든 요청에 Bearer 토큰 첨부

```
Authorization: Bearer sk-abc123...
```

---

## 3. API 레퍼런스

### 3-1. 영상 분석 (단건)

**`POST /api/vision/medical/analyze`**

| 파라미터 | 타입 | 필수 | 설명 |
|----------|------|:----:|------|
| image | file | ✅ | 의료 영상 파일 (JPG/PNG) |
| modality | string | | 영상 종류 (xray, ct, mri, ultrasound, pathology, fundus, dermoscopy) |
| body_part | string | | 촬영 부위 (chest, abdomen, brain, spine, extremity 등) |
| clinical_info | string | | 임상 정보 (증상, 병력, 검사 소견 등) |
| language | string | | 응답 언어 (ko/en, 기본: ko) |

**요청 예시**

```bash
curl -X POST https://example.org/ollama/api/vision/medical/analyze \
  -H "Authorization: Bearer sk-abc123..." \
  -F "image=@chest_pa.jpg" \
  -F "modality=xray" \
  -F "body_part=chest" \
  -F "clinical_info=기침 2주, 발열 38.5도, WBC 12000"
```

**성공 응답 (200)**

```json
{
  "findings": "우하폐야에 경화 소견이 관찰됩니다. 좌폐야는 맑습니다. 심장 크기는 정상 범위입니다. 횡격막은 정상 위치에 있습니다.",
  "impression": "우하엽 폐렴 의심. 감별: 무기폐, 흉수.",
  "recommendations": "임상 증상 지속 시 흉부 CT 추가 검사 권고. 항생제 치료 후 2주 내 추적 촬영 권고.",
  "raw_response": "(전체 원문 응답)",
  "model": "medgemma:4b",
  "modality": "xray",
  "body_part": "chest",
  "tokens": 180,
  "duration_ms": 3200,
  "disclaimer": "AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요."
}
```

### 3-2. 비교 판독 (이전 영상 대비)

**`POST /api/vision/medical/report`**

| 파라미터 | 타입 | 필수 | 설명 |
|----------|------|:----:|------|
| current_image | file | ✅ | 현재 영상 |
| previous_image | file | | 이전 영상 (비교 판독 시) |
| modality | string | | 영상 종류 |
| body_part | string | | 촬영 부위 |
| clinical_info | string | | 임상 정보 |
| language | string | | 응답 언어 (ko/en) |

**요청 예시**

```bash
curl -X POST https://example.org/ollama/api/vision/medical/report \
  -H "Authorization: Bearer sk-abc123..." \
  -F "current_image=@chest_20260520.jpg" \
  -F "previous_image=@chest_20260506.jpg" \
  -F "modality=xray" \
  -F "body_part=chest" \
  -F "clinical_info=폐렴 치료 2주 후 경과 관찰"
```

**성공 응답 (200)**

```json
{
  "report": "1. 현재 영상 소견: ...\n2. 이전 영상 대비 변화: ...\n3. 종합 판독: ...\n4. 권고: ...",
  "comparison": true,
  "model": "medgemma:4b",
  "modality": "xray",
  "body_part": "chest",
  "tokens": 350,
  "duration_ms": 5800,
  "disclaimer": "AI 참고용 분석입니다. 정식 판독은 영상의학과 전문의에게 의뢰하세요."
}
```

### 3-3. 모델 정보 조회

**`GET /api/vision/medical/models`**

```bash
curl https://example.org/ollama/api/vision/medical/models \
  -H "Authorization: Bearer sk-abc123..."
```

```json
{
  "models": [
    {
      "id": "medgemma:4b",
      "name": "MedGemma 4B",
      "size": "4B",
      "developer": "Google DeepMind",
      "type": "medical-vision",
      "specialties": ["radiology", "dermatology", "pathology", "ophthalmology"],
      "modalities": ["xray", "ct", "mri", "ultrasound", "pathology", "fundus", "dermoscopy"]
    }
  ]
}
```

### 3-4. 사전 검증 (Precheck)

**`POST /api/vision/medical/precheck`**

영상을 AI 판독에 보내기 전, 의료 영상 적합성을 사전 검증합니다.
비의료 이미지, 저품질 영상을 사전에 걸러 GPU 낭비를 방지합니다.

| 파라미터 | 타입 | 필수 | 설명 |
|----------|------|:----:|------|
| image | file | ✅ | 검증할 이미지 파일 |
| expected_modality | string | | 예상 모달리티 (불일치 검출용) |

**요청 예시**

```bash
curl -X POST https://example.org/ollama/api/vision/medical/precheck \
  -H "Authorization: Bearer sk-abc123..." \
  -F "image=@chest_pa.jpg" \
  -F "expected_modality=xray"
```

**적합 응답 (200, suitable=true)**

```json
{
  "suitable": true,
  "confidence": 0.94,
  "classification": {
    "is_medical": true,
    "modality": "xray",
    "modality_confidence": 0.97,
    "body_part": "chest",
    "body_part_confidence": 0.92
  },
  "quality": {
    "score": 85,
    "resolution": "2048x2048",
    "resolution_ok": true,
    "exposure_ok": true,
    "sharpness_ok": true,
    "issues": []
  },
  "warnings": [],
  "duration_ms": 800
}
```

**부적합 응답 (200, suitable=false)**

```json
{
  "suitable": false,
  "rejection_reason": "NOT_MEDICAL_IMAGE",
  "classification": {"is_medical": false, "modality": null},
  "quality": {"score": 25, "issues": ["LOW_RESOLUTION", "BLURRY"]},
  "warnings": ["이미지가 의료 영상이 아닙니다."]
}
```

### 3-5. analyze + precheck 통합

`/api/vision/medical/analyze` 호출 시 `precheck` 파라미터를 추가하면 자동 검증됩니다.

| 모드 | 동작 |
|------|------|
| `off` (기본) | 기존 동작, 검증 없이 바로 판독 |
| `warn` | 검증 후 경고를 판독 결과에 포함 |
| `strict` | 부적합 시 판독 거절 (HTTP 422) |

**strict 모드 예시**

```bash
curl -X POST https://example.org/ollama/api/vision/medical/analyze \
  -H "Authorization: Bearer sk-abc123..." \
  -F "image=@screenshot.png" \
  -F "modality=xray" \
  -F "precheck=strict"
```

**거절 응답 (422)**

```json
{
  "error": "image failed precheck — not suitable for medical analysis",
  "code": "PRECHECK_FAILED",
  "precheck": {
    "suitable": false,
    "rejection_reason": "NOT_MEDICAL_IMAGE",
    "quality": {"score": 25, "issues": ["LOW_RESOLUTION", "BLURRY"]}
  }
}
```

### 검증 항목

| 항목 | 기준 | 이슈 코드 |
|------|------|----------|
| 의료 영상 여부 | MedGemma 분류 | `NOT_MEDICAL_IMAGE` |
| 해상도 | 최소 256×256 | `LOW_RESOLUTION` |
| 파일 크기 | 최소 10KB | `OVER_COMPRESSED` |
| 선명도 | Laplacian variance ≥ 100 | `BLURRY` |
| 노출 | 평균 밝기 30-225 | `UNDER_EXPOSED` / `OVER_EXPOSED` |
| 모달리티 불일치 | expected vs detected | 경고 표시 |

### 권장 워크플로우

```
PACS Export → Precheck → (적합) → Analyze → 판독 결과 → EMR 기록
                       → (부적합) → 오류 반환, GPU 절약
```

---

## 4. 에러 코드

| HTTP | code | 원인 | 대응 |
|:----:|------|------|------|
| 400 | `MISSING_IMAGE` | 이미지 파일 미첨부 | `image` 필드로 파일 전송 |
| 400 | `EMPTY_IMAGE` | 빈 파일 | 유효한 이미지 확인 |
| 400 | `EMPTY_FILENAME` | 파일명 없음 | 파일명 포함하여 전송 |
| 401 | `unauthorized` | API 키 없음/만료 | Bearer 토큰 확인 |
| 404 | `MODEL_NOT_FOUND` | AI 모델 미설치 | 관리자에게 모델 설치 요청 |
| 413 | `IMAGE_TOO_LARGE` | 20MB 초과 | 이미지 리사이즈 후 재전송 |
| 415 | `UNSUPPORTED_FORMAT` | 지원하지 않는 형식 | JPG/PNG/WebP만 지원 |
| 422 | `PRECHECK_FAILED` | 사전 검증 실패 (strict 모드) | precheck 결과 확인, 적합한 영상 사용 |
| 502 | `LLM_ERROR` | AI 엔진 내부 오류 | 재시도, 관리자 문의 |
| 502 | `EMPTY_RESPONSE` | 모델이 빈 응답 반환 | 다른 이미지로 재시도 |
| 503 | `LLM_UNAVAILABLE` | AI 엔진 미실행 | 관리자에게 서버 상태 확인 |
| 504 | `TIMEOUT` | 추론 시간 초과 | 이미지 크기 줄이거나 재시도 |

**에러 응답 형식**

```json
{
  "error": "사람이 읽을 수 있는 에러 메시지",
  "code": "MACHINE_READABLE_CODE",
  "detail": "상세 기술 정보 (디버깅용)"
}
```

---

## 5. PACS 연동 코드 예시

### Python

```python
import requests

API_URL = "https://example.org/ollama/api/vision/medical/analyze"
API_KEY = "CHANGE_ME"

def analyze_medical_image(image_path, modality="xray", body_part="chest", clinical_info=""):
    """의료 영상 AI 분석 요청"""
    with open(image_path, "rb") as f:
        response = requests.post(API_URL,
            headers={"Authorization": f"Bearer {API_KEY}"},
            files={"image": f},
            data={
                "modality": modality,
                "body_part": body_part,
                "clinical_info": clinical_info,
            },
            timeout=120,
        )

    if response.status_code != 200:
        error = response.json()
        raise Exception(f"[{error.get('code')}] {error.get('error')}")

    result = response.json()
    return {
        "findings": result["findings"],
        "impression": result["impression"],
        "recommendations": result["recommendations"],
        "disclaimer": result["disclaimer"],
    }

# 사용
result = analyze_medical_image(
    "/path/to/chest_xray.jpg",
    modality="xray",
    body_part="chest",
    clinical_info="기침 2주, 발열 38.5도"
)
print(f"소견: {result['findings']}")
print(f"판독: {result['impression']}")
print(f"권고: {result['recommendations']}")
```

### C# (.NET)

```csharp
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text.Json;

public class MedicalImageAnalyzer
{
    private readonly HttpClient _client;
    private const string API_URL = "https://example.org/ollama/api/vision/medical/analyze";

    public MedicalImageAnalyzer(string apiKey)
    {
        _client = new HttpClient { Timeout = TimeSpan.FromSeconds(120) };
        _client.DefaultRequestHeaders.Authorization =
            new AuthenticationHeaderValue("Bearer", apiKey);
    }

    public async Task<AnalysisResult> AnalyzeAsync(
        string imagePath, string modality = "xray",
        string bodyPart = "chest", string clinicalInfo = "")
    {
        using var content = new MultipartFormDataContent();
        var imageContent = new StreamContent(File.OpenRead(imagePath));
        content.Add(imageContent, "image", Path.GetFileName(imagePath));
        content.Add(new StringContent(modality), "modality");
        content.Add(new StringContent(bodyPart), "body_part");
        content.Add(new StringContent(clinicalInfo), "clinical_info");

        var response = await _client.PostAsync(API_URL, content);
        var json = await response.Content.ReadAsStringAsync();

        if (!response.IsSuccessStatusCode)
        {
            var error = JsonSerializer.Deserialize<ErrorResponse>(json);
            throw new Exception($"[{error.Code}] {error.Error}");
        }

        return JsonSerializer.Deserialize<AnalysisResult>(json);
    }
}

public record AnalysisResult(
    string Findings, string Impression, string Recommendations,
    string Model, string Disclaimer, int Tokens, int DurationMs);

public record ErrorResponse(string Error, string Code, string Detail);
```

### Java

```java
import java.io.*;
import java.net.http.*;
import java.net.URI;

public class MedicalImageClient {
    private static final String API_URL =
        "https://example.org/ollama/api/vision/medical/analyze";
    private final String apiKey;
    private final HttpClient client;

    public MedicalImageClient(String apiKey) {
        this.apiKey = apiKey;
        this.client = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10))
            .build();
    }

    public String analyze(String imagePath, String modality,
                          String bodyPart, String clinicalInfo) throws Exception {
        String boundary = "----FormBoundary" + System.currentTimeMillis();
        byte[] imageBytes = Files.readAllBytes(Path.of(imagePath));

        String body = "--" + boundary + "\r\n"
            + "Content-Disposition: form-data; name=\"image\"; filename=\"image.jpg\"\r\n"
            + "Content-Type: image/jpeg\r\n\r\n";
        String fields = "\r\n--" + boundary + "\r\n"
            + "Content-Disposition: form-data; name=\"modality\"\r\n\r\n" + modality
            + "\r\n--" + boundary + "\r\n"
            + "Content-Disposition: form-data; name=\"body_part\"\r\n\r\n" + bodyPart
            + "\r\n--" + boundary + "\r\n"
            + "Content-Disposition: form-data; name=\"clinical_info\"\r\n\r\n" + clinicalInfo
            + "\r\n--" + boundary + "--\r\n";

        byte[] payload = concat(body.getBytes(), imageBytes, fields.getBytes());

        HttpRequest request = HttpRequest.newBuilder()
            .uri(URI.create(API_URL))
            .header("Authorization", "Bearer " + apiKey)
            .header("Content-Type", "multipart/form-data; boundary=" + boundary)
            .POST(HttpRequest.BodyPublishers.ofByteArray(payload))
            .timeout(Duration.ofSeconds(120))
            .build();

        HttpResponse<String> response = client.send(request,
            HttpResponse.BodyHandlers.ofString());
        return response.body();
    }
}
```

---

## 6. 운영 참고

### 성능

| 항목 | 값 |
|------|-----|
| 모델 | MedGemma 4B (Google DeepMind) |
| GPU | NVIDIA RTX 5080 16GB |
| 평균 응답 시간 | 3-5초 (단건 분석) |
| 비교 판독 | 5-8초 (2장) |
| 최대 타임아웃 | 120초 (단건), 180초 (비교) |
| 동시 요청 | 24 스레드 (Rate limit: 120/분) |

### 제한사항

1. **DICOM 직접 지원 안 됨** — JPG/PNG로 Export 후 전송 필요
2. **AI 참고용** — 정식 판독은 영상의학과 전문의 필수
3. **VRAM 공유** — LLM(qwen2.5:14b)과 GPU 공유, 동시 사용 시 지연 가능
4. **이미지 크기** — 20MB 이하, 해상도 제한 없음 (내부 리사이즈)

### DICOM Export 권장 설정

| 항목 | 권장값 |
|------|--------|
| 형식 | JPEG (품질 95%) 또는 PNG |
| 해상도 | 원본 유지 또는 2048×2048 이하 |
| 색상 | Grayscale (X-ray/CT/MRI), RGB (병리/피부) |
| 윈도우 레벨 | 검사 목적에 맞게 적용 후 Export |
| 환자 정보 | DICOM 태그에서 제거 (비식별화) |

---

## 7. 지원

| 항목 | 연락처 |
|------|--------|
| API 관리 | WeRU.B AI Server Admin |
| Swagger UI | https://example.org/ollama/docs |
| 서버 상태 | https://example.org/ollama/api/health |
