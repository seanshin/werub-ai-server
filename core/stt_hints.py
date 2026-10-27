"""STT 용어 힌트 캡 — whisper 프롬프트 창을 넘지 않게 자른다.

🔴 2026-07-30 edu 실측으로 드러난 결함(v2.124.42 도입분):
whisper 디코더는 `max_length=448`이고 그중 **절반(224토큰)만 프롬프트**(initial_prompt +
hotwords + 직전 문맥)에 쓸 수 있다. 그런데 캡이 **600자**로 걸려 있었다 — 한국어는 대략
1.2토큰/자라 600자면 **약 720토큰**으로 창을 3배 넘긴다.

edu 실측(181초 음원·같은 서버 설정, 힌트 개수만 변경):

| 힌트 | 결과 |
|------|------|
| 없음 | 단어 311 · 세그먼트 41 (정상) |
| 24개 | 세그먼트 28 (이미 변형) |
| 30개(168자) | **단어 94(−70%)** · 세그먼트 13 — `completed`인데 내용이 사라짐 |
| 38개(207자) | `The maximum decoding length must be > 0` 실패 |

**조용한 품질 붕괴가 더 위험하다** — 상태가 `completed`라 호출자가 탐지할 수 없고, 그대로
자막이 되면 학습자가 잘린 내용을 본다. 그래서 캡은 문자수가 아니라 **토큰 추정치**로 걸고,
**잘린 개수를 호출자에게 돌려준다**(무성 절단 금지).
"""
import re

# whisper 디코더 프롬프트 창(= max_length 448의 절반).
WHISPER_PROMPT_WINDOW_TOKENS = 224

# 힌트에 허용할 상한. 창 전체를 쓰면 디코딩할 자리가 남지 않으므로(위 표의 30개 사례)
# 넉넉히 남긴다. 실측상 24개(~140자)에서 이미 세그먼트가 변형되므로 그보다 아래로 잡는다.
DEFAULT_MAX_HINT_TOKENS = 96


def estimate_tokens(text: str) -> int:
    """토크나이저 없이 보수적 토큰 추정(과소추정 회피 방향).

    한글은 자당 ~1.2토큰, 그 외(라틴·숫자·기호)는 자당 ~0.32토큰으로 잡는다.
    `routes/ollama._estimate_tokens`와 같은 계열의 휴리스틱이다.
    """
    if not text:
        return 0
    hangul = len(re.findall(r"[가-힣]", text))
    other = len(text) - hangul
    return int(hangul * 1.2 + other * 0.32) + 1


def cap_hints(hint: str, max_tokens: int = DEFAULT_MAX_HINT_TOKENS):
    """쉼표 구분 힌트 목록 → (캡 적용 문자열|None, 사용 개수, 잘린 개수).

    **항목 경계에서만 자른다** — 항목 중간을 자르면 존재하지 않는 토막("클로르헥"))을
    바이어스해 오히려 오인식을 만든다. 첫 항목 하나도 못 넣을 만큼 길면 그대로 하나만 쓴다
    (빈 힌트로 만들면 호출자 의도가 통째로 사라진다).
    """
    if not hint or not str(hint).strip():
        return None, 0, 0
    items = [p.strip() for p in str(hint).split(",") if p.strip()]
    if not items:
        return None, 0, 0

    kept = []
    for item in items:
        candidate = ", ".join(kept + [item])
        if kept and estimate_tokens(candidate) > max_tokens:
            break
        kept.append(item)
    return ", ".join(kept), len(kept), len(items) - len(kept)


def hint_report(sent: int, used: int, dropped: int) -> dict:
    """호출자에게 돌려줄 힌트 처리 요약(무성 절단 금지)."""
    report = {"sent": sent, "used": used, "dropped": dropped,
              "max_tokens": DEFAULT_MAX_HINT_TOKENS}
    if dropped:
        report["reason"] = ("whisper 프롬프트 창(224토큰)을 넘으면 전사가 잘리거나 실패합니다 — "
                            "앞에서부터 %d개만 사용했습니다. 중요한 용어를 앞에 두십시오." % used)
    return report
