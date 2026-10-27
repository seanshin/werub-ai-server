"""task → 모델 라우팅 — "어느 모델에 무엇을 물을 것인가"를 한 곳에서 결정한다.

🔑 **왜 필요한가**(2026-08-10 실측): Ollama를 직접 호출하는 파일이 **56개**이고
모델명 하드코딩이 `qwen2.5:14b` **141회**·`qwen2.5:7b` 62·`bge-m3` 32·`med42-8b` 22회다.
기존 추상화 셋은 목적이 다르다 — `dispatch`는 **노드**(어느 기계), `aux_infer`는 사이드카,
`client/rag`는 임베딩·검색. **"어느 모델"을 정하는 지점이 없었다.**

⚠️ **이 모듈은 provider 추상화가 목적이 아니다.** 지금 필요한 provider는 Ollama 하나뿐이고
LiteLLM 도입은 "두 번째 provider가 실제로 필요해질 때"가 트리거다(기획서 §9-4d).
목적은 **호출 지점을 한 곳으로 모아 나중에 갈아끼울 수 있게** 만드는 것이다.

## 경계 (seam이 하지 않는 것)

| 관심사 | 어디 | 왜 |
|---|---|---|
| 응답 캐시 | seam 위(호출자) | 키에 이미지·think·options가 들어가야 한다(v2.124.59) |
| 큐/배치 | seam 위 | 큐는 잡 수명주기이고 LLM 호출은 그 안의 한 단계 |
| 한국어 후처리 | seam 위 | §1-6에서 내용을 지운 사고 — 후처리는 도메인이 정한다 |
| 노드 분산 | 직교 | `dispatch.run`은 capability 단위 |
| keep_alive·think | **seam 안** | 모델 선택과 붙어 있다(§1-5·§1-7) |

## 안전 규율

- 🔴 **`policy=local_only` task는 외부 provider가 거부한다**(fail-closed). 의료·PHI·규제
  경로가 프론티어로 번지지 않게 코드가 막는다. PHI 비저장 확인서·식약처 규제 트랙 때문이다.
- **미등록 task는 즉시 실패**한다. 오타로 조용히 기본 모델을 타면 v2.124.62 클래스 사고다.
- 실패는 예외가 아니라 `Result`에 실린다(기존 `_gen_failed` 규율 계승).

⚠️ **`core/llm_gateway.py`(v2.51.0)와 다른 층이다.** 그쪽은 *모델명 → provider*를
정하는 **provider 게이트웨이**이고(OpenAI·Anthropic·Google·Groq·OpenRouter 지원,
현재 전부 `enabled:false`·API 키 없음), 여기는 *task → 모델*을 정한다.
그래서 LLMGateway가 있어도 **모델명 하드코딩 141회는 그대로 남아 있었다** —
호출자가 여전히 `"qwen2.5:14b"`를 손으로 적어 넘기기 때문이다.
두 층은 겹치지 않는다: 이 모듈이 모델명을 정하고, 필요하면 그것을 LLMGateway가 라우팅한다.

기획: docs/01-plan/features/model-decoupling-and-eval-driven-development.plan.md §6
"""
from __future__ import annotations

__author__ = "신현묵 (Shin Hyunmook) <8508132+seanshin@users.noreply.github.com>"
__license__ = "Proprietary — see LICENSE"

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("ollama-admin")

ROUTING_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "model_routing.json")

LOCAL_ONLY = "local_only"
ANY = "any"


class UnknownTask(KeyError):
    """등록되지 않은 task — 오타가 조용히 기본 모델을 타면 안 된다."""


class PolicyViolation(RuntimeError):
    """local_only task를 외부 provider로 보내려 했다(fail-closed)."""


class UnknownResolver(KeyError):
    """표가 지목한 `model_from` resolver가 없다 — 설정 오류라 조용히 넘기지 않는다."""


