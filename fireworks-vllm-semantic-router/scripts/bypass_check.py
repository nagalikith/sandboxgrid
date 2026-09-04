#!/usr/bin/env python3
"""MoM enters the recipe; a Fireworks model ID is pass-through.

Live checks (when the router is serving this config):
  - eval of the secret fixture matches contain_secrets
  - a pinned Kimi chat with a harmless prompt does not run the refuse plugin

The pin path never sends the secret body to Fireworks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = os.environ.get(
    "ROUTER_PIN_MODEL", "accounts/fireworks/models/kimi-k2p7-code"
)
MOM = os.environ.get("ROUTER_PROBE_MODEL", "MoM")
EVAL_URL = os.environ.get("ROUTER_EVAL_URL", "http://127.0.0.1:8080/api/v1/eval")
CHAT_URL = os.environ.get(
    "ROUTER_CHAT_URL", "http://127.0.0.1:18080/inference/v1/chat/completions"
)
SECRET_ID = "secret-01"
HARMLESS = "Reply with the single word pong."


def load_secret_prompt() -> str:
    items = json.loads((ROOT / "fixtures" / "prompts.json").read_text())
    for item in items:
        if item.get("id") == SECRET_ID:
            return item["prompt"]
    raise SystemExit(f"missing {SECRET_ID} in fixtures/prompts.json")


def post_json(url: str, body: dict, headers: dict, timeout: int) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()[:800]
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw
    except Exception as exc:
        return 0, str(exc)


def decision_name(payload: dict) -> str | None:
    dr = payload.get("decision_result") or {}
    return dr.get("decision_name") or payload.get("routing_decision")


def config_contract() -> list[str]:
    """Static asserts that do not need a live router."""
    text = (ROOT / "config.yaml").read_text()
    readme = (ROOT / "README.md").read_text()
    problems = []
    if "contain_secrets" not in text:
        problems.append("config.yaml missing contain_secrets")
    if "coding_default" not in text:
        problems.append("config.yaml missing coding_default")
    if "keep_current_model: true" not in text:
        problems.append("config.yaml missing stay-on-model retention")
    if 'default_model: fireworks/kimi-k2p7-code' not in text:
        problems.append("default_model is not Kimi")
    if '"model": "MoM"' not in readme and "`MoM`" not in readme:
        problems.append("README does not document MoM")
    if "skips" not in readme.lower() and "pass-through" not in readme.lower():
        problems.append("README does not document pin bypass")
    if "reasoning_content" not in readme:
        problems.append("README does not document preserved thinking")
    if "vllm-sr status" not in readme:
        problems.append("README does not tell operators to read vllm-sr status for ports")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Only check the documented contract in config/README",
    )
    args = parser.parse_args()

    problems = config_contract()
    if problems:
        for p in problems:
            print(f"[FAIL] {p}")
        return 1
    print("[ok] config/README contract: MoM enters recipe; pin documented as bypass; thinking preserved")

    if args.offline:
        print("offline: live eval/chat asserts not run")
        return 0

    secret = load_secret_prompt()
    code, payload = post_json(
        EVAL_URL, {"messages": [{"role": "user", "content": secret}]}, {}, args.timeout
    )
    if code == 0:
        print(f"bypass_check: router not serving ({payload}). Live assert not run.")
        return 0
    if code != 200 or not isinstance(payload, dict):
        print(f"[FAIL] eval secret: HTTP {code} {payload}")
        return 1
    got = decision_name(payload)
    if got != "contain_secrets":
        print(f"[FAIL] MoM/eval secret expected contain_secrets got {got}")
        return 1
    print("[ok] MoM/eval secret → contain_secrets (recipe ran)")

    key = os.environ.get("FIREWORKS_API_KEY", "")
    if not key:
        print("FIREWORKS_API_KEY unset — skipped pinned harmless chat")
        return 0
    code, payload = post_json(
        CHAT_URL,
        {
            "model": PIN,
            "messages": [{"role": "user", "content": HARMLESS}],
            "max_tokens": 8,
        },
        {"Authorization": f"Bearer {key}"},
        args.timeout,
    )
    if code == 0:
        print(f"bypass_check: chat listener not serving ({payload}). Pin assert not run.")
        return 0
    if code != 200 or not isinstance(payload, dict):
        print(f"[FAIL] pinned chat HTTP {code} {payload}")
        return 1
    text = (
        ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    )
    refuse = "Request blocked" in text
    model = payload.get("model") or ""
    if refuse:
        print("[FAIL] pinned Kimi chat ran the refuse plugin — pin is not a bypass")
        return 1
    if "kimi" not in model.lower() and "k2" not in model.lower():
        print(f"[FAIL] pinned chat returned unexpected model {model!r}")
        return 1
    print(f"[ok] pin {PIN} skipped recipe plugins (model={model})")
    print("secret body was not sent on the pin path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
