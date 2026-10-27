"""설정 관리 — config.json 기반"""
import json, os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "queue.db")

OLLAMA_BASE = "http://localhost:11434"
COMFYUI_BASE = "http://127.0.0.1:8188"
IMAC_OLLAMA_BASE = "http://localhost:8586"
DEFAULT_BIND_PORT = 8585


def get_self_base():
    """자기 자신 API 호출용 base URL"""
    cfg = load_config()
    port = cfg.get("bind_port", DEFAULT_BIND_PORT)
    return f"http://127.0.0.1:{port}"


def load_config():
    """config.json 로드 (없으면 빈 dict)"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg):
    """config.json 저장"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