# ── 동적 모델 소스(resolver) ────────────────────────────────────────────────
# 🔑 **어떤 task는 모델이 표에 못 박힐 수 없다.** judge 모델은 `core/judge_config.py`가
# 정본이고(config → env → 기본값), 표에 값을 또 적으면 **정본이 둘**이 된다. 그러면
# config로 judge를 바꿨을 때 `/api/models/routing`이 옛 값을 계속 광고한다 —
# 규제 트랙은 그 엔드포인트 폴링으로 모델 교체를 감지하기로 한 계약이다(v2.124.12).
# ⇒ 표는 **어디서 가져올지**만 적고, 값은 정본에서 읽는다.
#
# 이름 등록제다(임의 import 문자열 금지) — config가 임의 모듈을 부르게 하면 안 된다.
def _resolve_judge_config(arg: str | None) -> str:
    from core.judge_config import judge_model
    return judge_model(arg or "quality")


_TOOLS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "tools.json")
_tools_cache = {"mtime": None, "data": {}}


def _resolve_tools_config(arg: str | None) -> str:
    """`config/tools.json`이 정본인 모델들 — 오케스트레이터 계열.

    🔑 **여기를 표로 옮기지 않는다.** `tools.json`은 운영 설정이고
    `POST /api/orchestrator/tools/reload`로 **무재배포 교체**되는 살아 있는 파일이다.
    표에 값을 또 적으면 reload로 바꾼 모델을 `/api/models/routing`이 계속 옛 값으로
    광고한다(judge 모델과 같은 클래스). 표는 **어디서 가져올지**만 적는다.

    파일을 직접 읽되 **mtime으로 캐시**한다 — reload는 즉시 반영되고 호출마다 읽지 않는다.
    (`ToolRegistry`는 모듈 싱글턴이 없고 오케스트레이터가 자기 인스턴스를 만든다.
     둘 다 **같은 파일**을 읽으므로 정본은 하나다.)

    arg: `general_chat` · `workflow_default_model` · 그 외=툴 콜링 `defaults`
    """
    try:
        mt = os.path.getmtime(_TOOLS_PATH)
        if _tools_cache["mtime"] != mt:
            with open(_TOOLS_PATH, encoding="utf-8") as f:
                _tools_cache["data"] = json.load(f)
            _tools_cache["mtime"] = mt
    except Exception as e:  # noqa: BLE001 — 표 조회가 파일 문제로 죽지 않는다
        logger.warning("[model_router] tools.json 읽기 실패: %s", e)
    d = _tools_cache["data"] or {}
    if arg == "general_chat":
        return str((d.get("general_chat") or {}).get("model") or "")
    if arg == "workflow_default_model":
        return str(d.get("workflow_default_model") or "")
    return str((d.get("defaults") or {}).get("model") or "")


_MODEL_RESOLVERS = {"judge_config": _resolve_judge_config,
                    "tools_config": _resolve_tools_config}


def _apply_model_from(spec: dict) -> dict:
    """`model_from: "<resolver>:<arg>"` → spec["model"]. 정본에서 읽어 온다."""
    ref = spec.get("model_from")
    if not ref:
        return spec
    name, _, arg = str(ref).partition(":")
    fn = _MODEL_RESOLVERS.get(name)
    if fn is None:
        raise UnknownResolver(
            f"미등록 resolver '{name}' — 등록된 것: {sorted(_MODEL_RESOLVERS)}")
    spec["model"] = fn(arg or None)
    spec["model_source"] = ref
    return spec


def _busy_fields(e) -> dict:
    """전송 예외가 Fair Queue 거절이면 그 종류를 Result에 보존한다(v2.125.68).
    스트림 경로(`stream`·`chat_stream`)는 예외를 그대로 던지므로 타입이 자연히 보존된다."""
    from core.llm_resilience import LLMBusyError
    if isinstance(e, LLMBusyError):
        return {"busy": True, "retry_after": e.retry_after}
    return {}


@dataclass
class Result:
    """정규화된 생성 결과. 실패해도 예외가 아니라 이 객체로 돌아온다."""
    text: str = ""
    tokens: int = 0
    duration_ms: int = 0
    model: str = ""
    provider: str = ""
    digest: str | None = None          # 로컬만 채워진다. 외부 API엔 digest가 없다(§3)
    finish_reason: str = "stop"
    ok: bool = True
    error: str = ""
    status: int | None = None          # HTTP 상태(로컬 어댑터). 4xx·5xx면 ok=False
    raw: dict = field(default_factory=dict)
    # v2.125.68: 실패가 Fair Queue 거절(LLMBusyError)이었나. 종전엔 예외가 문자열로만 실려
    # 호출자가 고장과 거절을 못 갈랐다(→ 공용 브레이커 실패로 세짐·의료 502). 성공 경로엔 영향 없다.
    busy: bool = False
    retry_after: int | None = None


