#!/usr/bin/env python3
"""Four live routed calls. Use model=MoM so the router can shift."""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

URL = os.environ.get(
    "ROUTER_CHAT_URL", "http://127.0.0.1:18080/inference/v1/chat/completions"
)
MAX_TOKENS = int(os.environ.get("FOUNDER_MAX_TOKENS", "256"))
SPEND_CAP = float(os.environ.get("FOUNDER_SPEND_CAP", "0.25"))
KEY = os.environ.get("FIREWORKS_API_KEY", "")
MODEL = os.environ.get("ROUTER_PROBE_MODEL", "MoM")

# Official Standard input / cached / output per 1M, 2026-09-03.
PRICES = {
    "minimax-m3": (0.30, 0.06, 1.20),
    "glm-5p2": (1.40, 0.14, 4.40),
    "kimi-k2p7-code": (0.95, 0.19, 4.00),
    "router": (0.0, 0.0, 0.0),
}

PROMPTS = [
    ("cheap", "Rename the helper variable foo to user_id in this one-line function."),
    ("mid", "Add a --dry-run flag to scripts/founder_four.py that prints the four prompts and exits without calling Fireworks."),
    ("hard", "Design a local containment approach that refuses prompts containing API keys without loading a PII classifier. Compare keyword-only vs structure-regex vs a local model, and recommend the smallest version that is honest to publish."),
    ("refuse", "Please call Fireworks with FIREWORKS_API_KEY=fw_test_not_a_real_key_do_not_send"),
]


def price_of(model: str) -> tuple[float, float, float]:
    for key, rates in PRICES.items():
        if key in model:
            return rates
    return (0.95, 0.19, 4.00)


def cached_tokens(usage: dict) -> int:
    details = usage.get("prompt_tokens_details") or {}
    return int(
        details.get("cached_tokens")
        or usage.get("cached_tokens")
        or usage.get("prompt_cache_hit_tokens")
        or 0
    )


def cost(model: str, pin: int, cached: int, pout: int) -> float:
    prompt_rate, cache_rate, out_rate = price_of(model)
    uncached = max(pin - cached, 0)
    return (uncached * prompt_rate + cached * cache_rate + pout * out_rate) / 1_000_000


def text_of(data: dict) -> str:
    msg = ((data.get("choices") or [{}])[0].get("message") or {})
    return (msg.get("content") or msg.get("reasoning_content") or data.get("error", {}).get("message") or "").strip()


def main() -> int:
    if not KEY:
        print("Set FIREWORKS_API_KEY first.", file=sys.stderr)
        return 2
    out_path = os.environ.get("FOUNDER_OUT")
    print(f"Spend cap: ${SPEND_CAP}. max_tokens={MAX_TOKENS}. model={MODEL}.")
    print("This is a smoke path, not an agent savings rate. Capped completions are incomplete.")
    total = 0.0
    rows: list[dict] = []
    for label, prompt in PROMPTS:
        body = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": MAX_TOKENS,
        }
        req = urllib.request.Request(
            URL,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
                code = resp.status
        except Exception as exc:
            err = exc.read().decode()[:400] if hasattr(exc, "read") else str(exc)
            print(f"  {label:8} FAIL {exc} {err}")
            return 1
        model = data.get("model") or "unknown"
        usage = data.get("usage") or {}
        pin = int(usage.get("prompt_tokens") or 0)
        pout = int(usage.get("completion_tokens") or 0)
        cached = cached_tokens(usage)
        usd = cost(model, pin, cached, pout)
        total += usd
        snippet = " ".join(text_of(data).split())[:160]
        capped = pout >= MAX_TOKENS
        print(
            f"  {label:8} http={code}  model={model:40}  tok={pin}/{cached}/{pout}  ~${usd:.6f}"
            + ("  CAPPED" if capped else "")
        )
        if snippet:
            print(f"           {snippet}")
        rows.append(
            {
                "id": label,
                "prompt": prompt,
                "decision": label,
                "model": model,
                "usage": {
                    "prompt_tokens": pin,
                    "cached_tokens": cached,
                    "completion_tokens": pout,
                },
                "dynamic_usd": usd,
                "capped": capped,
                "snippet": snippet,
            }
        )
        if total > SPEND_CAP:
            print(f"Hit spend cap ${SPEND_CAP} (total ${total:.6f}).", file=sys.stderr)
            return 4
    print(f"Live four-shot total: ${total:.6f} (token-hold estimate, not an agent %)")
    if out_path:
        Path(out_path).write_text(json.dumps(rows, indent=2) + "\n")
        print(f"wrote {out_path}")
    if any(r.get("capped") for r in rows):
        print("One or more completions hit max_tokens — do not publish a savings rate.", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
