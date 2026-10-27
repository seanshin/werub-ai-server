"""Core 모듈 단위 테스트."""
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCache:
    """core.cache — TTL 캐시"""

    def test_set_get(self):
        from core.cache import TTLCache
        c = TTLCache(maxsize=10, ttl=60)
        c.set("key1", "val1")
        assert c.get("key1") == "val1"

    def test_miss(self):
        from core.cache import TTLCache
        c = TTLCache(maxsize=10, ttl=60)
        assert c.get("nonexistent") is None

    def test_maxsize(self):
        from core.cache import TTLCache
        c = TTLCache(maxsize=2, ttl=60)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)  # a가 evict됨
        assert c.get("a") is None
        assert c.get("c") == 3


class TestKorean:
    """core.korean — 한국어 필터"""

    def test_force_korean_removes_chinese(self):
        from core.korean import force_korean
        result = force_korean("这是中文テスト한국어")
        assert "한국어" in result
        assert "中文" not in result

    def test_force_korean_preserves_numbers(self):
        from core.korean import force_korean
        result = force_korean("2026년 5월 26일 한국어 테스트 123")
        assert "2026" in result
        assert "123" in result

    def test_force_korean_token_stream(self):
        from core.korean import force_korean_token
        assert force_korean_token("안녕") == "안녕"
        assert force_korean_token("hello") == "hello"  # 영어는 통과

    def test_quality_check(self):
        from core.korean import korean_quality_check
        passed, ratio, detail = korean_quality_check("이것은 한국어 텍스트입니다. 품질 검사를 통과해야 합니다.")
        assert passed is True
        assert ratio > 0.5

        passed, ratio, detail = korean_quality_check("This is English only text without any Korean.")
        assert passed is False


class TestSecurity:
    """core.security — SSRF 방지"""

    def test_valid_webhook(self):
        from core.security import validate_webhook_url
        assert validate_webhook_url("https://example.com/hook") is True

    def test_reject_private_ip(self):
        from core.security import validate_webhook_url
        assert validate_webhook_url("http://127.0.0.1/hook") is False
        assert validate_webhook_url("http://192.168.1.1/hook") is False
        assert validate_webhook_url("http://10.0.0.1/hook") is False


class TestQueueEngine:
    """core.queue_engine — 공정 큐"""

    def test_acquire_release(self):
        from core.queue_engine import ChatFairQueue
        q = ChatFairQueue(max_global=3, max_per_user=1)
        ok, _, _, _ = q.acquire("user1")
        assert ok is True
        assert q.active_total == 1
        q.release("user1")
        assert q.active_total == 0

    def test_per_user_limit(self):
        from core.queue_engine import ChatFairQueue
        q = ChatFairQueue(max_global=3, max_per_user=1)
        ok1, _, _, _ = q.acquire("user1")
        ok2, _, _, _ = q.acquire("user1")  # 같은 유저 2번째
        assert ok1 is True
        assert ok2 is False
        q.release("user1")

    def test_global_limit(self):
        from core.queue_engine import ChatFairQueue
        q = ChatFairQueue(max_global=2, max_per_user=1)
        q.acquire("user1")
        q.acquire("user2")
        ok3, _, _, _ = q.acquire("user3")  # 글로벌 한도 초과
        assert ok3 is False
        q.release("user1")
        q.release("user2")


class TestLLMResilience:
    """core.llm_resilience — 서킷 브레이커 + 타임아웃"""

    def test_adaptive_timeout(self):
        from core.llm_resilience import get_adaptive_timeout
        t14 = get_adaptive_timeout("qwen2.5:14b", "draft")
        t7 = get_adaptive_timeout("qwen2.5:7b", "draft")
        assert t14 > t7  # 14B가 7B보다 타임아웃 길어야 함

    def test_timeout_gemma4(self):
        from core.llm_resilience import get_adaptive_timeout
        t = get_adaptive_timeout("gemma4:latest")
        assert t >= 180

    def test_circuit_breaker(self):
        from core.llm_resilience import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=1)
        assert cb.can_execute() is True
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is False  # 2번 실패 후 차단


class TestNoFlaskInCore:
    """core 모듈에 Flask import가 없어야 함 (파일 내용 검사)"""

    def test_no_flask_imports(self):
        """규칙의 실제 의도 = **core가 Flask 없이도 import 가능해야 한다**.

        그래서 검사 대상은 '파일에 flask라는 글자가 있는가'가 아니라 **모듈 최상위 import**다.
        함수 안 지연 import는 import 시점에 Flask를 요구하지 않으므로 통과시킨다
        (종전 문자열 검사는 지연 import까지 실패로 잡아, 화이트리스트에 'lazy import'라고
        적어두고 실제로는 최상위 import인 파일이 섞이는 부정확한 상태였다).
        """
        import ast
        import glob

        core_files = glob.glob(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "core", "*.py"))
        # websocket_handler는 Blueprint 정의라 최상위 Flask 의존이 설계상 불가피(예외 명시).
        skip = {"websocket_handler.py"}
        for fpath in core_files:
            fname = os.path.basename(fpath)
            if fname in skip:
                continue
            tree = ast.parse(open(fpath).read(), filename=fname)
            for node in tree.body:                      # 최상위만 검사(함수 내부 = 지연 import)
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                assert not any(n.split(".")[0] == "flask" for n in names), \
                    f"{fname}: 모듈 최상위 Flask import — core는 Flask 없이 import 가능해야 한다"