@dataclass
class StreamChunk:
    """스트리밍 조각. `done=True`인 마지막 조각에만 집계가 실린다."""
    text: str = ""
    done: bool = False
    tokens: int = 0
    model: str = ""
    finish_reason: str = "stop"
    raw: dict = field(default_factory=dict)


# ── 라우팅 표 ────────────────────────────────────────────────────────────────
_DEFAULT_ROUTING = {
    "_doc": "task → 모델. 코드에 모델명을 쓰지 않는다.",
    "tasks": {},
    "defaults": {"policy": LOCAL_ONLY, "provider": "ollama"},
}


def load_routing(path: str | None = None) -> dict:
    """라우팅 표. 파일이 없으면 빈 표(= 모든 task가 미등록 → 명시 실패)."""
    p = path or ROUTING_PATH
    if not os.path.exists(p):
        return dict(_DEFAULT_ROUTING)
    with open(p, encoding="utf-8") as f:
        raw = json.load(f)
    raw.setdefault("tasks", {})
    raw.setdefault("defaults", dict(_DEFAULT_ROUTING["defaults"]))
    return raw


def resolve(task: str, routing: dict | None = None) -> dict:
    """task → {model, provider, policy, …}. 미등록이면 `UnknownTask`.

    🔑 **미등록을 기본값으로 흘리지 않는다.** 오타 하나가 조용히 다른 모델을 타면
    그 사실이 응답 어디에도 안 남는다(v2.124.62에서 없는 모델 지정이 예열을 매번
    무성 실패시키던 것과 같은 클래스).
    """
    r = routing if routing is not None else load_routing()
    spec = (r.get("tasks") or {}).get(task)
    if not spec:
        known = sorted((r.get("tasks") or {}).keys())
        raise UnknownTask(f"미등록 task '{task}' — 등록된 것: {known[:12]}")
    d = dict(r.get("defaults") or {})
    d.update(spec)
    d["task"] = task
    d.setdefault("policy", LOCAL_ONLY)
    d.setdefault("provider", "ollama")
    return _apply_model_from(d)


def check_policy(spec: dict, provider_is_local: bool) -> None:
    """local_only task를 외부로 보내려 하면 막는다(fail-closed).

    의료·PHI·규제 경로가 프론티어로 **번지지 않게** 하는 마지막 방어선이다.
    """
    if spec.get("policy") == LOCAL_ONLY and not provider_is_local:
        raise PolicyViolation(
            f"task '{spec.get('task')}'는 policy={LOCAL_ONLY} — "
            f"외부 provider '{spec.get('provider')}'로 보낼 수 없다(PHI·규제 경계)")


