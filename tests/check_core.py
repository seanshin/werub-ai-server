#!/usr/bin/env python3
"""core/ 모듈 import 검증"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from core.config import load_config, save_config, OLLAMA_BASE, COMFYUI_BASE
    print("OK: core.config")

    from core.cache import TTLCache, llm_response_cache, image_models_cache
    cache = TTLCache(maxsize=10, ttl=5)
    cache.set("test", "value")
    assert cache.get("test") == "value"
    print("OK: core.cache")

    from core.korean import force_korean, korean_quality_check, force_korean_token
    test = "这是中文test한국어"
    result = force_korean(test)
    assert "한국어" in result
    assert "中文" not in result
    print("OK: core.korean")

    from core.security import validate_webhook_url, PRIVATE_NETS
    assert validate_webhook_url("https://example.com/hook") == True
    assert validate_webhook_url("http://127.0.0.1/hook") == False
    print("OK: core.security")

    from core.queue_engine import ChatFairQueue
    q = ChatFairQueue(max_global=2, max_per_user=1)
    ok, _, _, _ = q.acquire("user1")
    assert ok
    assert q.active_total == 1
    q.release("user1")
    assert q.active_total == 0
    print("OK: core.queue_engine")

    from core.logger import setup_logger
    print("OK: core.logger")

    # Flask import 없는지 확인
    import importlib
    for mod_name in ["core.config", "core.cache", "core.korean", "core.security", "core.queue_engine", "core.logger"]:
        mod = importlib.import_module(mod_name)
        src = open(mod.__file__).read()
        if "from flask" in src or "import flask" in src:
            print(f"FAIL: {mod_name} contains Flask import!")
            sys.exit(1)
    print("OK: No Flask imports in core/")

    print("\nALL CHECKS PASSED")
except Exception as e:
    print(f"FAIL: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
