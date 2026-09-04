#!/usr/bin/env python3
"""Price trace JSONL with official Fireworks Standard input/cached/output.

Counterfactuals: always-Kimi, always-GLM, always-MiniMax, routed (that turn's
model + that turn's usage). Does not invent cache hits. Flags rows where
completion_tokens >= max_tokens as incomplete.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACES = ROOT / "fixtures" / "traces" / "turns.jsonl"

# Official Fireworks Standard list: input / cached input / output per 1M, 2026-09-03.
# https://docs.fireworks.ai/serverless/pricing
PRICES = {
    "minimax-m3": (0.30, 0.06, 1.20),
    "glm-5p2": (1.40, 0.14, 4.40),
    "kimi-k2p7-code": (0.95, 0.19, 4.00),
}
ALWAYS = {
    "kimi": "kimi-k2p7-code",
    "glm": "glm-5p2",
    "minimax": "minimax-m3",
}


def rates_for(model: str) -> tuple[float, float, float] | None:
    for key, rates in PRICES.items():
        if key in (model or ""):
            return rates
    return None


def cost_usd(model_key: str, prompt: int, cached: int, completion: int) -> float:
    pin, pcache, pout = PRICES[model_key]
    uncached = max(prompt - cached, 0)
    return (uncached * pin + cached * pcache + completion * pout) / 1_000_000


def routed_key(turn: dict) -> str | None:
    for candidate in (turn.get("routed_model"), turn.get("model")):
        rates = rates_for(candidate or "")
        if rates:
            for key in PRICES:
                if key in candidate:
                    return key
    return None


def load_turns(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def price_turn(turn: dict) -> dict:
    usage = turn.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(usage.get("cached_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    max_tokens = turn.get("max_tokens")
    capped = bool(max_tokens is not None and completion >= int(max_tokens) and completion > 0)
    rk = routed_key(turn)
    always = {name: cost_usd(key, prompt, cached, completion) for name, key in ALWAYS.items()}
    routed = cost_usd(rk, prompt, cached, completion) if rk else None
    live = (turn.get("source") or "") == "live"
    return {
        "id": turn.get("id"),
        "source": turn.get("source"),
        "model": turn.get("model"),
        "routed_model": turn.get("routed_model") or (rk if rk else None),
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "max_tokens": max_tokens,
        "capped": capped,
        "always_kimi_usd": always["kimi"],
        "always_glm_usd": always["glm"],
        "always_minimax_usd": always["minimax"],
        "routed_usd": routed,
        "live_usage": live,
        "invented_cache": False,
    }


def render_md(rows: list[dict], source: str) -> str:
    live = sum(1 for r in rows if r.get("live_usage"))
    synthetic = len(rows) - live
    capped = [r for r in rows if r.get("capped")]
    priced = [r for r in rows if r.get("routed_usd") is not None]
    totals = {
        "kimi": sum(r["always_kimi_usd"] for r in rows),
        "glm": sum(r["always_glm_usd"] for r in rows),
        "minimax": sum(r["always_minimax_usd"] for r in rows),
        "routed": sum(r["routed_usd"] for r in priced),
    }
    lines = [
        "# Trace cost (token-hold, not a savings rate)",
        "",
        f"Source: `{source}`. Official Standard input / cached / output as of 2026-09-03",
        "([pricing](https://docs.fireworks.ai/serverless/pricing)).",
        "Same `usage` on every counterfactual. Cache is whatever the turn recorded — never invented.",
        "",
    ]
    if live == 0:
        lines += [
            "**No live usage yet.** Seed traces are synthetic-shape (request structure,",
            "estimated prompt tokens, `cached_tokens = 0`, usually `completion_tokens = 0`).",
            "This table is a wiring check, not a bill.",
            "",
        ]
    else:
        lines += [f"Live turns: {live}. Synthetic-shape turns: {synthetic}.", ""]
    if not rows:
        lines += ["No traces to price.", ""]
        return "\n".join(lines)
    routed_total = "—" if not priced else f"{totals['routed']:.6f}"
    lines += [
        f"| rows | live | synthetic-shape | capped | always-Kimi USD | always-GLM USD | always-MiniMax USD | routed USD |",
        f"|---:|---:|---:|---:|---:|---:|---:|---:|",
        f"| {len(rows)} | {live} | {synthetic} | {len(capped)} | {totals['kimi']:.6f} | "
        f"{totals['glm']:.6f} | {totals['minimax']:.6f} | {routed_total} |",
        "",
        "| id | source | prompt | cached | completion | capped? | Kimi | GLM | MiniMax | routed |",
        "|---|---|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        routed = "" if r["routed_usd"] is None else f"{r['routed_usd']:.6f}"
        lines.append(
            f"| {r['id']} | {r['source']} | {r['prompt_tokens']} | {r['cached_tokens']} | "
            f"{r['completion_tokens']} | {'yes' if r['capped'] else ''} | "
            f"{r['always_kimi_usd']:.6f} | {r['always_glm_usd']:.6f} | "
            f"{r['always_minimax_usd']:.6f} | {routed} |"
        )
    lines += [
        "",
        "Capped rows (`completion_tokens >= max_tokens`) are incomplete work. Do not quote a %.",
        "Do not add a Fast latency column unless a real Fast call happened.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--out-json", type=Path, default=ROOT / "docs" / "trace-cost.json")
    parser.add_argument("--out-md", type=Path, default=ROOT / "docs" / "trace-cost.md")
    args = parser.parse_args()

    turns = load_turns(args.traces)
    rows = [price_turn(t) for t in turns]
    payload = {
        "pricing_source": "https://docs.fireworks.ai/serverless/pricing",
        "pricing_as_of": "2026-09-03",
        "pricing_shape": "input / cached_input / output per 1M",
        "source": str(args.traces),
        "note": (
            "Token-hold counterfactual on captured usage. "
            "No invented cached_tokens. Not a % cheaper claim."
        ),
        "rows": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2) + "\n")
    try:
        source_label = str(args.traces.resolve().relative_to(ROOT))
    except ValueError:
        source_label = str(args.traces)
    md = render_md(rows, source_label)
    args.out_md.write_text(md if md.endswith("\n") else md + "\n")
    print(md)
    print(f"wrote {args.out_json}", file=sys.stderr)
    print(f"wrote {args.out_md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
