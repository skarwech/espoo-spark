"""Cached Claude calls with JSON-schema output (docs/method.md §6).

Every response is stored under data/llm_cache/<task>/<sha256>.json, keyed by (model, effort, system, user, schema, attempt).
Demo runs replay the cache, so outputs are identical across runs. Uncached calls are appended to the ledger with their cost,
and a process stops once SPARK_LLM_BUDGET_USD (default 25) is spent.
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "llm_cache"
LEDGER = CACHE / "_ledger.jsonl"
MODEL = "claude-opus-5-5"
EFFORT = "medium"
API_URL = "https://api.anthropic.com"  # pinned: never inherit ANTHROPIC_BASE_URL from the host shell
PRICE_PER_MTOK = {"input_tokens": 4.0, "output_tokens": 20.0, "cache_read_input_tokens": 0.20, "cache_creation_input_tokens": 5.0}
BUDGET_USD = float(os.environ.get("SPARK_LLM_BUDGET_USD", "25"))
# Amendment A1 (method.md) was not completed; it is opt-in (SPARK_AMENDMENT=A1). The default engine is the registered run v1.
A1 = os.environ.get("SPARK_AMENDMENT", "") == "A1"

_lock = threading.Lock()
_spent = 0.0
_client = None


class LLMError(RuntimeError):
    pass


def _api_key() -> str:
    # SPARK_ANTHROPIC_API_KEY first: a project-specific name in a shell profile does not change how Claude Code itself authenticates.
    key = os.environ.get("SPARK_ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise LLMError("No API key: export SPARK_ANTHROPIC_API_KEY (or put ANTHROPIC_API_KEY in spark/.env). Cached outputs still replay.")
    return key


def client() -> anthropic.Anthropic:
    global _client
    with _lock:
        if _client is None:
            _client = anthropic.Anthropic(api_key=_api_key(), base_url=API_URL, max_retries=4,
                                     timeout=anthropic.Timeout(600.0, read=120.0))  # SSE pings flow while generating; 2 min of silence = stalled
    return _client


def cache_key(task: str, system: str, user: str, schema: dict, attempt: int) -> str:
    blob = json.dumps({"model": MODEL, "effort": EFFORT, "task": task, "system": system, "user": user,
                       "schema": schema, "attempt": attempt}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cost(usage: dict) -> float:
    return sum((usage.get(k) or 0) * p for k, p in PRICE_PER_MTOK.items()) / 1e6


def call(task: str, system: str, user: str, schema: dict, *, attempt: int = 0, use_cache: bool = True, store: bool = True) -> dict:
    """Return the parsed JSON object. use_cache=False forces a fresh call (stability re-runs); store=False keeps it out of the cache."""
    global _spent
    key = cache_key(task, system, user, schema, attempt)
    path = CACHE / task / f"{key}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["output"]
    with _lock:
        if _spent >= BUDGET_USD:
            raise LLMError(f"budget of ${BUDGET_USD:.2f} reached; raise SPARK_LLM_BUDGET_USD to continue")
    t0 = time.monotonic()
    for tries in range(3):  # a stream stalled mid-response is not retried by the SDK; retry it here
        try:
            resp = _stream(system, user, schema)
            break
        except Exception as e:  # rate limits / 5xx were already retried by the SDK; auth and bad requests won't heal
            if tries == 2 or isinstance(e, (anthropic.AuthenticationError, anthropic.BadRequestError,
                                            anthropic.RateLimitError, anthropic.InternalServerError)):
                raise _wrap(e, task, t0) from e
    usage = resp.usage.to_dict()
    usd = cost(usage)
    with _lock:
        _spent += usd
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"task": task, "key": key, "served_by": resp.model, "stop": resp.stop_reason, "usage": usage,
                                "usd": round(usd, 5), "seconds": round(time.monotonic() - t0, 1), "request_id": getattr(resp, "_request_id", None), "stored": store}) + "\n")
    if resp.stop_reason == "refusal":
        raise LLMError(f"refused ({task}): {getattr(resp.stop_details, 'category', None)}")
    if resp.stop_reason == "max_tokens":
        raise LLMError(f"hit max_tokens ({task})")
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if text is None:
        raise LLMError(f"no text block ({task})")
    output = json.loads(text)
    if store:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task": task, "model": MODEL, "served_by": resp.model, "effort": EFFORT, "output": output},
                                   indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return output


def _stream(system: str, user: str, schema: dict):
    with client().beta.messages.stream(  # streamed: long judge responses must not hit the HTTP timeout
        model=MODEL, max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        return stream.get_final_message()


def _wrap(e: Exception, task: str, t0: float) -> LLMError:
    if isinstance(e, anthropic.AuthenticationError):
        return LLMError("the API key was rejected (401); check spark/.env")
    if isinstance(e, anthropic.BadRequestError):
        return LLMError(f"bad request ({task}): {e.message}")
    if isinstance(e, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError, anthropic.APITimeoutError)):
        return LLMError(f"API unavailable after retries ({task}): {type(e).__name__}")
    return LLMError(f"transport error after {time.monotonic() - t0:.0f}s ({task}): {type(e).__name__}")  # e.g. httpx2.ReadTimeout mid-stream


def spent() -> float:
    return _spent
