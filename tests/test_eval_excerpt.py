"""근거 발췌 절단 회귀 — 무성 절단 금지.

🐞 2026-08-05 실측: `evidence_excerpt` 상한이 4,000자였는데 실제 근거는 5,000~7,700자.
드릴다운에서 이 발췌를 근거 전체로 알고 대조해 **"근거에 없는 벌칙 수치 5/10"이라는
허상**을 얻었고, 실사용 근거로는 발동 0회인 처치를 만들었다가 철회했다.

잘렸다는 사실이 보였다면 애초에 안 걸렸을 함정이다. 그래서 **자를 때는 말한다**.
(v2.124.11이 프롬프트 청크에 절단 마커를 넣은 것과 같은 규율 — 그때는 모델이,
지금은 사람이 잘린 줄 모르고 완성해 버린다.)
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from eval_faithfulness import _EXCERPT_MAX, _excerpt  # noqa: E402


def test_short_evidence_kept_whole():
    ev = "제15조(진료기록부 등의 보존) 10년" * 10
    assert _excerpt(ev) == ev


def test_boundary_not_truncated():
    ev = "가" * _EXCERPT_MAX
    assert _excerpt(ev) == ev
    assert "절단" not in _excerpt(ev)


def test_long_evidence_says_it_was_cut():
    ev = "나" * (_EXCERPT_MAX + 500)
    out = _excerpt(ev)
    assert "발췌 절단" in out
    assert str(len(ev)) in out            # 원본 길이를 밝힌다
    assert out.startswith("나" * 100)      # 앞부분은 그대로


def test_real_evidence_sizes_fit():
    """실측 근거 크기(5,240자·7,669자)가 상한 안에 들어와야 한다 — 이번 함정의 원인."""
    assert _EXCERPT_MAX >= 7669


def test_none_and_empty():
    assert _excerpt(None) == ""
    assert _excerpt("") == ""
