"""외부 호출자 답변의 충실도 채점 입력 — (질문·답변·근거) 쌍 파싱·검증.

야간 배치는 원래 **질문만 받아 스스로 답을 생성**한 뒤 채점한다(`scripts/eval_faithfulness.py`).
Cerno처럼 자기 파이프라인으로 답을 만드는 호출자는 그 경로를 쓸 수 없어, 생성 단계를 건너뛰고
**주어진 쌍을 채점**하는 입력이 필요하다(2026-07-30 Cerno 회신 A-2 — 실시간 검증은 질의당
LLM 2회가 붙어 p95 목표·동시 슬롯과 충돌하므로 **비동기 사후 샘플링**으로 합의).

입력은 NDJSON 1줄 = 1건:

    {"id": "...", "question": "...", "answer": "...",
     "evidence": ["근거1", "근거2"], "meta": {...}}

⚠️ `evidence`는 **답변 생성에 실제 쓴 근거**여야 한다. 채점 시점에 재검색해 채우면 채점 대상이
달라져 위양성이 난다(우리 하네스에서 가장 흔했던 오류 원인).
⚠️ PHI는 **호출자가 제거한 상태**로 보낸다 — 서버는 이 데이터를 `eval.db`에 적재한다.
"""
import json

# 한 번에 태울 수 있는 상한. 야간 배치(03:30)는 judge 호출로 문항당 수 초를 쓰므로,
# 무제한으로 받으면 배치 창을 밀어낸다(자체 10문항 배치가 이미 수 분).
MAX_RECORDS = 100

_REQUIRED = ("question", "answer")


def _as_evidence(value):
    """근거를 채점용 단일 텍스트로. 리스트는 줄바꿈 결합(자체 러너와 동일 형식)."""
    if isinstance(value, (list, tuple)):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return "\n".join(parts)
    return str(value or "").strip()


def parse_pairs(lines, max_records=MAX_RECORDS):
    """NDJSON 줄들 → (유효 레코드 리스트, 오류 메시지 리스트).

    **한 줄이 깨져도 전체를 버리지 않는다** — 하루치 표본에서 한 건의 오타로 그날 측정이
    통째로 사라지면 안 된다. 대신 오류를 모아 돌려주고 호출자가 볼 수 있게 한다.
    """
    records, errors = [], []
    for i, raw in enumerate(lines, 1):
        line = (raw or "").strip()
        if not line or line.startswith("#"):
            continue
        if len(records) >= max_records:
            errors.append("%d행 이후 생략 — 한 번에 %d건까지" % (i, max_records))
            break
        try:
            obj = json.loads(line)
        except (ValueError, TypeError) as e:
            errors.append("%d행 JSON 파싱 실패: %s" % (i, str(e)[:60]))
            continue
        if not isinstance(obj, dict):
            errors.append("%d행: 객체가 아님" % i)
            continue
        missing = [k for k in _REQUIRED if not str(obj.get(k) or "").strip()]
        if missing:
            errors.append("%d행: %s 누락" % (i, "·".join(missing)))
            continue
        evidence = _as_evidence(obj.get("evidence"))
        if not evidence:
            # 근거 없이는 충실도를 정의할 수 없다(무엇에 대한 지지인지가 없다).
            errors.append("%d행: evidence 비어 있음 — 답변 생성에 쓴 근거가 필요" % i)
            continue
        records.append({
            "id": str(obj.get("id") or "").strip() or ("line-%d" % i),
            "question": str(obj["question"]).strip(),
            "answer": str(obj["answer"]).strip(),
            "evidence": evidence,
            "meta": obj.get("meta") if isinstance(obj.get("meta"), dict) else None,
        })
    return records, errors


def load_pairs(path, max_records=MAX_RECORDS):
    with open(path, encoding="utf-8") as f:
        return parse_pairs(f, max_records=max_records)