# ── 어댑터 ──────────────────────────────────────────────────────────────────
class OllamaAdapter:
    """현재 프로덕션 경로를 그대로 재현하는 기준선 어댑터.

    ⚠️ `routes/medical_law._generate`와 **동작이 같아야 한다** — 이관은 변경이 아니다.
    (think off·keep_alive 동반·options 기본값·실패 시 에러 문자열)
    """

    name = "ollama"
    is_local = True

    def __init__(self, base: str | None = None, session=None):
        self.base = base or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        self._session = session

    def _post(self, path: str, payload: dict, timeout: int, stream: bool = False):
        sess = self._session
        if sess is None:
            import requests
            sess = requests
        if stream:
            return sess.post(f"{self.base}{path}", json=payload, timeout=timeout, stream=True)
        return sess.post(f"{self.base}{path}", json=payload, timeout=timeout)

    def _payload(self, *, model, prompt, stream, system=None, options=None, schema=None,
                 images=None, think=None, keep_alive=None,
                 logprobs: bool = False, top_logprobs=None) -> dict:
        """요청 본문 — **스트림/비스트림이 여기 하나를 공유한다.**

        🔑 두 경로가 각자 본문을 만들면 조용히 갈라진다. 이 레포가 이미 겪은 클래스다:
        v2.124.59에서 `/api/chat`만 `options`·`think`·이미지 `data:` 접두 처리가 빠져
        **호출자가 준 값이 200 응답과 함께 버려지고 있었다**(로그에 흔적도 없었다).

        options는 `engine_defaults.pin_options`를 통과한다(D2 — `repeat_penalty 1.1`을
        엔진 기본값 의존에서 독립. 호출자 명시·모델 Modelfile 값이 이긴다).
        """
        from core import engine_defaults
        payload = {"model": model, "prompt": prompt, "stream": bool(stream),
                   "options": engine_defaults.pin_options(model, options, base=self.base)}
        if system is not None:
            payload["system"] = system
        if schema is not None:
            payload["format"] = schema          # §1-9 구조화 출력
        if images:
            payload["images"] = images          # 호출자가 정규화해 넘긴다(§1-4)
        if think is not None:
            payload["think"] = think            # 호출자가 capability 확인(§1-5)
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive  # §1-7 상주 유지
        if logprobs:
            # P6-1 Phase 0 — 판정 확률. **기본 False에서는 키 자체를 싣지 않는다**:
            # 기존 59 task의 페이로드가 바이트 단위로 동일해야 이관 동일성 회귀
            # (v2.125.13 자산)가 계속 그 의미를 갖는다.
            payload["logprobs"] = True
            if top_logprobs is not None:
                payload["top_logprobs"] = top_logprobs
        return payload

    def generate(self, *, model, prompt, system=None, options=None, schema=None,
                 images=None, think=None, keep_alive=None, timeout=60,
                 logprobs: bool = False, top_logprobs=None) -> Result:
        payload = self._payload(model=model, prompt=prompt, stream=False, system=system,
                                options=options, schema=schema, images=images,
                                think=think, keep_alive=keep_alive,
                                logprobs=logprobs, top_logprobs=top_logprobs)
        try:
            r = self._post("/api/generate", payload, timeout)
            # 🔴 **HTTP 오류를 삼키지 않는다.** 이 층이 없으면 500 응답이
            #    `response` 키 없는 JSON → **빈 문자열**로 조용히 흐른다(무성 실패).
            #    종전 평가 하네스가 `raise_for_status()`로 크게 실패시키던 그 층이다.
            status = getattr(r, "status_code", None)
            if status is not None and status >= 400:
                body = ""
                try:
                    body = str((r.json() or {}).get("error") or "")[:120]
                except Exception:  # noqa: BLE001
                    body = (getattr(r, "text", "") or "")[:120]
                logger.error("[model_router] HTTP %s model=%s: %s", status, model, body)
                return Result(model=model, provider=self.name, ok=False, status=status,
                              error=f"HTTP {status}: {body}" if body else f"HTTP {status}")
            d = r.json()
        except Exception as e:  # noqa: BLE001 — 실패는 Result에 싣는다
            logger.error("[model_router] generate failed model=%s: %s", model, e)
            return Result(model=model, provider=self.name, ok=False, error=str(e)[:200],
                          **_busy_fields(e))
        return Result(
            text=d.get("response") or "",
            tokens=d.get("eval_count") or 0,
            duration_ms=int((d.get("total_duration") or 0) / 1e6),
            model=model, provider=self.name,
            finish_reason=d.get("done_reason") or "stop",
            ok=True, status=getattr(r, "status_code", None), raw=d)

    def stream(self, *, model, prompt, system=None, options=None, schema=None,
               images=None, think=None, keep_alive=None, timeout=120):
        """토큰을 흘리는 생성 — `StreamChunk`를 yield하는 **제너레이터**.

        비스트림 `generate`와 **규율이 다르다**:

        | | 실패 처리 |
        |---|---|
        | `generate` | `Result(ok=False)`에 실어 돌려준다 |
        | `stream`   | **예외를 던진다** |

        스트리밍은 이미 토큰을 내보낸 뒤에 실패할 수 있어 "결과 객체"로 돌려줄 자리가 없다.
        호출자는 어차피 제너레이터 안에서 자기 오류 이벤트를 만들어야 하므로, 종전
        `raise_for_status()`가 하던 대로 **던지는 쪽이 이관 전과 동작이 같다**.

        🔴 **커넥션 회수는 이 층이 보장한다.** 클라이언트가 중간에 끊으면 호출자
        제너레이터에 `GeneratorExit`이 들어오고, 그것이 여기까지 전파된다.
        `finally`로 닫지 않으면 업스트림 커넥션이 풀에 남아 샌다.
        """
        payload = self._payload(model=model, prompt=prompt, stream=True, system=system,
                                options=options, schema=schema, images=images,
                                think=think, keep_alive=keep_alive)
        r = self._post("/api/generate", payload, timeout, stream=True)
        status = getattr(r, "status_code", None)
        if status is not None and status >= 400:
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
            logger.error("[model_router] stream HTTP %s model=%s", status, model)
            raise RuntimeError("HTTP %s" % status)
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                tok = d.get("response") or ""
                if tok:
                    yield StreamChunk(text=tok, model=model)
                if d.get("done"):
                    yield StreamChunk(done=True, tokens=d.get("eval_count") or 0,
                                      model=model,
                                      finish_reason=d.get("done_reason") or "stop", raw=d)
                    break
        finally:
            # 조기 종료(GeneratorExit)·예외 어느 쪽이든 반드시 닫는다.
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass

    # ── messages API(`/api/chat`) ───────────────────────────────────────────
    # `/api/generate`(prompt)와 **다른 엔드포인트**다. 대화 이력을 넘기는 경로
    # (counseling·orchestrator)는 이쪽을 쓴다. 두 API의 필드가 갈라진 채 방치되면
    # v2.124.59가 된다 — 그때 `/api/chat`만 `options`·`think`·이미지 처리가 빠져
    # **호출자가 준 값이 조용히 버려졌다**. 그래서 여기도 본문을 한 곳에서 만든다.
    def _chat_payload(self, *, model, messages, stream, options=None, schema=None,
                      think=None, keep_alive=None, tools=None) -> dict:
        from core import engine_defaults
        payload = {"model": model, "messages": messages, "stream": bool(stream),
                   "options": engine_defaults.pin_options(model, options, base=self.base)}
        if schema is not None:
            payload["format"] = schema
        if think is not None:
            payload["think"] = think
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        if tools:
            payload["tools"] = tools
        return payload

    def chat(self, *, model, messages, options=None, schema=None, think=None,
             keep_alive=None, tools=None, timeout=120) -> Result:
        """messages 기반 생성. 실패는 `Result`에 싣는다(`generate`와 같은 규율)."""
        payload = self._chat_payload(model=model, messages=messages, stream=False,
                                     options=options, schema=schema, think=think,
                                     keep_alive=keep_alive, tools=tools)
        try:
            r = self._post("/api/chat", payload, timeout)
            status = getattr(r, "status_code", None)
            if status is not None and status >= 400:
                logger.error("[model_router] chat HTTP %s model=%s", status, model)
                return Result(model=model, provider=self.name, ok=False, status=status,
                              error="HTTP %s" % status)
            d = r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("[model_router] chat failed model=%s: %s", model, e)
            return Result(model=model, provider=self.name, ok=False, error=str(e)[:200],
                          **_busy_fields(e))
        msg = d.get("message") or {}
        return Result(
            text=msg.get("content") or "",
            tokens=d.get("eval_count") or 0,
            duration_ms=int((d.get("total_duration") or 0) / 1e6),
            model=model, provider=self.name,
            finish_reason=d.get("done_reason") or "stop",
            ok=True, status=getattr(r, "status_code", None), raw=d)

    def chat_stream(self, *, model, messages, options=None, schema=None, think=None,
                    keep_alive=None, tools=None, timeout=120):
        """messages 스트리밍 — `StreamChunk` 제너레이터. 규율은 `stream`과 같다
        (실패는 **던진다** · 커넥션 회수는 `finally`가 보장)."""
        payload = self._chat_payload(model=model, messages=messages, stream=True,
                                     options=options, schema=schema, think=think,
                                     keep_alive=keep_alive, tools=tools)
        r = self._post("/api/chat", payload, timeout, stream=True)
        status = getattr(r, "status_code", None)
        if status is not None and status >= 400:
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass
            logger.error("[model_router] chat_stream HTTP %s model=%s", status, model)
            raise RuntimeError("HTTP %s" % status)
        try:
            for line in r.iter_lines():
                if not line:
                    continue
                d = json.loads(line)
                tok = (d.get("message") or {}).get("content") or ""
                if tok:
                    yield StreamChunk(text=tok, model=model)
                if d.get("done"):
                    yield StreamChunk(done=True, tokens=d.get("eval_count") or 0,
                                      model=model,
                                      finish_reason=d.get("done_reason") or "stop", raw=d)
                    break
        finally:
            try:
                r.close()
            except Exception:  # noqa: BLE001
                pass

    def embed(self, *, model, texts, ctx=2048, timeout=120) -> list:
        """§1-2 — 임베딩은 num_ctx를 **명시**한다. 전역 8192를 먹으면 KV 4.5GB로 OOM."""
        try:
            r = self._post("/api/embed", {"model": model, "input": texts,
                                          "options": {"num_ctx": ctx}}, timeout)
            return (r.json() or {}).get("embeddings") or []
        except Exception as e:  # noqa: BLE001
            logger.error("[llm_gateway] embed failed model=%s: %s", model, e)
            return []


