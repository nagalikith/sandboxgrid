#!/usr/bin/env python3
"""Build a token-hold cost table from router_replay (or a saved dump)."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Official Fireworks Standard list: input / cached input / output per 1M, 2026-09-03.
# https://docs.fireworks.ai/serverless/pricing
PRICES = {
    "fireworks/minimax-m3": (0.30, 0.06, 1.20),
    "accounts/fireworks/models/minimax-m3": (0.30, 0.06, 1.20),
    "fireworks/glm-5p2": (1.40, 0.14, 4.40),
    "accounts/fireworks/models/glm-5p2": (1.40, 0.14, 4.40),
    "fireworks/kimi-k2p7-code": (0.95, 0.19, 4.00),
    "accounts/fireworks/models/kimi-k2p7-code": (0.95, 0.19, 4.00),
}
STRONG_MODEL = "fireworks/kimi-k2p7-code"
LONG_MODEL = "fireworks/glm-5p2"


def cost_usd(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    if model not in PRICES:
        raise KeyError(f"no official price for {model!r}; add it to PRICES")
    pin, pcache, pout = PRICES[model]
    uncached = max(prompt_tokens - cached_tokens, 0)
    return (uncached * pin + cached_tokens * pcache + completion_tokens * pout) / 1_000_000


def load_records(source: str) -> list[dict]:
    if source.startswith("http://") or source.startswith("https://"):
        with urllib.request.urlopen(source) as resp:
            payload = json.loads(resp.read().decode())
    else:
        payload = json.loads(Path(source).read_text())
    if isinstance(payload, list):
        return payload
    for key in ("items", "data", "records", "results"):
        if isinstance(payload.get(key), list):
            return payload[key]
    raise ValueError("could not find a record list in replay payload")


def cached_of(usage: dict, record: dict) -> int:
    details = usage.get("prompt_tokens_details") or {}
    return int(
        details.get("cached_tokens")
        or usage.get("cached_tokens")
        or usage.get("prompt_cache_hit_tokens")
        or record.get("cached_tokens")
        or 0
    )


def extract_usage(record: dict) -> tuple[str, int, int, int, str | None]:
    model = (
        record.get("model")
        or record.get("selected_model")
        or (record.get("model_ref") or {}).get("model")
        or ""
    )
    usage = record.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or record.get("prompt_tokens") or 0)
    completion_tokens = int(
        usage.get("completion_tokens") or record.get("completion_tokens") or 0
    )
    decision = record.get("decision") or record.get("decision_name")
    return model, prompt_tokens, cached_of(usage, record), completion_tokens, decision


def bar_svg(dynamic: float, always_strong: float, always_long: float, out: Path) -> None:
    width, height = 720, 320
    pad_l, pad_r, pad_t, pad_b = 200, 90, 44, 56
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    peak = max(dynamic, always_strong, always_long, 1e-9)

    def x_for(v: float) -> float:
        return pad_l + plot_w * (v / peak)

    bars = [
        ("Dynamic routing", dynamic, "#2f6fed"),
        (f"Always {STRONG_MODEL.split('/')[-1]}", always_strong, "#c23b22"),
        (f"Always {LONG_MODEL.split('/')[-1]}", always_long, "#b8860b"),
    ]
    row_h = plot_h / 3
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<text x="20" y="28" font-family="sans-serif" font-size="16">Token-hold counterfactual (not agent savings)</text>',
    ]
    for i, (label, value, color) in enumerate(bars):
        y = pad_t + i * row_h + 12
        w = max(2.0, x_for(value) - pad_l)
        parts.append(
            f'<text x="16" y="{y + 16}" font-family="sans-serif" font-size="12">{label}</text>'
        )
        parts.append(f'<rect x="{pad_l}" y="{y}" width="{w:.1f}" height="22" fill="{color}"/>')
        parts.append(
            f'<text x="{pad_l + w + 8:.1f}" y="{y + 16}" font-family="sans-serif" font-size="12">${value:.6f}</text>'
        )
    note = (
        "Same token counts on every model, including cached input. "
        "Not what another model would have generated."
    )
    parts.append(
        f'<text x="20" y="{height - 18}" font-family="sans-serif" font-size="11">{note}</text>'
    )
    parts.append("</svg>")
    out.write_text("\n".join(parts) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default="http://127.0.0.1:8080/v1/router_replay?limit=100",
        help="Replay URL or a saved JSON dump",
    )
    parser.add_argument("--out-json", type=Path, default=ROOT / "docs" / "cost-comparison.json")
    parser.add_argument("--out-md", type=Path, default=ROOT / "docs" / "cost-comparison.md")
    parser.add_argument("--out-svg", type=Path, default=ROOT / "docs" / "cost-comparison.svg")
    args = parser.parse_args()

    records = load_records(args.source)
    rows = []
    dynamic_total = 0.0
    strong_total = 0.0
    long_total = 0.0
    skipped = 0
    for rec in records:
        model, pin, cached, pout, decision = extract_usage(rec)
        if not model or (pin == 0 and pout == 0):
            skipped += 1
            continue
        try:
            dyn = cost_usd(model, pin, cached, pout)
            always_strong = cost_usd(STRONG_MODEL, pin, cached, pout)
            always_long = cost_usd(LONG_MODEL, pin, cached, pout)
        except KeyError as exc:
            skipped += 1
            print(f"skip: {exc}", file=sys.stderr)
            continue
        dynamic_total += dyn
        strong_total += always_strong
        long_total += always_long
        rows.append(
            {
                "decision": decision,
                "model": model,
                "prompt_tokens": pin,
                "cached_tokens": cached,
                "completion_tokens": pout,
                "dynamic_usd": dyn,
                "always_strong_usd": always_strong,
                "always_long_usd": always_long,
            }
        )

    def pct(part: float, whole: float) -> float | None:
        return 100.0 * part / whole if whole else None

    summary = {
        "pricing_source": "https://docs.fireworks.ai/serverless/pricing",
        "pricing_as_of": "2026-09-03",
        "pricing_shape": "input / cached_input / output per 1M",
        "strong_model": STRONG_MODEL,
        "long_context_model": LONG_MODEL,
        "n_records": len(rows),
        "n_skipped": skipped,
        "dynamic_usd": dynamic_total,
        "always_strong_usd": strong_total,
        "always_long_usd": long_total,
        "vs_always_strong_usd": strong_total - dynamic_total,
        "vs_always_strong_pct": pct(strong_total - dynamic_total, strong_total),
        "vs_always_long_usd": long_total - dynamic_total,
        "vs_always_long_pct": pct(long_total - dynamic_total, long_total),
        "note": (
            "Token-hold counterfactual: same prompt/cached/completion counts on every model. "
            "Not what the other model would have generated. "
            "Capped thinking dumps are incomplete work — do not quote a % from them."
        ),
        "rows": rows,
    }

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2) + "\n")

    md = [
        "# Token-hold cost (not an agent savings rate)",
        "",
        f"Source: `{args.source}`. Same token counts on every model, including cached input.",
        "This is not what another model would have generated.",
        "The secret-refuse call is $0 and is omitted here (no tokens).",
        "",
        f"Pricing: official Fireworks Standard input / cached / output as of {summary['pricing_as_of']} "
        f"([docs]({summary['pricing_source']})).",
        f"Baselines: always `{STRONG_MODEL}` and always `{LONG_MODEL}` (1M-context list).",
        "",
        f"| records | dynamic USD | always-Kimi USD | vs Kimi | always-GLM USD | vs GLM |",
        f"|---|---:|---:|---:|---:|---:|",
        f"| {summary['n_records']} | {dynamic_total:.6f} | {strong_total:.6f} | "
        f"{summary['vs_always_strong_usd']:.6f} ({(summary['vs_always_strong_pct'] or 0):.1f}%) | "
        f"{long_total:.6f} | {summary['vs_always_long_usd']:.6f} ({(summary['vs_always_long_pct'] or 0):.1f}%) |",
        "",
        "| decision | model | prompt | cached | completion | dynamic USD | always-Kimi USD | always-GLM USD |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        md.append(
            f"| {row['decision'] or ''} | `{row['model']}` | {row['prompt_tokens']} | "
            f"{row['cached_tokens']} | {row['completion_tokens']} | {row['dynamic_usd']:.6f} | "
            f"{row['always_strong_usd']:.6f} | {row['always_long_usd']:.6f} |"
        )
    args.out_md.write_text("\n".join(md) + "\n")
    bar_svg(dynamic_total, strong_total, long_total, args.out_svg)
    print(f"wrote {args.out_json}")
    print(f"wrote {args.out_md}")
    print(f"wrote {args.out_svg}")
    if not rows:
        print("no priced records — chart is empty until a live replay dump exists", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
