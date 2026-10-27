# iMac M1 워커 gunicorn 설정 — apple-silicon-worker Phase 1
# 실행: venv-mac/bin/gunicorn -c gunicorn_worker.conf.py worker_app:app

bind = "127.0.0.1:8587"   # 터널 전용 (독립 LAN 노출은 WORKER_BIND env로 오버라이드)
import os
if os.environ.get("WORKER_BIND"):
    bind = os.environ["WORKER_BIND"]

workers = 1               # 단일 프로세스 — 모델(MPS) 메모리 중복 적재 방지
threads = 8               # 동시 요청 처리 (detect ~0.1s 짧은 작업 위주)
worker_class = "gthread"
timeout = 300             # STT(Phase 2) 대비
graceful_timeout = 30
keepalive = 5

accesslog = "/Users/user/ai/ollama-admin/logs/worker_access.log"
errorlog = "/Users/user/ai/ollama-admin/logs/worker.log"
loglevel = "info"