_ADAPTERS = {"ollama": OllamaAdapter}


def get_adapter(provider: str, **kw):
    cls = _ADAPTERS.get(provider)
    if cls is None:
        raise ValueError(f"미구현 provider '{provider}' — 등록된 것: {sorted(_ADAPTERS)}")
    return cls(**kw)


# ── 공개 진입점 ─────────────────────────────────────────────────────────────
def generate(task: str, prompt: str, *, model=None, system=None, options=None, schema=None,
             images=None, think=None, keep_alive=None, timeout=60,
             routing=None, adapter=None, logprobs: bool = False, top_logprobs=None) -> Result:
    """역할 이름으로 생성한다. 모델은 config가 정한다.

    task는 `<domain>.<role>` 규약(예: `medical_law.answer`).
    미등록 task는 `UnknownTask`, 정책 위반은 `PolicyViolation`을 **던진다**
    (이 둘은 설정·배선 오류라 조용히 넘어가면 안 된다). 생성 실패만 Result에 실린다.

    `model`을 주면 라우팅 표를 **덮어쓴다**. 이건 편의 기능이 아니라 **이관 장치**다 —
    일부 경로는 모델을 **동적으로** 고른다(`medical_law`는 상주 모델을 따라간다:
    quality 모드에서 14b를 고정으로 부르면 상주 35b가 매 요청 축출돼 12초 콜드 복귀를
    물었다). 그 선택 로직을 라우팅 표의 고정값으로 바꾸면 **이관이 아니라 변경**이 된다.
    ⇒ 1단계는 모델 선택을 호출자에 남기고 **task 등록·정책·전송만** 가져온다.
       (동적 선택의 표 이관은 후속 단계 — 표에 resolver 개념이 필요하다.)

    ⚠️ 오버라이드해도 **정책은 task 기준으로 적용**된다. 모델을 바꾼다고 `local_only`가
       풀리면 경계가 무의미해진다.
    """
    spec = resolve(task, routing)
    ad = adapter or get_adapter(spec["provider"])
    check_policy(spec, getattr(ad, "is_local", False))
    kw = {}
    if logprobs:
        # 어댑터가 이 인자를 모를 수 있다(외부 provider 어댑터). 요청했을 때만 넘겨
        # **기존 어댑터 계약을 건드리지 않는다** — 미지원 어댑터는 TypeError 대신
        # 그냥 확률이 안 실린 Result를 주고, 호출자는 축 null로 처리한다.
        kw["logprobs"] = True
        if top_logprobs is not None:
            kw["top_logprobs"] = top_logprobs
    try:
        return ad.generate(model=model or spec["model"], prompt=prompt, system=system,
                           options=options if options is not None else spec.get("options"),
                           schema=schema, images=images, think=think,
                           keep_alive=keep_alive, timeout=timeout, **kw)
    except TypeError:
        if not kw:
            raise
        logger.warning("[model_router] adapter=%s logprobs 미지원 — 확률 없이 진행",
                       getattr(ad, "name", "?"))
        return ad.generate(model=model or spec["model"], prompt=prompt, system=system,
                           options=options if options is not None else spec.get("options"),
                           schema=schema, images=images, think=think,
                           keep_alive=keep_alive, timeout=timeout)


