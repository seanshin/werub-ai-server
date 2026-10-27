"""Core ML 임베딩 모듈 — Apple Neural Engine 활용

bge-m3 또는 지정 sentence-transformer 모델을 Core ML (.mlpackage)로
변환하여 ANE(Apple Neural Engine)에서 직접 실행, 임베딩 속도를 향상시킨다.

사전 요건:
  pip install coremltools sentence-transformers

변환 실행:
  python -c "from core.coreml_embed import convert_model; convert_model()"
"""
import logging
import os
import sys
import time
import numpy as np

logger = logging.getLogger("ollama-admin")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODEL_DIR = os.path.join(_BASE_DIR, "coreml_models")
_DEFAULT_MODEL = "BAAI/bge-m3"
_DEFAULT_OUT = os.path.join(_MODEL_DIR, "bge-m3.mlpackage")

# 런타임 캐시
_coreml_model = None
_tokenizer = None
_model_path: str = ""


def is_available() -> bool:
    """Core ML 임베딩 사용 가능 여부"""
    if sys.platform != "darwin":
        return False
    try:
        import coremltools  # noqa
        return True
    except ImportError:
        return False


def get_model_path(model_id: str = _DEFAULT_MODEL) -> str:
    """변환된 .mlpackage 경로 반환"""
    safe_name = model_id.replace("/", "_").replace(":", "_")
    return os.path.join(_MODEL_DIR, f"{safe_name}.mlpackage")


def is_converted(model_id: str = _DEFAULT_MODEL) -> bool:
    """Core ML 변환 완료 여부"""
    return os.path.exists(get_model_path(model_id))


def convert_model(
    model_id: str = _DEFAULT_MODEL,
    max_seq_len: int = 512,
    compute_units: str = "ALL",  # ALL = CPU+GPU+ANE
) -> str:
    """sentence-transformer → Core ML 변환

    Args:
        model_id: HuggingFace 모델 ID
        max_seq_len: 최대 시퀀스 길이
        compute_units: "ALL" | "CPU_AND_NE" | "CPU_AND_GPU" | "CPU_ONLY"

    Returns:
        저장된 .mlpackage 경로
    """
    try:
        import coremltools as ct
        import torch
        from transformers import AutoTokenizer, AutoModel
    except ImportError as e:
        raise RuntimeError(f"변환 패키지 미설치: {e}\npip install coremltools sentence-transformers") from e

    os.makedirs(_MODEL_DIR, exist_ok=True)
    out_path = get_model_path(model_id)

    logger.info("[CoreML] 변환 시작: %s → %s", model_id, out_path)

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModel.from_pretrained(model_id, torchscript=True)
    model.eval()

    # 더미 입력으로 trace
    dummy = tokenizer(
        "Apple Silicon Neural Engine test",
        return_tensors="pt",
        padding="max_length",
        max_length=max_seq_len,
        truncation=True,
    )

    with torch.no_grad():
        traced = torch.jit.trace(
            model,
            (dummy["input_ids"], dummy["attention_mask"]),
            strict=False,
        )

    # Core ML 변환
    cu = {
        "ALL": ct.ComputeUnit.ALL,
        "CPU_AND_NE": ct.ComputeUnit.CPU_AND_NE,
        "CPU_AND_GPU": ct.ComputeUnit.CPU_AND_GPU,
        "CPU_ONLY": ct.ComputeUnit.CPU_ONLY,
    }.get(compute_units, ct.ComputeUnit.ALL)

    ml_model = ct.convert(
        traced,
        inputs=[
            ct.TensorType(name="input_ids", shape=dummy["input_ids"].shape, dtype=np.int32),
            ct.TensorType(name="attention_mask", shape=dummy["attention_mask"].shape, dtype=np.int32),
        ],
        compute_units=cu,
        minimum_deployment_target=ct.target.macOS13,
    )

    ml_model.save(out_path)
    logger.info("[CoreML] 변환 완료: %s (%.1f MB)", out_path,
                sum(os.path.getsize(os.path.join(dp, f))
                    for dp, _, fs in os.walk(out_path) for f in fs) / 1e6)
    return out_path


def load_model(model_id: str = _DEFAULT_MODEL) -> bool:
    """변환된 Core ML 모델을 메모리에 로드"""
    global _coreml_model, _tokenizer, _model_path

    if _coreml_model and _model_path == model_id:
        return True

    path = get_model_path(model_id)
    if not os.path.exists(path):
        logger.warning("[CoreML] 변환된 모델 없음: %s", path)
        return False

    try:
        import coremltools as ct
        from transformers import AutoTokenizer
        _coreml_model = ct.models.MLModel(path)
        _tokenizer = AutoTokenizer.from_pretrained(model_id)
        _model_path = model_id
        logger.info("[CoreML] 모델 로드 완료: %s", model_id)
        return True
    except Exception as e:
        logger.error("[CoreML] 모델 로드 실패: %s", e)
        return False


def embed(texts: list[str], model_id: str = _DEFAULT_MODEL, normalize: bool = True) -> list[list[float]]:
    """Core ML ANE로 임베딩 생성

    Args:
        texts: 임베딩할 텍스트 목록
        model_id: 사용할 모델 ID
        normalize: L2 정규화 여부

    Returns:
        임베딩 벡터 목록
    """
    if not is_available():
        raise RuntimeError("Core ML 미지원 환경 (macOS 전용)")

    if not _coreml_model or _model_path != model_id:
        if not load_model(model_id):
            raise RuntimeError(f"Core ML 모델 로드 실패. 먼저 convert_model() 실행 필요")

    embeddings = []
    for text in texts:
        enc = _tokenizer(
            text,
            return_tensors="np",
            padding="max_length",
            max_length=512,
            truncation=True,
        )
        out = _coreml_model.predict({
            "input_ids": enc["input_ids"].astype(np.int32),
            "attention_mask": enc["attention_mask"].astype(np.int32),
        })
        # last_hidden_state의 [CLS] 토큰 벡터
        hidden = list(out.values())[0]
        vec = hidden[0][0] if hidden.ndim == 3 else hidden[0]

        if normalize:
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm

        embeddings.append(vec.tolist())

    return embeddings


def get_status() -> dict:
    """Core ML 임베딩 상태 반환"""
    available = is_available()
    return {
        "available": available,
        "platform": sys.platform,
        "model_loaded": _coreml_model is not None,
        "model_id": _model_path or None,
        "converted_models": _list_converted(),
        "coremltools": _get_coremltools_version(),
        "model_dir": _MODEL_DIR,
    }


def _list_converted() -> list[str]:
    if not os.path.exists(_MODEL_DIR):
        return []
    return [
        d.replace(".mlpackage", "").replace("_", "/", 1)
        for d in os.listdir(_MODEL_DIR)
        if d.endswith(".mlpackage")
    ]


def _get_coremltools_version() -> str | None:
    try:
        import coremltools
        return coremltools.__version__
    except ImportError:
        return None
