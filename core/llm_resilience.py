"""LLM 호출 복원력 — 서킷 브레이커 + 요청 병합 + 적응형 타임아웃"""
import hashlib
import logging
import threading
import time
from concurrent.futures import Future

logger = logging.getLogger("ollama-admin")


# ---------------------------------------------------------------------------
# P1-1: 적응형 타임아웃
# ---------------------------------------------------------------------------

# 모델 크기 기반 기본 타임아웃 (초)
_MODEL_TIMEOUT = {
    "qwen2.5:14b": 90,
    "qwen2.5:7b": 45,
    "qwen3:8b": 60,              # thinking 모드 포함 (v2.62)
    "phi4-reasoning": 120,        # 추론 특화, 긴 사고 과정 (v2.62)
    "qwen3-embedding": 20,        # 임베딩 (v2.62)
    "meditron": 60,
    "medllama2": 60,
    "qwen2.5:3b": 30,
    "minicpm-v": 90,
    "llama3.2:3b": 30,
    "gemma4": 180,           # 26B MoE
    "exaone-deep": 120,
    "dnotitia/dna": 120,
    "mindwell": 60,               # 상담 8B (v2.62)
    # v2.69.0 신규 모델
    "gpt-oss": 180,               # 20B MoE, o3-mini급 추론
    "gemma4:26b": 180,            # 26B MoE
    "qwen3:14b": 90,              # 14B 추론 강화
    "qwen3-coder": 60,            # 30B MoE (활성 3.3B, 빠름)
    "bge-m3": 15,
    "nomic-embed-text": 15,
    "mxbai-embed-large": 15,
}

# 작업별 가산 (초)
_ACTION_TIMEOUT_BONUS = {
    "summarize": 30,   # SOAP 구조화 — 긴 출력
    "analyze": 20,     # 감별진단
    "draft": 30,       # 의무기록 작성
    "explain": 0,      # 간단한 설명
    "reference": 0,    # RAG 답변
    "translate": 10,   # 번역 (길이 비례)
    "batch": 60,       # 배치 처리
    "embed": -10,      # 임베딩 (빠름)
    "meeting_analysis": 60,  # 회의록 요약 (긴 입력)
}


def get_adaptive_timeout(model: str, action: str = "", default: int = 120) -> int:
    """모델+작업 기반 적응형 타임아웃 반환"""
    # 모델 기본값 (알 수 없는 모델은 default)
    base = default
    for key, val in _MODEL_TIMEOUT.items():
        if key in model:
            base = val
            break

    # 작업별 가산
    bonus = _ACTION_TIMEOUT_BONUS.get(action, 0)
    result = max(base + bonus, 15)  # 최소 15초

    return result


# ---------------------------------------------------------------------------
# P1-3: 요청 중복 제거 (Inflight Deduplication)
# ---------------------------------------------------------------------------

_inflight: dict[str, Future] = {}
_inflight_lock = threading.Lock()


def _make_dedup_key(model: str, prompt: str, system: str) -> str:
    """요청 해시 생성"""
    raw = f"{model}:{system[:100]}:{prompt[:500]}"
    return hashlib.md5(raw.encode()).hexdigest()


def dedup_call(key: str, fn, *args, **kwargs):
    """동일 키의 진행 중인 요청이 있으면 결과 공유, 없으면 새로 실행

    대기자는 원 요청이 실패·시간 초과하면 None을 받는다(호출자가 실패로 처리).
    ⚠️ 대기는 반드시 락 **밖**에서 — 종전엔 락을 쥔 채 최대 180s 기다려
    키가 다른 모든 요청과 원 요청의 정리(finally)까지 직렬화됐다(v2.125.45).
    """
    with _inflight_lock:
        waiting = _inflight.get(key)
        if waiting is None:
            future = Future()
            _inflight[key] = future

    if waiting is not None:
        logger.debug("Dedup hit: %s", key[:12])
        try:
            return waiting.result(timeout=180)
        except Exception as e:
            # 무성 소실 금지 — 대기자가 None을 받는 이유를 남긴다
            logger.warning("[dedup] 대기 실패 key=%s: %s: %s",
                           key[:12], type(e).__name__, e)
            return None

    try:
        result = fn(*args, **kwargs)
        future.set_result(result)
        return result
    except Exception as e:
        future.set_exception(e)
        raise
    finally:
        # BaseException(스레드 중단 등)으로 빠져도 대기자가 180s를 헛기다리지 않게
        if not future.done():
            future.set_exception(RuntimeError("dedup owner aborted"))
        with _inflight_lock:
            if _inflight.get(key) is future:
                _inflight.pop(key, None)