def stream(task: str, prompt: str, *, model=None, system=None, options=None, schema=None,
           images=None, think=None, keep_alive=None, timeout=120,
           routing=None, adapter=None):
    """스트리밍 생성 — `StreamChunk` 제너레이터.

    `generate`와 **같은 표·같은 정책**을 쓴다. 스트리밍만 정책을 안 보면
    `local_only` 경계가 절반만 서게 된다.

    ⚠️ task 미등록·정책 위반은 **첫 조각을 내기 전에** 던진다(제너레이터를 소비해야
    실행되므로, 호출자가 이터레이션을 시작한 시점에 난다).
    """
    spec = resolve(task, routing)
    ad = adapter or get_adapter(spec["provider"])
    check_policy(spec, getattr(ad, "is_local", False))
    return ad.stream(model=model or spec["model"], prompt=prompt, system=system,
                     options=options if options is not None else spec.get("options"),
                     schema=schema, images=images, think=think,
                     keep_alive=keep_alive, timeout=timeout)


def chat(task: str, messages, *, model=None, options=None, schema=None, think=None,
         keep_alive=None, tools=None, timeout=120, routing=None, adapter=None) -> Result:
    """messages 기반 생성 — `generate`와 **같은 표·같은 정책**."""
    spec = resolve(task, routing)
    ad = adapter or get_adapter(spec["provider"])
    check_policy(spec, getattr(ad, "is_local", False))
    return ad.chat(model=model or spec["model"], messages=messages,
                   options=options if options is not None else spec.get("options"),
                   schema=schema, think=think, keep_alive=keep_alive, tools=tools,
                   timeout=timeout)


