# gunicorn.conf.py — WeRUBLLMManager production config
import multiprocessing
import os

# 바인딩
bind = "127.0.0.1:8585"

# 워커 — gthread (스레드 기반, SQLite 호환)
worker_class = "gthread"
workers = 1                # 워커 프로세스 (워커 루프는 1개만 실행되므로 1 유지)
threads = 96               # 워커당 스레드 (동시 API 처리, 16→24→32→96: SSE 스트림 스레드 점유 대비, v2.71)
worker_connections = 200

# 풀 포화 경보의 분모 — 앱은 gunicorn 설정을 직접 못 읽으므로 env로 내보낸다(v2.124.94).
# 마스터가 설정을 로드할 때 설정되고 워커가 fork로 상속한다. 여기가 단일 소스이므로
# threads 값을 바꾸면 경보 임계도 자동으로 따라온다(절대 임계 하드코딩 금지).
os.environ["GUNICORN_THREADS"] = str(threads)

# 타임아웃
timeout = 300              # Ollama 추론 최대 300초
graceful_timeout = 30
keepalive = 5

# 로깅
accesslog = "-"
errorlog = "-"
loglevel = "info"

# 프로세스
daemon = False
pidfile = None
preload_app = False         # ⚠️ False 필수 — True면 마스터가 fork 전 queue.db 커넥션을 열고
                            # 단일 워커가 SQLite -shm(MAP_SHARED) mmap을 상속 → 워커의 새 connect가
                            # 데드락(/api/chat get_db wedge, 격리 connect는 0ms인데 워커만 행, race라 간헐).
                            # workers=1이라 preload 이득 거의 없음. 워커가 직접 import→fork 상속 제거.
                            # [[feedback_get_db_busy_timeout]] 2026-06-08 chat 행 진짜 근원.

# 제한
max_requests = 5000        # 메모리 누수 방지: 5000 요청 후 워커 재시작
max_requests_jitter = 500  # 동시 재시작 방지


# ── SSE graceful drain (v2.75) ──
# 재시작 시 SSE 스트림 스레드를 graceful_timeout(30s) 대기 없이 즉시 회수하고,
# 클라이언트에 shutdown을 통지해 재연결 폭주(스레드 고갈 트리거)를 방지한다.
# 참고: 인시던트 project_incident_worker_deadlock

def _drain_sse():
    try:
        import app as _app
        _app.signal_shutdown()
    except Exception:
        pass


def post_worker_init(worker):
    # 워커 프로세스에서 SIGTERM(systemctl restart 경로)을 가로채 드레인 후
    # gunicorn 원래 핸들러로 체이닝(graceful stop 동작 보존).
    import signal
    prev = signal.getsignal(signal.SIGTERM)

    def _on_term(signum, frame):
        _drain_sse()
        if callable(prev):
            prev(signum, frame)

    try:
        signal.signal(signal.SIGTERM, _on_term)
    except Exception:
        pass


def worker_int(worker):
    # SIGINT/SIGQUIT 경로 (gunicorn이 워커에서 호출)
    _drain_sse()
