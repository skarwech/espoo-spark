"""Cached LLM calls with JSON-schema output (docs/method.md §6, §7).

Claude (Anthropic API, schema-constrained decoding) for the registered engine v1; DeepSeek and Kimi on Verda Inference
(OpenAI-compatible, schema appended to the prompt and every answer validated, one corrective retry) for engine d1 and the step-3
conversations. Every response is stored under data/llm_cache/<task>/<sha256>.json, keyed by (model, effort, system, user, schema,
attempt); demo runs replay the cache. Uncached calls are appended to the ledger with their list-price cost. A process stops once
SPARK_LLM_BUDGET_USD (Anthropic, default 25) is spent; Verda calls stop once the ledger's cumulative Verda spend reaches
SPARK_VERDA_BUDGET_USD (default 20, method.md §7).
"""
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

import anthropic
import httpx
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "llm_cache"
LEDGER = CACHE / "_ledger.jsonl"
SUBJECT_INDEX = CACHE / "_subjects.json"
MODEL = "claude-opus-5-5"
EFFORT = "medium"
API_URL = "https://api.anthropic.com"  # pinned: never inherit ANTHROPIC_BASE_URL from the host shell
PRICE_PER_MTOK = {"input_tokens": 4.0, "output_tokens": 20.0, "cache_read_input_tokens": 0.20, "cache_creation_input_tokens": 5.0}
BUDGET_USD = float(os.environ.get("SPARK_LLM_BUDGET_USD", "25"))
# Amendment A1 (method.md) was not completed; it is opt-in (SPARK_AMENDMENT=A1). The default engine is the registered run v1.
A1 = os.environ.get("SPARK_AMENDMENT", "") == "A1"
# Engine profile: v1 = registered Claude run (default, replays from cache); d1 = DeepSeek V4.1 Flash; k1 = Kimi K3.1 Pro (both on
# Verda, method.md §7). k1 is the step-3 engine chosen by the project owner after d1 results.
ENGINE = os.environ.get("SPARK_ENGINE", "v1")
DEEPSEEK, KIMI = "deepseek-4-1-flash-1017", "kimi-k3-1-pro"
ENGINE_MODEL = {"v1": MODEL, "d1": DEEPSEEK, "k1": KIMI}[ENGINE]
VERDA_URL = "https://inference.verda.com/v1"  # pinned (Verda docs); a configured base URL must name this host or nothing is sent
VERDA_PRICE = {DEEPSEEK: (0.15, 0.60), KIMI: (3.00, 15.00)}  # USD per M input / output tokens, verda.com list prices 2026-11-14
VERDA_EFFORT = {DEEPSEEK: "default", KIMI: "low"}  # Kimi reasons at length; low effort keeps the conversations within budget
VERDA_BUDGET_USD = float(os.environ.get("SPARK_VERDA_BUDGET_USD", "20"))

_lock = threading.Lock()
_spent = 0.0
_client = None
_verda_spent = None  # read from the ledger on first use, then accumulated


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


