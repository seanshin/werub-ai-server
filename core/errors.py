"""응답 에러 위생 (보안 L1) — 내부 예외 메시지를 클라이언트에 노출하지 않는다.

production은 debug=off라 미처리 예외는 Flask 기본 제네릭(스택 없음)이지만, 명시적
`jsonify({"error": str(e)}), 500`은 내부 예외 메시지(파일 경로·라이브러리 문자열)를
JSON으로 그대로 내보낸다. PHI/시크릿은 아니나 정보 누출이라, 500대는 제네릭 바디로
바꾸고 상세는 **서버 로그에만** 남긴다. 400대(사용자 입력 검증 메시지)는 유용하므로
이 헬퍼의 대상이 아니다.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ollama-admin")


def internal_error(exc: Exception, context: str = "", status: int = 500):
    """500 응답 — 클라이언트엔 제네릭, 서버 로그엔 상세. (message, status) 튜플 반환."""
    from flask import jsonify        # 지연 import — core는 Flask 없이도 import 가능해야 한다
    logger.warning("internal error%s: %s",
                   f" [{context}]" if context else "", exc)
    return jsonify({"error": "internal server error"}), status