def chat_stream(task: str, messages, *, model=None, options=None, schema=None, think=None,
                keep_alive=None, tools=None, timeout=120, routing=None, adapter=None):
    """messages 스트리밍 — `StreamChunk` 제너레이터."""
    spec = resolve(task, routing)
    ad = adapter or get_adapter(spec["provider"])
    check_policy(spec, getattr(ad, "is_local", False))
    return ad.chat_stream(model=model or spec["model"], messages=messages,
                          options=options if options is not None else spec.get("options"),
                          schema=schema, think=think, keep_alive=keep_alive, tools=tools,
                          timeout=timeout)


def embed(task: str, texts, *, ctx=None, timeout=120, routing=None, adapter=None) -> list:
    """임베딩. ⚠️ **임베딩 모델 교체는 전 코퍼스 재색인을 요구한다** —
    라우팅 표의 `requires_reindex`가 그 사실을 명시한다(기획서 §6-1f)."""
    spec = resolve(task, routing)
    ad = adapter or get_adapter(spec["provider"])
    check_policy(spec, getattr(ad, "is_local", False))
    return ad.embed(model=spec["model"], texts=texts,
                    ctx=ctx if ctx is not None else spec.get("ctx", 2048), timeout=timeout)


def routing_table(routing: dict | None = None) -> list:
    """`GET /api/models/routing`용 — task별 해석 결과. 규제 트랙 통지 계약을 잇는다."""
    r = routing if routing is not None else load_routing()
    out = []
    for task in sorted((r.get("tasks") or {}).keys()):
        s = resolve(task, r)
        out.append({"task": task, "model": s.get("model"), "provider": s.get("provider"),
                    "policy": s.get("policy"),
                    "model_source": s.get("model_source"),   # 동적 소스면 어디서 왔는지
                    "requires_reindex": bool(s.get("requires_reindex"))})
    return out