def cache_key(task: str, system: str, user: str, schema: dict, attempt: int, model: str = MODEL,
              subject_id: str | None = None) -> str:
    effort = EFFORT if model == MODEL else VERDA_EFFORT[model]
    request = {"model": model, "effort": effort, "task": task, "system": system, "user": user, "schema": schema, "attempt": attempt}
    if subject_id is not None:
        request["subject_id"] = subject_id
    blob = json.dumps(request, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _register_subject(subject_id: str, task: str, key: str) -> None:
    with _lock:
        index = json.loads(SUBJECT_INDEX.read_text(encoding="utf-8")) if SUBJECT_INDEX.exists() else {}
        entry = {"task": task, "key": key}
        refs = index.setdefault(subject_id, [])
        if entry not in refs:
            refs.append(entry)
        SUBJECT_INDEX.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def purge_subject(subject_id: str) -> int:
    """Remove cached model outputs explicitly indexed to a submitted story id."""
    if not re.fullmatch(r"S-[0-9a-f]{32}", subject_id):
        raise ValueError("invalid story id")
    with _lock:
        index = json.loads(SUBJECT_INDEX.read_text(encoding="utf-8")) if SUBJECT_INDEX.exists() else {}
        entries = index.pop(subject_id, [])
        removed = 0
        for entry in entries:
            path = CACHE / entry["task"] / f"{entry['key']}.json"
            if path.exists():
                path.unlink()
                removed += 1
        if index:
            SUBJECT_INDEX.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        elif SUBJECT_INDEX.exists():
            SUBJECT_INDEX.unlink()
    return removed


def cost(usage: dict) -> float:
    return sum((usage.get(k) or 0) * p for k, p in PRICE_PER_MTOK.items()) / 1e6


def call(task: str, system: str, user: str, schema: dict, *, attempt: int = 0, use_cache: bool = True, store: bool = True,
         model: str | None = None, subject_id: str | None = None) -> dict:
    """Return the parsed JSON object. use_cache=False forces a fresh call (stability re-runs); store=False keeps it out of the cache.
    model defaults to the engine profile's model; a non-Claude model goes to Verda."""
    global _spent
    model = model or ENGINE_MODEL
    key = cache_key(task, system, user, schema, attempt, model, subject_id)
    path = CACHE / task / f"{key}.json"
    if use_cache and path.exists():
        if subject_id:
            _register_subject(subject_id, task, key)
        return json.loads(path.read_text(encoding="utf-8"))["output"]
    if model != MODEL:
        output = _verda_call(task, system, user, schema, model, key, path, store)
        if store and subject_id:
            _register_subject(subject_id, task, key)
        return output
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
        if subject_id:
            _register_subject(subject_id, task, key)
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


def _setting(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        k, _, v = line.strip().partition("=")
        if k.strip() == name:
            return v.strip().strip('"').strip("'")
    return ""


def _verda_key(model: str) -> str:
    family = "KIMI" if model.startswith("kimi") else "DEEPSEEK"
    key = _setting(f"VERDA_INFERENCE_API_KEY_{family}") or _setting("VERDA_INFERENCE_API_KEY")
    if not key:
        raise LLMError(f"No Verda key: add VERDA_INFERENCE_API_KEY_{family}=... to spark/.env. Cached outputs still replay.")
    custom = _setting("VERDA_INFERENCE_BASE_URL").rstrip("/")
    if custom and custom not in (VERDA_URL, VERDA_URL.removesuffix("/v1")):
        raise LLMError("VERDA_INFERENCE_BASE_URL does not name Verda's documented host; nothing was sent.")
    return key


def verda_spent() -> float:
    """Cumulative Verda spend from the ledger (all processes)."""
    global _verda_spent
    with _lock:
        if _verda_spent is None:
            _verda_spent = sum(json.loads(l).get("usd", 0) for l in LEDGER.read_text(encoding="utf-8").splitlines()
                               if l and json.loads(l).get("provider") == "verda") if LEDGER.exists() else 0.0
        return _verda_spent


def _parse(text: str, schema: dict) -> tuple[dict | None, str]:
    """(object, "") when text holds JSON valid for schema, else (None, why)."""
    t = (text or "").strip()
    if "{" not in t:
        return None, "no JSON object in the answer"
    try:
        obj = json.loads(t[t.index("{"):t.rindex("}") + 1])
        jsonschema.validate(obj, schema)
        return obj, ""
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e.msg}"
    except jsonschema.ValidationError as e:
        return None, f"schema violation: {e.message[:300]}"


def _verda_request(model: str, messages: list[dict], task: str) -> dict:
    body = {"model": model, "messages": messages, "max_tokens": 16384, "response_format": {"type": "json_object"}}
    if VERDA_EFFORT[model] != "default":
        body["reasoning_effort"] = VERDA_EFFORT[model]
    with httpx.Client(base_url=VERDA_URL, headers={"Authorization": f"Bearer {_verda_key(model)}"},
                      timeout=httpx.Timeout(600.0, connect=30.0)) as c:
        for tries in range(4):
            try:
                r = c.post("/chat/completions", json=body)
                if r.status_code == 400 and "response_format" in body:  # JSON mode not offered: rely on the prompt and validation
                    body.pop("response_format")
                    continue
                if r.status_code in (401, 403):
                    raise LLMError(f"the Verda key was rejected ({r.status_code})")
                if r.status_code == 400:
                    raise LLMError(f"bad request ({task}): {r.text[:300]}")
                r.raise_for_status()
                return r.json()
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
                if tries == 3:
                    raise LLMError(f"Verda unavailable after retries ({task}): {type(e).__name__}") from e
                time.sleep(4 * (tries + 1))
    raise LLMError(f"Verda request failed ({task})")


def _verda_call(task: str, system: str, user: str, schema: dict, model: str, key: str, path: Path, store: bool) -> dict:
    """One answer, validated against the schema; one corrective retry (method.md §7)."""
    global _verda_spent
    if verda_spent() >= VERDA_BUDGET_USD:
        raise LLMError(f"Verda budget of ${VERDA_BUDGET_USD:.2f} reached; raise SPARK_VERDA_BUDGET_USD to continue")
    messages = [{"role": "system", "content": f"{system}\n\nReturn only a JSON object that conforms to this JSON Schema:\n"
                                              f"{json.dumps(schema, ensure_ascii=False)}"},
                {"role": "user", "content": user}]
    for retry in (0, 1):
        t0 = time.monotonic()
        resp = _verda_request(model, messages, task)
        choice, usage = resp["choices"][0], resp.get("usage", {})
        usd = (usage.get("prompt_tokens", 0) * VERDA_PRICE[model][0] + usage.get("completion_tokens", 0) * VERDA_PRICE[model][1]) / 1e6
        text = choice["message"].get("content") or ""
        output, why = _parse(text, schema)
        with _lock:
            _verda_spent = (_verda_spent or 0.0) + usd
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"task": task, "key": key, "provider": "verda", "served_by": resp.get("model", model),
                                    "stop": choice.get("finish_reason"), "usage": usage, "usd": round(usd, 6),
                                    "seconds": round(time.monotonic() - t0, 1), "retry": retry, "valid": output is not None,
                                    "stored": store}) + "\n")
        if output is not None:
            break
        messages += [{"role": "assistant", "content": text[:20000]},
                     {"role": "user", "content": f"That answer was not usable ({why}). Return only the corrected JSON object."}]
    if output is None:
        raise LLMError(f"no schema-valid answer after one retry ({task}): {why}")
    if store:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"task": task, "model": model, "served_by": resp.get("model", model), "effort": VERDA_EFFORT[model],
                                    "provider": "verda", "output": output}, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
    return output


def spent() -> float:
    return _spent + (_verda_spent or 0.0)
