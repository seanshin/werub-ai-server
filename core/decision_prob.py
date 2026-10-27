"""판정 토큰 확률 추출 — 제약 디코딩 출력에서 라벨별 확률을 뽑는다. (외부 API 0)

P6-1 Phase 0. 기획서 `docs/01-plan/features/P6-1-Decision-Confidence-And-Jev.plan.md` §4-1.

🔑 **왜 `top_logprobs`를 읽는가(2026-09-22 GPU 실측)**

제약 디코딩(`format` 스키마) 상태에서 **선택된 토큰 자신의 `logprob`은 신뢰할 수 없다**.
문법이 고른 토큰이 무제약 분포의 상위에 없으면 그 값은 사실상 0이 된다:

    format={"type":"string","enum":["A","B","C"]} 로 판정시켰을 때
      tok='"C'  logprob→p=0.0000   ← 선택 토큰 자신의 값(쓸 수 없다)
      top_logprobs: 'C'=0.9999, 'B'=0.0001   ← **여기가 진짜 분포**

그리고 **라벨이 다중 토큰이면 분포 자체가 없다**. `["동일문서","상보파싱","별개회차"]`로
같은 질문을 시켰더니 선택 토큰 확률이 0.0119→0.0009→0.0000으로 떨어졌고
(`top_logprobs`는 문법이 없었다면 쓰려던 '연'·'위'·'적'을 보여줬다) 판정 자체도 틀렸다.
단일 토큰 라벨로 바꾸자 분포가 살아나고 **답도 교정됐다**.

⇒ **규약: 판정용 스키마의 값은 단일 토큰 라벨만 쓴다**(`true/false`, `A/B/C`, `1/2/3`).
   라벨의 뜻은 시스템 프롬프트에 적는다. 다중 토큰 한국어 enum은 답과 확률을 동시에 망친다.

⚠️ **대소문자·공백 변종이 확률을 쪼갠다.** 실측에서 `false`=0.951 + `False`=0.049로
갈렸다. 정규화 후 합산하지 않으면 confidence가 체계적으로 과소평가된다.

실패는 전부 **None**이다. 0.5로 채우지 않는다 — 미측정과 "판정 0.5"는 다른 사실이고,
결측을 값으로 채우면 축이 깨끗해 보인다(v2.125.9에서 겪은 그 함정).
"""

from __future__ import annotations

__author__ = "Shin Hyunmook <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary"

import logging
import math

logger = logging.getLogger(__name__)

#: 참/거짓 판정용 기본 라벨 집합(`{"supported": {"type": "boolean"}}` 스키마와 짝).
BOOL_LABELS = {"true": ["true"], "false": ["false"]}


def _norm(tok: str) -> str:
    """라벨 비교용 정규화 — 앞뒤 공백·따옴표 제거 후 casefold.

    JSON 제약 디코딩은 값 앞에 `"`를 붙여 내보내므로(`'"C'`) 따옴표도 턴다.
    """
    return tok.strip().strip('"').strip().casefold()


def extract_label_prob(logprobs, labels=None, *, min_total: float = 1e-9):
    """판정 토큰 위치의 `top_logprobs` → 라벨별 확률(잡힌 라벨 안에서 재정규화).

    Args:
        logprobs: Ollama 응답의 `logprobs` 배열(`Result.raw.get("logprobs")`).
                  각 원소는 `{"token", "logprob", "top_logprobs": [...]}`.
        labels: `{반환키: [허용 토큰 변종, ...]}`. 기본은 `BOOL_LABELS`.
                변종은 `_norm` 후 비교하므로 대소문자를 나열할 필요가 없다.
        min_total: 이 값 이하의 확률 합은 신호로 보지 않는다.

    Returns:
        `{라벨: 확률}` (합=1.0) 또는 **None**.

        None이 되는 경우:
          - `logprobs`가 없거나 비어 있다(엔진이 안 실어 보냄 / 미지원)
          - 어느 위치에서도 라벨 토큰이 안 잡힌다
          - 잡힌 확률 합이 `min_total` 이하

    판정 위치는 **라벨이 처음 잡히는 토큰 위치**로 정한다. JSON 객체 출력에서는
    키 이름(`"supported"`)이 먼저 나오지만 키 토큰은 라벨 집합에 없으므로 자연히
    값 위치에서 잡힌다.
    """
    if not logprobs:
        return None
    labels = labels or BOOL_LABELS
    want = {}
    for key, variants in labels.items():
        for v in variants:
            want[_norm(v)] = key

    for pos in logprobs:
        tops = (pos or {}).get("top_logprobs") or []
        hit = {}
        for alt in tops:
            key = want.get(_norm(alt.get("token", "")))
            if key is None:
                continue
            try:
                p = math.exp(alt["logprob"])
            except (KeyError, TypeError, OverflowError, ValueError):
                continue
            # ⚠️ 같은 라벨의 변종(`false`/`False`/` false`)은 **합산**한다.
            hit[key] = hit.get(key, 0.0) + p
        if not hit:
            continue
        total = sum(hit.values())
        if total <= min_total:
            continue
        return {k: v / total for k, v in hit.items()}

    return None


def prob_of(logprobs, label: str = "true", labels=None):
    """`extract_label_prob`에서 한 라벨의 확률만 꺼낸다.

    Returns:
        확률 · 또는 **None**(판정 위치 자체를 못 찾음 = 미측정).

    🔑 **분포는 잡혔는데 그 라벨만 없으면 `0.0`이다(None이 아니다).** `top_logprobs`는
    상위 k개만 주므로, 라벨이 거기 없다는 것은 **그 라벨이 결정적으로 졌다**는 뜻이지
    측정을 못 했다는 뜻이 아니다. 실측 예: `{"supported": false}` 확신 판정에서
    `top_logprobs`에 `true`가 아예 없었다 — 이때 `P(true)=None`을 돌려주면
    **가장 확신한 건들이 축에서 통째로 빠지고** 애매한 건만 남아 분포가 뒤집힌다.

    ⚠️ 대신 k 밖의 잔여 질량만큼(상위 k 중 최소 확률 미만) 과소평가된다 — 알려진
    근사이고, 이 축을 **절대값이 아니라 하위 구간 지목**에만 쓰는 이유 중 하나다.
    """
    dist = extract_label_prob(logprobs, labels)
    if dist is None:
        return None
    return dist.get(label, 0.0)
