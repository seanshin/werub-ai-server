"""고비용 엔드포인트 개별 레이트리밋 홀더 (보안 백로그 M6, v2.125.38).

배경(06-23 감사 M6): 전역 120/min뿐이라 단일 키가 그 전량을 LLM 생성·이미지·
아바타 같은 고비용 경로에 쏟으면 GPU 포화(상주 축출·gthread 고갈)가 가능했다.
임계는 실측에서 왔다 — 7일 journald 피크가 generate 5/min·chat 2·rag 3·
counseling 3·translate 5라, 아래 데코레이터로 주는 개별 상한(10~60/min)은
관측 피크의 6배 이상이면서 단일 키 독식은 막는다.

`limiter`는 app.py가 Limiter 생성 직후 주입한다. 주입 전(단독 blueprint 테스트·
배치 스크립트·오프라인 import)엔 데코레이터가 no-op이라 기존 동작이 깨지지 않는다.
STT 실시간 경로(/api/stt/chunk)는 **의도적으로 제외** — 세그먼트 연쇄 호출 계약이라
개별 상한이 실사용을 깨뜨릴 수 있고, 전역 120/min이 이미 덮는다.
"""
limiter = None  # app.py가 주입


def heavy_limit(rate: str):
    """고비용 라우트용 per-key 리밋. `@bp.route` 아래에 단다.

    마커 `_heavy_limit`은 회귀(소스/런타임 검사)가 대상 누락을 잡는 데 쓴다.
    """
    def deco(f):
        f._heavy_limit = rate
        if limiter is not None:
            return limiter.limit(rate)(f)
        return f
    return deco
