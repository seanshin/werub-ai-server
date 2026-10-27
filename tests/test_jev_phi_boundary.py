"""P6-1 Phase 1 — 🔴 Jev PHI 경계 회귀. 소스 스캔(실행·네트워크 0).

**왜 테스트가 경계인가**: Jev는 외부 상용 API다. 의료·상담·STT·스크라이브 경로가
이 모듈을 부르면 **환자 데이터가 우리 통제 밖으로 나간다**. 공급사 문서에는 처리 리전도
HIPAA/GDPR 언급도 없다. 문서로 "쓰지 않기로 한다"고 적어 두면 다음 사람이 모른다 —
v2.125.34에서 라이선스 금지 조항 모델을 일반 표면에만 배선한 것과 같은 구조로,
**배선 자체가 준수 장치**가 되게 한다.

판정 대상이 필요한 층(triage 증상 파싱·is_crisis 등)은 **원문이 있어야 판정되므로**
비식별 피처만 보내는 설계가 성립하지 않는다. 그 층은 로컬 구조화 출력으로 간다.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 이 파일들에서 Jev를 부르면 PHI가 외부로 나갈 수 있다.
_BANNED = [
    "routes/medical.py", "routes/medical_advanced.py", "routes/medical_law.py",
    "routes/triage.py", "routes/counseling.py", "routes/vision.py", "routes/fhir.py",
    "routes/meeting.py", "routes/system.py",
    "core/meeting_stt.py", "core/meeting_pipeline.py", "core/meeting_diarize.py",
    "core/stt_realtime.py", "core/mlx_stt.py", "core/reasoning_chain.py",
]

_JEV_REF = re.compile(r"(?:from\s+core\s+import\s+[^\n]*\bjev\b"
                      r"|from\s+core\.jev\s+import"
                      r"|import\s+core\.jev"
                      r"|typesafe)", re.I)


def _read(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else None


@pytest.mark.parametrize("rel", _BANNED)
def test_의료계열은_jev를_부르지_않는다(rel):
    src = _read(rel)
    if src is None:
        pytest.skip("%s 없음" % rel)
    hit = _JEV_REF.search(src)
    assert not hit, ("🔴 %s 가 Jev를 참조한다(%r). PHI가 외부 상용 API로 나갈 수 있다 — "
                     "이 층은 로컬 구조화 출력으로 간다." % (rel, hit.group(0)))


def test_전_저장소에서_jev_호출자는_허용목록뿐이다():
    """동적 import·우회 배선을 잡는다. 허용 목록이 늘면 **여기서 먼저 걸린다**."""
    import core.jev as J
    allowed_files = {"core/jev.py", "core/faithfulness.py",
                     "scripts/eval_faithfulness.py", "scripts/eval_jev_gate.py"}
    found = set()
    for p in list((ROOT / "routes").rglob("*.py")) + list((ROOT / "core").rglob("*.py")) \
            + list((ROOT / "scripts").rglob("*.py")) + [ROOT / "app.py"]:
        if not p.exists():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if _JEV_REF.search(p.read_text(encoding="utf-8")):
            found.add(rel)
    assert found <= allowed_files, (
        "허용되지 않은 Jev 호출자: %s — 평가 층 외에는 쓰지 않는다(PHI 경계)"
        % sorted(found - allowed_files))
    # 목록 자체도 코드와 맞는지 확인(문서 드리프트 방지)
    assert set(J._ALLOWED_CALLERS) == {"core.faithfulness", "scripts.eval_faithfulness",
                                       "scripts.eval_jev_gate"}


def test_전송_페이로드에_환자_식별_키가_없다(monkeypatch):
    """어댑터가 보내는 것은 **state(근거 텍스트) + questions(지시문)** 뿐이어야 한다.

    구조를 화이트리스트로 고정한다 — 나중에 누가 메타데이터를 얹으면 여기서 걸린다.
    """
    import core.jev as J

    sent = {}

    class _S:
        def post(self, url, json=None, timeout=None, headers=None):
            sent.update(json or {})
            raise RuntimeError("stop")     # 응답은 볼 필요 없다

    monkeypatch.setattr(J, "_cfg", lambda k: dict(J._DEFAULTS, jev_enabled=True)[k])
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.setattr(J, "_caller_allowed", lambda: True)
    J._usage.update(day=None, tokens=0)
    J.ask_noul("근거 본문", {"c1": "지지되는가"}, session=_S())

    assert set(sent) == {"state", "model", "questions"}
    for banned in ("patient", "patient_id", "mrn", "tenant", "tenant_id", "user",
                   "name", "rrn", "phone", "chart", "meta", "metadata"):
        assert banned not in sent
