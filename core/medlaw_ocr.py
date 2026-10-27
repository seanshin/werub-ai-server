"""medlaw 파싱 붕괴 PDF의 OCR 폴백 (v2.125.37).

배경: toUnicode 결손 PDF는 텍스트 추출이 한글을 통째로 잃는다(530076-1 실사고 —
한글 2.1%). v2.125.36 가드는 그런 문서를 색인에서 빼기만 하므로, 대체 첨부가 없으면
그 고시가 코퍼스에서 사라진다. 여기서 deepseek-ocr(함대 보유)로 페이지 이미지를
읽어 구제한다.

채택 근거(사전 등록 측정 — docs/01-plan/references/medlaw-ocr-fallback-preregistration
-2026-09-03.md): 실사고 PDF 8p를 정답 hwpx와 대조 — 단어 재현율 0.983 · 핵심 식별자
5/5 · 1.2분. ⚠️OCR도 LLM이라 표에서 `---` 반복 폭주가 났다(done=length) —
`clean_ocr_text`가 그 잔해를 결정적으로 정리한다(v2.125.25 triage 폭주 잔해와 같은
클래스: 폴백이 폭주 잔해를 그대로 내보내면 안 된다).

호출 맥락: 스케줄러 색인 경로 전용(요청 경로 아님 — subprocess 허용). deepseek-ocr
적재(6.7GB)가 상주 모델을 일시 축출하지만 발생 빈도가 낮고(코퍼스 146파일 중 1건)
배치 시간대(05 UTC)라 허용.
"""
import base64
import glob
import logging
import os
import re
import shutil
import subprocess
import tempfile

logger = logging.getLogger("ollama-admin")

MAX_PAGES = 30          # 고시문은 짧다 — 폭주 비용 상한
_PAGE_TIMEOUT = 180     # 페이지당 OCR 상한(s)


def clean_ocr_text(text: str) -> str:
    """OCR 폭주 잔해 정리(결정적) — 구두점·구분선 전용 줄 제거 + 동일 줄 3연속부터 붕괴.

    실측 폭주 형상: 표 렌더링이 `---`·`|` 구분선 줄을 num_predict가 다할 때까지 반복
    (p8에서 11,698자 중 대부분). 내용 줄(한글·영숫자 포함)은 건드리지 않는다.
    """
    out: list = []
    prev = None
    run = 0
    for line in text.splitlines():
        s = line.strip()
        if s and not re.search(r"[가-힣A-Za-z0-9]", s):
            continue                     # '---' '| | |' 같은 구분선 전용 줄
        if s == prev:
            run += 1
            if run >= 2:                 # 같은 줄 3연속부터 버린다
                continue
        else:
            run = 0
        prev = s
        out.append(line)
    return "\n".join(out)


def ocr_pdf_text(pdf_path: str, dpi: int = 200) -> str | None:
    """PDF → 페이지 PNG(pdftoppm) → deepseek-ocr → 정리된 전문. 실패는 None.

    라우터 task `medlaw.ocr` 경유(모델명은 config/model_routing.json이 정한다).
    페이지 단위 실패는 그 페이지만 비우고 계속한다(문서 전체를 잃지 않는다).
    """
    if shutil.which("pdftoppm") is None:
        logger.warning("[medlaw-ocr] pdftoppm 없음 — OCR 폴백 불가")
        return None
    from core import model_router

    with tempfile.TemporaryDirectory(prefix="medlaw_ocr_") as td:
        try:
            subprocess.run(["pdftoppm", "-r", str(dpi), "-png", pdf_path,
                            os.path.join(td, "pg")],
                           check=True, capture_output=True, timeout=120)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[medlaw-ocr] pdftoppm 실패 {os.path.basename(pdf_path)[:50]}: {e}")
            return None
        pages = sorted(glob.glob(os.path.join(td, "pg*.png")))
        if not pages:
            return None
        if len(pages) > MAX_PAGES:
            logger.warning(f"[medlaw-ocr] {len(pages)}p > 상한 {MAX_PAGES} — 앞 {MAX_PAGES}p만")
            pages = pages[:MAX_PAGES]

        texts: list = []
        for i, p in enumerate(pages, 1):
            try:
                img = base64.b64encode(open(p, "rb").read()).decode()
                res = model_router.generate(
                    "medlaw.ocr", "Free OCR.", images=[img],
                    keep_alive="2m",         # 페이지 루프 동안 재적재 방지
                    timeout=_PAGE_TIMEOUT)
                if res.ok:
                    texts.append(res.text)
                else:
                    logger.warning(f"[medlaw-ocr] p{i} 실패: {res.error or res.status}")
                    texts.append("")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[medlaw-ocr] p{i} 예외: {e}")
                texts.append("")
        joined = "\n".join(texts)
    return clean_ocr_text(joined) if joined.strip() else None
