"""Minimal Meta Model API client (Responses API, streamed, structured output) with a spend guard.

Auth: MODEL_API_KEY, or a proxy that injects the key for api.meta.ai (cloud environments).
"""
import base64
import json
import os
import time

import requests

API = "https://api.meta.ai/v1/responses"
DEFAULT_MODEL = "muse-spark-1.3-contributor"
# USD per 1M tokens (input, cached input, output), dev.meta.ai pricing, Oct 2026
PRICES = {
    "muse-spark-1.3": (1.25, 0.15, 4.25),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
}


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

    def respond(self, instructions, content, schema, name="Output", retries=2):
        """One streamed call with structured output. Returns (parsed JSON, info {usage, cost_usd, seconds})."""
        self.budget.check()
        body = {"model": self.model, "reasoning": {"effort": self.effort}, "instructions": instructions,
                "stream": True, "input": [{"type": "message", "role": "user", "content": content}],
                "text": {"format": {"type": "json_schema", "name": name, "schema": schema}}}
        key = os.environ.get("MODEL_API_KEY")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        last = None
        for attempt in range(retries + 1):
            t0 = time.time()
            try:
                r = requests.post(API, headers=headers, json=body, timeout=(15, 120), stream=True)
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
        usd = cost(self.model, usage)
        self.budget.charge(usd)
        info = {"usage": usage, "cost_usd": usd, "seconds": round(time.time() - t0, 1), "model": self.model}
        out = next((c["text"] for item in data.get("output", []) if item.get("type") == "message"
                    for c in item.get("content", []) if c.get("type") == "output_text"), None)
        if out is None:
            raise ModelError("response has no output text")
        try:
            return json.loads(out), info
        except json.JSONDecodeError as e:
            raise ModelError(f"output is not JSON ({e}): {out[:300]}") from None
