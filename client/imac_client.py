"""iMac M칩 서비스 클라이언트 — GPU 서버용 SDK

GPU 서버(Ubuntu)에서 iMac의 특화 서비스를 호출한다.
SSH 리버스 터널을 통해 localhost로 접근한다.

사용 예시:
    from client.imac_client import iMacClient

    client = iMacClient()
    if client.is_available():
        vectors = client.embed(["텍스트1", "텍스트2"])
        result  = client.chat("요약해줘")
        metrics = client.apple_silicon_metrics()
"""
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger("ollama-admin")


class iMacUnavailable(Exception):
    """iMac 터널 연결 불가"""


class iMacClient:
    """iMac M칩 서비스 클라이언트

    Args:
        base_url:    iMac Admin API (SSH 터널 경유, 기본값 localhost:8585)
        ollama_base: iMac Ollama    (SSH 터널 경유, 기본값 localhost:8586)
        api_key:     API 키 (터널 경유 시 불필요, 직접 접속 시 필요)
        timeout:     기본 타임아웃 (초)
        cache_ttl:   가용성 캐시 TTL (초)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8586",
        ollama_base: str = "http://localhost:8586",
        api_key: str = "",
        timeout: int = 30,
        cache_ttl: int = 30,
    ):
        self.base_url    = base_url.rstrip("/")
        self.ollama_base = ollama_base.rstrip("/")
        self.timeout     = timeout
        self._cache_ttl  = cache_ttl

        self._session = requests.Session()
        self._session.headers["Content-Type"] = "application/json"
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

        self._available: Optional[bool] = None
        self._available_ts: float = 0
        self._metrics_cache: dict = {}
        self._metrics_ts: float = 0

    # -------------------------------------------------------------------------
    # 연결 상태
    # -------------------------------------------------------------------------

    def is_available(self, force: bool = False) -> bool:
        """iMac 연결 가능 여부 (캐시 TTL 내 재사용)"""
        now = time.time()
        if not force and self._available is not None and (now - self._available_ts) < self._cache_ttl:
            return self._available
        try:
            r = self._session.get(f"{self.base_url}/api/system/platform", timeout=3)
            self._available = r.ok and r.json().get("platform") == "imac"
        except Exception:
            self._available = False
        self._available_ts = now
        return self._available

    def platform_info(self) -> dict:
        """iMac 플랫폼 정보 및 활성 기능 플래그"""
        r = self._session.get(f"{self.base_url}/api/system/platform", timeout=5)
        r.raise_for_status()
        return r.json()

    def _require_available(self):
        if not self.is_available():
            raise iMacUnavailable(f"iMac 연결 불가: {self.base_url}")

    # -------------------------------------------------------------------------
    # 임베딩
    # -------------------------------------------------------------------------

    def embed(
        self,
        texts: list[str],
        model: str = "bge-m3",
        use_coreml: bool = False,
        normalize: bool = True,
    ) -> list[list[float]]:
        """텍스트 임베딩 생성

        Args:
            texts:      임베딩할 텍스트 목록
            model:      Ollama 모델명 (use_coreml=False 시)
            use_coreml: True면 Core ML ANE 사용 (변환 완료 필요)
            normalize:  L2 정규화 여부

        Returns:
            임베딩 벡터 목록
        """
        self._require_available()

        if use_coreml:
            return self._embed_coreml(texts, normalize=normalize)
        return self._embed_ollama(texts, model=model)

    def _embed_ollama(self, texts: list[str], model: str = "bge-m3") -> list[list[float]]:
        r = self._session.post(
            f"{self.base_url}/api/embeddings",
            json={"model": model, "input": texts},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("embeddings", [])

    def _embed_coreml(self, texts: list[str], normalize: bool = True) -> list[list[float]]:
        r = self._session.post(
            f"{self.base_url}/api/embed/coreml",
            json={"input": texts, "normalize": normalize},
            timeout=60,
        )
        if r.status_code == 400:
            # Core ML 미변환 → Ollama 폴백
            logger.warning("[iMacClient] Core ML 미변환, Ollama 폴백")
            return self._embed_ollama(texts)
        r.raise_for_status()
        return r.json().get("embeddings", [])

    def embed_with_fallback(
        self,
        texts: list[str],
        local_embed_fn=None,
    ) -> list[list[float]]:
        """임베딩 — iMac 실패 시 로컬 폴백

        Args:
            texts:           임베딩할 텍스트 목록
            local_embed_fn:  폴백 함수 (없으면 빈 리스트 반환)
        """
        try:
            return self.embed(texts, use_coreml=True)
        except iMacUnavailable:
            logger.warning("[iMacClient] iMac 불가 — 로컬 폴백")
        except Exception as e:
            logger.warning("[iMacClient] embed 오류: %s — 로컬 폴백", e)

        if local_embed_fn:
            return local_embed_fn(texts)
        return []

    # -------------------------------------------------------------------------
    # STT
    # -------------------------------------------------------------------------

    def stt(
        self,
        file_path: str | None = None,
        file_url: str | None = None,
        language: str = "ko",
        model: str = "base",
        diarize: bool = False,
    ) -> dict:
        """음성 → 텍스트 변환 (faster-whisper + Metal)

        Args:
            file_path: 로컬 오디오 파일 경로 (file_url과 택1)
            file_url:  S3/HTTP URL (file_path와 택1)
            language:  언어 코드 (ko, en, ja, ...)
            model:     Whisper 모델 크기 (tiny, base, small, medium, large-v3)
            diarize:   화자 분리 여부

        Returns:
            {text, segments, language, duration_sec, engine, duration_ms}
        """
        self._require_available()

        if file_url:
            r = self._session.post(
                f"{self.base_url}/api/stt",
                json={"file_url": file_url, "language": language,
                      "model": model, "diarize": diarize},
                timeout=300,
            )
        elif file_path:
            with open(file_path, "rb") as f:
                r = requests.post(
                    f"{self.base_url}/api/stt",
                    files={"file": (os.path.basename(file_path), f)},
                    data={"language": language, "model": model, "diarize": str(diarize).lower()},
                    headers={k: v for k, v in self._session.headers.items()
                             if k != "Content-Type"},
                    timeout=300,
                )
        else:
            raise ValueError("file_path 또는 file_url 필요")

        r.raise_for_status()
        return r.json()

    # -------------------------------------------------------------------------
    # 경량 추론
    # -------------------------------------------------------------------------

    def chat(
        self,
        message: str,
        system: str = "",
        model: str | None = None,
        force_local: bool = True,
        use_gateway: bool = True,
    ) -> dict:
        """경량 추론 채팅

        Args:
            message:     사용자 메시지
            system:      시스템 프롬프트
            model:       모델명 (None이면 iMac 기본 모델)
            force_local: True면 iMac 로컬 처리 강제 (GPU 포워딩 없음)
            use_gateway: True면 Smart Gateway 사용, False면 Ollama 직접 호출

        Returns:
            {message, model, routed_to, duration_ms}
        """
        self._require_available()

        if use_gateway:
            payload = {"message": message, "force_local": force_local}
            if system:
                payload["system"] = system
            if model:
                payload["model"] = model
            r = self._session.post(
                f"{self.base_url}/api/gateway/chat",
                json=payload,
                timeout=self.timeout,
            )
            r.raise_for_status()
            return r.json()
        else:
            # Ollama 직접 호출
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": message})
            r = self._session.post(
                f"{self.ollama_base}/api/chat",
                json={"model": model or "qwen2.5:7b", "messages": messages, "stream": False},
                timeout=self.timeout,
            )
            r.raise_for_status()
            d = r.json()
            return {
                "message": d.get("message", {}).get("content", ""),
                "model": d.get("model", ""),
                "routed_to": "imac_direct",
                "duration_ms": round(d.get("total_duration", 0) / 1e6),
            }

    # -------------------------------------------------------------------------
    # M칩 메트릭
    # -------------------------------------------------------------------------

    def apple_silicon_metrics(self, use_cache: bool = True) -> dict:
        """M칩 실시간 메트릭 (CPU/GPU/ANE/메모리/열)

        Returns:
            {chip, cpu, gpu, memory, thermal, powermetrics}
        """
        self._require_available()
        now = time.time()
        if use_cache and self._metrics_cache and (now - self._metrics_ts) < 5:
            return self._metrics_cache

        r = self._session.get(f"{self.base_url}/api/system/apple-silicon", timeout=5)
        r.raise_for_status()
        self._metrics_cache = r.json()
        self._metrics_ts = now
        return self._metrics_cache

    def is_memory_ok(self, max_pressure: str = "warning") -> bool:
        """iMac 메모리 상태가 오프로드 가능한지 확인

        Args:
            max_pressure: 허용 최대 압박 수준 ("normal"|"warning"|"critical")
        """
        try:
            metrics = self.apple_silicon_metrics()
            pressure = metrics.get("memory", {}).get("pressure", "normal")
            order = {"normal": 0, "warning": 1, "critical": 2}
            return order.get(pressure, 2) <= order.get(max_pressure, 1)
        except Exception:
            return False

    def is_thermal_ok(self) -> bool:
        """iMac 열 상태가 오프로드 가능한지 확인"""
        try:
            metrics = self.apple_silicon_metrics()
            return not metrics.get("thermal", {}).get("throttling", False)
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # 모델 캐시 제어
    # -------------------------------------------------------------------------

    def cache_status(self) -> dict:
        """iMac 메모리에 로드된 모델 목록"""
        self._require_available()
        r = self._session.get(f"{self.base_url}/api/models/cache-status", timeout=5)
        r.raise_for_status()
        return r.json()

    def warm_model(self, model: str, keep_alive: str = "1h") -> bool:
        """iMac에서 모델 예열 요청

        Args:
            model:      예열할 모델명
            keep_alive: 유지 시간 (예: "1h", "30m", "-1" 영구)
        """
        self._require_available()
        r = self._session.post(
            f"{self.base_url}/api/models/warm",
            json={"model": model, "keep_alive": keep_alive},
            timeout=60,
        )
        return r.ok

    def unload_model(self, model: str) -> bool:
        """iMac에서 모델 언로드 요청"""
        self._require_available()
        r = self._session.post(
            f"{self.base_url}/api/models/unload",
            json={"model": model},
            timeout=15,
        )
        return r.ok

    # -------------------------------------------------------------------------
    # 오프로드 판단 헬퍼
    # -------------------------------------------------------------------------

    def should_offload(
        self,
        model: str,
        lightweight_models: set | None = None,
        max_memory_pressure: str = "warning",
    ) -> bool:
        """GPU 서버의 특정 작업을 iMac으로 오프로드해야 하는지 판단

        Args:
            model:                작업에 사용할 모델
            lightweight_models:   iMac으로 보낼 모델 목록 (None이면 7b 이하 모두)
            max_memory_pressure:  허용 최대 메모리 압박

        Returns:
            True면 iMac으로 오프로드
        """
        if not self.is_available():
            return False

        # 모델 크기 판단
        if lightweight_models is not None:
            if model not in lightweight_models:
                return False
        else:
            # 기본: 7b 이하만 오프로드 (파라미터 수 파싱)
            if not _is_lightweight_model(model):
                return False

        # 메모리/열 상태 확인
        return self.is_memory_ok(max_memory_pressure) and self.is_thermal_ok()

    # -------------------------------------------------------------------------
    # 컨텍스트 매니저
    # -------------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._session.close()

    def __repr__(self):
        return f"iMacClient(base={self.base_url}, available={self._available})"


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _is_lightweight_model(model: str, max_b: float = 8.0) -> bool:
    """모델명에서 파라미터 크기를 파싱하여 경량 여부 판단

    예) "qwen2.5:7b" → 7.0 ≤ 8.0 → True
        "qwen2.5:14b" → 14.0 > 8.0 → False
        "qwen3:32b"   → 32.0 > 8.0 → False
    """
    import re
    # "숫자b" 또는 "숫자.숫자b" 패턴 찾기 (단어 경계 기준)
    m = re.search(r'(?:^|[:\-_/])(\d+(?:\.\d+)?)b(?:\b|$)', model.lower())
    if not m:
        return True  # 크기 불명 → 일단 허용
    size = float(m.group(1))
    return size <= max_b


# ---------------------------------------------------------------------------
# 싱글톤 (GPU 서버 앱에서 공유 사용)
# ---------------------------------------------------------------------------

_default_client: iMacClient | None = None


def get_client(
    base_url: str = "http://localhost:8586",
    ollama_base: str = "http://localhost:8586",
    api_key: str = "",
) -> iMacClient:
    """글로벌 iMacClient 인스턴스 반환 (싱글톤)

    GPU 서버에서 호출 시 기본값:
      base_url    = http://localhost:8586  (SSH 리버스 터널 → iMac Admin API)
      ollama_base = http://localhost:8586  (iMac Admin API 경유로 Ollama 프록시)
    """
    global _default_client
    if _default_client is None:
        _default_client = iMacClient(
            base_url=base_url,
            ollama_base=ollama_base,
            api_key=api_key,
        )
    return _default_client
