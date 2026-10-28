"""Minimal Meta Model API client (Responses API, streamed, structured output) with a spend guard.

Auth: MODEL_API_KEY, or a proxy that injects the key for api.meta.ai (cloud environments).

Every call is appended to a spend log (JSON lines: timestamp, script, model, tokens, cost): $SPEND_LOG if set, else
experiments/spend.jsonl in the repo. The server points SPEND_LOG at its volume and derives its daily cap from it.
"""
import base64
import datetime
import json
import os
import sys
import threading
import time

import requests

API = "https://api.meta.ai/v1/responses"
DEFAULT_MODEL = "muse-spark-1.3-contributor"
# USD per 1M tokens (input, cached input, output), dev.meta.ai pricing, Oct 2026
PRICES = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
}


_REPO_LOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "experiments", "spend.jsonl")
_log_lock = threading.Lock()


def spend_log_path():
    if os.environ.get("SPEND_LOG"):
        return os.environ["SPEND_LOG"]
    return _REPO_LOG if os.path.isdir(os.path.dirname(_REPO_LOG)) else None


def log_spend(model, usage, usd, name, tag=None):
    path = spend_log_path()
    if not path:
        return
    rec = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "script": os.environ.get("SPEND_SCRIPT") or os.path.basename(sys.argv[0] or "python"),
           "call": name, "model": model, "input_tokens": usage.get("input_tokens", 0),
           "output_tokens": usage.get("output_tokens", 0), "cost_usd": round(usd, 6)}
    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    if reasoning is not None:
        rec["reasoning_tokens"] = reasoning
    if tag:
        rec["tag"] = tag
    with _log_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")


def spent_since(path, since_iso):
    """Total logged spend at or after an ISO timestamp (e.g. today 00:00 UTC)."""
    total = 0.0
    if path and os.path.exists(path):
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("ts", "") >= since_iso:
                    total += rec.get("cost_usd", 0)
    return total


class ModelError(RuntimeError):
    pass


class BudgetExceeded(ModelError):
    pass


class Budget:
    """Shared spend counter; refuses new calls once `limit_usd` is reached."""

    def __init__(self, limit_usd):
        self.limit_usd, self.spent = limit_usd, 0.0

    def check(self):
        if self.spent >= self.limit_usd:
            raise BudgetExceeded(f"budget ${self.limit_usd:.2f} used up (spent ${self.spent:.4f})")

    def charge(self, usd):
        self.spent += usd


def cost(model, usage):
    p_in, p_cached, p_out = PRICES.get(model, PRICES["muse-spark-1.3"])
    inp = usage.get("input_tokens", 0)
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    return ((inp - cached) * p_in + cached * p_cached + usage.get("output_tokens", 0) * p_out) / 1e6


def text(s):
    return {"type": "input_text", "text": s}


def image(path_or_bytes, mime=None):
    if isinstance(path_or_bytes, (bytes, bytearray)):
        data, mime = bytes(path_or_bytes), mime or "image/png"
    else:
        with open(path_or_bytes, "rb") as f:
            data = f.read()
        mime = mime or ("image/jpeg" if str(path_or_bytes).lower().endswith((".jpg", ".jpeg")) else "image/png")
    return {"type": "input_image", "image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}


def read_stream(lines):
    """Parse a Responses API SSE stream into the final response object."""
    final, deltas, event = None, [], None
    for raw in lines:
        line = raw.decode() if isinstance(raw, bytes) else raw
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            d = json.loads(payload)
            kind = d.get("type") or event
            if kind == "response.output_text.delta":
                deltas.append(d.get("delta", ""))
            elif kind in ("response.completed", "response.incomplete"):
                final = d.get("response", d)
            elif kind in ("response.failed", "error"):
                raise ModelError(f"stream error: {json.dumps(d)[:500]}")
    if final is None:
        if not deltas:
            raise ModelError("stream ended without a response")
        final = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "".join(deltas)}]}]}
    return final


class Client:
    def __init__(self, model=DEFAULT_MODEL, effort="medium", budget=None):
        self.model, self.effort = model, effort
        self.budget = budget or Budget(float("inf"))

    def respond(self, instructions, content, schema, name="Output", retries=2, max_output_tokens=24000,
                model=None, effort=None):
        """One streamed call with structured output. Returns (parsed JSON, info {usage, cost_usd, seconds}).
        `model` / `effort` override the client's defaults for this call (Talos uses different settings per role)."""
        self.budget.check()
        model, effort = model or self.model, effort or self.effort
        # cap output: one critic call once ran away to 1.5 MB of broken JSON
        body = {"model": model, "reasoning": {"effort": effort}, "instructions": instructions,
                "max_output_tokens": max_output_tokens,
                "stream": True, "input": [{"type": "message", "role": "user", "content": content}],
                "text": {"format": {"type": "json_schema", "name": name, "schema": schema}}}
        key = os.environ.get("MODEL_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        last = None
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                r = requests.post(API, headers=headers, json=body, timeout=(15, 300), stream=True)  # reasoning can go quiet for a while
                if r.status_code == 200:
                    data = read_stream(r.iter_lines())
                    break
                last = f"HTTP {r.status_code}: {r.text[:500]}"
                if r.status_code < 500 and r.status_code != 429:
                    raise ModelError(last)
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
            time.sleep(4 * (attempt + 1))
        else:
            raise ModelError(last)
        usage = data.get("usage", {})
        usd = cost(model, usage)
        self.budget.charge(usd)
        log_spend(model, usage, usd, name, getattr(self, "tag", None))
        info = {"usage": usage, "cost_usd": usd, "seconds": round(time.time() - t0, 1), "model": model,
                "effort": effort}
        out = next((c["text"] for item in data.get("output", []) if item.get("type") == "message"
                    for c in item.get("content", []) if c.get("type") == "output_text"), None)
        if out is None:
            raise ModelError("response has no output text")
        try:
            return json.loads(out), info
        except json.JSONDecodeError as e:
            raise ModelError(f"output is not JSON ({e}): {out[:300]}") from None
