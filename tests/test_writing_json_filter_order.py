"""글쓰기 JSON 경로 — **필터를 JSON에 먼저 태우지 않는다** (2026-08-18 실결함).

🔴 증상: `/api/writing/seo`·`/api/writing/evaluate`가 **8/8 결정적으로** `raw` 폴백을 냈다.
   호출자가 받는 것은 `meta_description: ""`·`keywords: []`인 **빈 결과**인데 **HTTP 200**이다.
   간헐이 아니라 늘 그랬다 — 이 두 엔드포인트는 사실상 동작한 적이 없다.

근본 원인: 순서가 뒤집혀 있었다.

    response = force_korean(response)      # ← JSON 문자열을 줄 단위로 거른다
    result   = force_json(response, "raw")  # ← 구조가 이미 깨져 파싱 실패

`force_korean`은 **한글 없는 줄을 버린다.** JSON에서 그 줄이란
`"keywords": [` · `"score": 70,` · `}` · `]` — 즉 **구조 전부**다.
LLM 출력 자체는 멀쩡한 한국어 JSON이었고, 부순 것은 우리 후처리다.

📌 **v2.124.50과 같은 클래스**다 — 그때는 한국어 필터가 비전 경로의 **영문 OCR을 통째로
   삭제**해 "빈 응답"으로 보였다. *필터는 자기가 무엇을 지우는지 모른다.*

🔑 그 용도의 함수가 **이미 있었다**(`force_korean_json` = 파싱 먼저, 문자열 값에만 필터).
   `medical_advanced.py`는 10곳에서 줄곧 그렇게 쓰고 있었다. 잘못 쓴 곳은 이 둘뿐이었다
   (레포 전수 확인). ⇒ **고칠 것은 필터가 아니라 순서다.**
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from core.korean import force_korean, force_korean_json
from core.quality import force_json

# 실제로 관측된 모양의 응답(seo 프롬프트가 요구하는 스키마).
_LLM_JSON = json.dumps(
    {
        "meta_description": "신규 고객 유입으로 매출이 12퍼센트 증가했다",
        "keywords": ["매출", "신규고객", "재택근무"],
        "readability": {"score": 70, "avg_sentence_length": 24, "paragraph_count": 2,
                        "has_subheadings": False, "issues": ["제목 부재"]},
        "suggestions": ["소제목을 추가할 것", "문장을 짧게 나눌 것"],
    },
    ensure_ascii=False,
    indent=2,
)


# ── 전제 확인(탐지기 검증) ──────────────────────────────────────────────────
def test_filter_destroys_json_structure():
    """이 전제가 이 파일 전체의 근거다 — 먼저 못 박는다.

    필터가 언젠가 구조를 보존하게 바뀌면 이 테스트가 **먼저** 빨개져서
    아래 순서 규칙의 근거가 사라졌음을 알린다(규칙만 남아 도는 것을 막는다).
    """
    filtered = force_korean(_LLM_JSON)
    assert '"keywords"' not in filtered, "한글 없는 줄이 살아남았다면 전제가 달라진 것"
    result = force_json(filtered, "raw")
    assert "raw" in result, "필터를 먼저 태우면 파싱은 깨져야 한다(그게 이 결함이다)"


def test_correct_order_preserves_content():
    """파싱 먼저 → 문자열 값에만 필터. 내용이 살아 있어야 한다."""
    result = force_korean_json(force_json(_LLM_JSON, "raw"))
    assert "raw" not in result
    assert result["meta_description"].strip()
    assert result["keywords"] == ["매출", "신규고객", "재택근무"]
    # 🔑 숫자·불리언은 **문자열이 아니라서** 필터가 손대지 않는다(그게 이 함수의 요점).
    assert result["readability"]["score"] == 70
    assert result["readability"]["has_subheadings"] is False
    assert len(result["suggestions"]) == 2


def test_json_fallback_still_reports_failure():
    """LLM이 진짜로 JSON을 못 냈을 때는 여전히 `raw`로 정직하게 알려야 한다.

    ⚠️ 순서를 고쳤다고 실패를 감추면 안 된다 — 그건 다른 결함이다.
    """
    result = force_korean_json(force_json("죄송합니다. 잘 모르겠습니다.", "raw"))
    assert "raw" in result


# ── 소스 불변식 ─────────────────────────────────────────────────────────────
def _writing_src() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "routes", "writing.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_no_force_korean_before_force_json_in_writing():
    """`force_korean(...)` 직후 `force_json`이 오는 지점이 **하나도 없어야** 한다.

    두 곳을 고쳤으니 세 번째가 생기는 것을 막는다 — 이 결함은 200으로 나가서
    **로그에 아무 흔적이 없다**. 사람이 눈치채는 경로가 아니다.
    """
    lines = _writing_src().splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if re.search(r"(?<!_json)\bforce_korean\s*\(", line) and "force_korean_json" not in line:
            window = "\n".join(lines[i:i + 6])
            if "force_json(" in window:
                offenders.append(f"{i + 1}: {line.strip()}")
    assert not offenders, "JSON 파싱 앞에 force_korean이 있다:\n" + "\n".join(offenders)


def test_json_endpoints_use_force_korean_json():
    """seo·evaluate 두 경로가 실제로 `force_korean_json`을 쓰는지 확인한다.

    v2.125.72: 파싱이 `force_json` → `parse_json_strict`(실패면 502)로 바뀌어 형태가 `force_korean_json(parsed)`가 됐다.
    의도(파싱 먼저, 필터는 값에만)는 같다 — 위 순서 검사가 그대로 지킨다."""
    src = _writing_src()
    assert src.count("force_korean_json(parsed)") == 2
    assert src.count("parse_json_strict(response)") >= 2