# ---------------------------------------------------------------------------
# P1-4: 서킷 브레이커
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """Ollama 서킷 브레이커 — 연속 실패 시 빠른 실패 반환

    States:
        CLOSED  → 정상 운영, 요청 통과
        OPEN    → 차단, 즉시 에러 반환 (recovery_timeout 후 HALF_OPEN)
        HALF_OPEN → 시험 요청 1건 통과, 성공 시 CLOSED, 실패 시 OPEN
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(self, failure_threshold=3, recovery_timeout=30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = self.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        """요청 실행 가능 여부"""
        with self._lock:
            if self.state == self.CLOSED:
                return True
            if self.state == self.OPEN:
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = self.HALF_OPEN
                    logger.info("Circuit breaker → HALF_OPEN (recovery attempt)")
                    return True
                return False
            # HALF_OPEN: 1건만 통과
            return True

    def record_success(self):
        """성공 기록"""
        with self._lock:
            if self.state == self.HALF_OPEN:
                logger.info("Circuit breaker → CLOSED (recovered)")
            self.state = self.CLOSED
            self.failure_count = 0

    def record_failure(self):
        """실패 기록"""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = self.OPEN
                logger.warning("Circuit breaker → OPEN (failures=%d)", self.failure_count)

    @property
    def is_open(self) -> bool:
        return self.state == self.OPEN


# 글로벌 서킷 브레이커 인스턴스
ollama_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)


# ---------------------------------------------------------------------------
# v2.125.68: 대기열 거절(busy) — 백엔드 고장이 아니라 **우리 자신의 부하 조절**
# ---------------------------------------------------------------------------
# 🔴 왜 따로 두나(2026-09-30 HIS 실측): ChatFairQueue가 사용자당 동시 1·대기 60s를 넘겨 거절하면
#    `_GatedOllamaSession`이 그냥 RuntimeError를 던졌고, 라우터(core 라우터 모듈)가 문자열로 싣고, medical이
#    502로 냈다(`active=1/3` — 전역 슬롯은 남아 있었다). 더 큰 문제는 그 거절이 위의
#    **ollama_breaker 실패로 세졌다**는 것 — 브레이커는 의료·상담·스토리·소설·글쓰기가 공유하고
#    연속 3회면 OPEN이라, 한 클라이언트의 동시 요청 3건이 거절되면 **전 도메인이 30초 503**이 된다.
#    (60일 OPEN 2건은 둘 다 09-28 타임아웃 건 — busy로 열린 실례는 아직 0 ⇒ 이건 예방이다.)
# ⚠️ RuntimeError를 상속한다 — 기존 `except RuntimeError`/`except Exception` 호출자는 그대로 잡는다.
#    구분이 필요한 곳만 `is_busy()`로 갈라 쓴다.
BUSY_RETRY_AFTER_S = 5   # /api/chat 계열의 기존 429 응답(retry_after 5)과 맞춘다


class LLMBusyError(RuntimeError):
    """Fair Queue 슬롯 획득 실패. 메시지 문자열은 종전과 같다(로그 grep 호환)."""

    def __init__(self, message: str, *, active=None, max_global=None, wait_queue=None,
                 max_wait_queue=None, per_user=None, waited_s=None,
                 retry_after: int = BUSY_RETRY_AFTER_S):
        super().__init__(message)
        self.active = active
        self.max_global = max_global
        self.wait_queue = wait_queue
        self.max_wait_queue = max_wait_queue
        self.per_user = per_user
        self.waited_s = waited_s
        self.retry_after = retry_after


def is_busy(x) -> bool:
    """예외 또는 라우터 Result가 대기열 거절인가."""
    return isinstance(x, LLMBusyError) or bool(getattr(x, "busy", False))


def result_error(res, default: str = "generate failed") -> RuntimeError:
    """실패한 라우터 Result → 던질 예외. busy면 LLMBusyError(종류 보존), 아니면 종전 RuntimeError."""
    msg = getattr(res, "error", "") or default
    if is_busy(res):
        return LLMBusyError(msg, retry_after=getattr(res, "retry_after", None) or BUSY_RETRY_AFTER_S)
    return RuntimeError(msg)


def record_failure_unless_busy(err, breaker=None) -> bool:
    """브레이커에 실패를 기록하되 **busy는 제외**한다. 기록했으면 True."""
    if is_busy(err):
        logger.info("[breaker] busy 거절은 실패로 세지 않음: %s", str(err)[:160])
        return False
    (breaker or ollama_breaker).record_failure()
    return True


def busy_payload(err) -> tuple[dict, dict]:
    """429 응답 본문과 헤더 — /api/chat 계열의 기존 429 형식에 맞춘다."""
    ra = getattr(err, "retry_after", None) or BUSY_RETRY_AFTER_S
    body = {"error": "server busy", "code": "server_busy", "retry_after": ra,
            "message": "동시 처리 한도 초과 — 잠시 후 다시 시도하세요",
            "detail": str(err)[:300]}
    return body, {"Retry-After": str(ra)}
