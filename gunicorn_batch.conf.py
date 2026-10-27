# gunicorn_batch.conf.py — WeRUBLLMManager BATCH MODE config
# iMac M1 등 CPU 서버에서 배치 전용 운영 시 사용.
#
# 사용법:
#   BATCH_MODE=1 gunicorn -c gunicorn_batch.conf.py app:app

# 바인딩
bind = "127.0.0.1:8585"

# 워커 — 배치는 동시 요청이 적으므로 최소화
worker_class = "gthread"
workers = 1
threads = 4                    # 배치 전용: 32→4 (동시 API 요청 거의 없음)
worker_connections = 50

# 타임아웃 — CPU 추론은 느리므로 연장
timeout = 600                  # 배치 작업 최대 10분 (GPU 300초 → CPU 600초)
graceful_timeout = 30
keepalive = 5

# 로깅
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 프로세스
daemon = False
pidfile = None
preload_app = True

# 제한 — 배치는 장시간 운영이므로 넉넉하게
max_requests = 10000
max_requests_jitter = 1000
