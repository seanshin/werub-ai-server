"""TTL 캐시 — LLM 응답, 이미지 모델 목록 + 응답 캐시 키 생성"""
import hashlib
import json
import re
import threading
import time
from collections import OrderedDict

# 요청 단위 캐시 우회 헤더 (2026-08-12)
#
# 왜 필요한가: 계약 스모크는 "실제 모델을 호출해 쓸 만한 답이 나오는가"를 본다고 선언해
# 놓고, **재실행하면 캐시를 읽는다**. 실측에서 medlaw·control·predict·adverse-drug
# 43체크가 0.001~0.015초로 지나갔고(한 글자·한 살만 바꾸면 4.3~6.4초) 모델을 부르지
# 않았다. 배포 경로는 HUP로 워커가 새로 떠서 콜드라 정상이지만, **"흔들리나 보려고
# 단독 재실행"** 하는 순간 그 43체크는 판정력을 잃는다 — 지난 답을 다시 볼 뿐이다.
#
# ⚠️ **로컬 직결 요청에서만** 허용한다. 외부에 열면 같은 질의를 반복해 매번 생성시킬 수
#    있어 캐시가 완충 역할을 못 한다.
BYPASS_HEADER = "X-No-Cache"

_LOOPBACK = ("127.0.0.1", "::1", "localhost")
_BYPASS_TRUE = ("1", "true", "yes", "on")


def bypass_allowed(header_value, remote_addr, forwarded=None) -> bool:
    """캐시 우회 요청인가 — 순수 판정.

    프록시 헤더가 하나라도 있으면 로컬로 보지 않는다(v2.109.2 H1과 같은 규율:
    CF/XFF가 붙는 경로를 로컬로 오인하면 인증·권한 판정이 통째로 무너진다).
    """
    if forwarded:
        return False
    if remote_addr not in _LOOPBACK:
        return False
    return (header_value or "").strip().lower() in _BYPASS_TRUE


class TTLCache:
    """스레드 안전 TTL 캐시 (maxsize + TTL 초과 시 자동 만료)"""

    def __init__(self, maxsize=256, ttl=300):
        self._cache = OrderedDict()
        self._maxsize = maxsize
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._cache.get(key)
            if item is None:
                return None
            if time.time() - item[1] > self._ttl:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return item[0]

    def set(self, key, value):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)
            self._cache[key] = (value, time.time())

    def clear(self):
        with self._lock:
            self._cache.clear()

    def __len__(self):
        with self._lock:
            return len(self._cache)


# ---------------------------------------------------------------------------
# 응답 캐시 키
# ---------------------------------------------------------------------------
# 캐시 키는 **출력을 바꾸는 입력을 전부** 담아야 한다. 빠뜨린 입력은 조용한 오답이 된다:
#   · 2026-07-30 그룹웨어 제보 — `/api/chat` 키가 `model + 사용자 메시지 텍스트`뿐이라
#     이미지·think가 달라도 같은 키였다. **A 고객 명함을 올렸는데 B 고객 응답**이 200으로
#     돌아오고(409ms), 오류가 아닌 그럴듯한 오답이라 호출자 로그에 흔적이 남지 않는다.
#   · 2026-06 번역 — 키가 `prompt[:200]`이라 보일러플레이트에 잘려 다른 발화가 같은 키였다.
# 그래서 필드를 열거해 넣지 말고, **요청에서 출력에 영향 있는 것을 통째로** 넘긴다.

_DATA_URI = re.compile(r"^data:[^;,]*;base64,", re.I)


def _image_digest(img):
    """이미지 입력 → 안정적 지문. `data:` 접두는 서버가 떼고 보내므로 동일 취급."""
    if not isinstance(img, str):
        img = str(img)
    return hashlib.sha256(_encode(_DATA_URI.sub("", img))).hexdigest()


def _encode(text: str) -> bytes:
    """해싱용 바이트 인코딩 — **짝 잃은 서로게이트를 허용**한다.

    2026-08-03: `/api/chat`이 `UnicodeEncodeError: surrogates not allowed`로 **500을
    1,726건** 냈다. JS 클라이언트가 이모지 중간에서 문자열을 자르면(`slice`) `\\ud83c`
    같은 반쪽 서로게이트가 넘어오는데, `ensure_ascii=False` JSON을 UTF-8로 인코딩하면
    거기서 터진다. **캐시 키 함수는 어떤 입력에도 죽으면 안 된다.**
    `surrogatepass`는 서로게이트를 보존해 인코딩하므로 **서로 다른 입력이 같은 키가 되지
    않는다**(`errors="replace"`는 충돌 위험이 있어 부적합).
    """
    return text.encode("utf-8", "surrogatepass")


def response_cache_key(payload: dict) -> str:
    """출력에 영향을 주는 요청 필드 dict → 캐시 키(정렬 직렬화 후 sha256)."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(_encode(blob)).hexdigest()


def chat_cache_key(model, messages, data=None) -> str:
    """`/api/chat` 응답 캐시 키.

    포함: 모델 · **모든 역할의 메시지**(system 포함) · **이미지 지문** · think ·
    options · format · tools. 하나라도 다르면 다른 키 = 다른 응답.
    """
    data = data or {}
    norm = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        imgs = m.get("images")
        if isinstance(imgs, (list, tuple)):
            digests = [_image_digest(i) for i in imgs]
        elif imgs:
            digests = [_image_digest(imgs)]
        else:
            digests = []
        norm.append({"role": m.get("role"), "content": m.get("content") or "",
                     "images": digests})
    return response_cache_key({
        "model": model,
        "messages": norm,
        "think": data.get("think"),
        "options": data.get("options"),
        "format": data.get("format"),
        "tools": data.get("tools"),
    })


def generate_cache_key(model, prompt, system=None, options=None, images=None) -> str:
    """`/api/generate`·큐 잡 응답 캐시 키(같은 규율 — options·이미지까지 포함)."""
    if isinstance(options, str):                     # 큐는 options를 JSON 문자열로 보관
        try:
            options = json.loads(options)
        except (ValueError, TypeError):
            pass
    digests = [_image_digest(i) for i in images] if isinstance(images, (list, tuple)) else []
    return response_cache_key({"model": model, "prompt": prompt, "system": system or "",
                               "options": options or None, "images": digests})


# 글로벌 캐시 인스턴스
llm_response_cache = TTLCache(maxsize=500, ttl=3600)   # 1시간
image_models_cache = TTLCache(maxsize=1, ttl=300)       # 5분
