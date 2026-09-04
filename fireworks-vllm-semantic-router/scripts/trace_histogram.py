#!/usr/bin/env python3
"""Print distributions over fixtures/traces JSONL.

Reports prompt tokens, cached fraction, user-message count, tool count,
and estimated context budget (prompt + thinking + max_tokens) vs
262K / 512K / 1M.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRACES = ROOT / "fixtures" / "traces" / "turns.jsonl"

WINDOWS = (
    ("kimi-262K", 262144),
    ("minimax-512K", 512000),
    ("glm-1M", 1048576),
)


def load_turns(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def user_message_count(messages: list) -> int:
    return sum(1 for m in messages if (m or {}).get("role") == "user")


def tool_count(turn: dict) -> int:
    tools = turn.get("tools") or []
    return len(tools)


def has_tool_loop(turn: dict) -> bool:
    for message in turn.get("messages") or []:
        if message.get("tool_calls") or message.get("role") == "tool":
            return True
    return False


def budget_of(turn: dict) -> int:
    usage = turn.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    thinking = int(usage.get("thinking_tokens") or 0)
    max_tokens = int(turn.get("max_tokens") or 0)
    return prompt + thinking + max_tokens


def cached_fraction(turn: dict) -> float | None:
    usage = turn.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int(usage.get("cached_tokens") or 0)
    if prompt <= 0:
        return None
    return cached / prompt


def summarize_nums(values: list[float]) -> str:
    if not values:
        return "n/a"
    values = sorted(values)
    p50 = statistics.median(values)
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return (
        f"n={len(values)} min={values[0]:.0f} p50={p50:.0f} "
        f"p90={p90:.0f} max={values[-1]:.0f} mean={statistics.mean(values):.0f}"
    )


def window_counts(budgets: list[int]) -> dict[str, int]:
    counts = {name: 0 for name, _ in WINDOWS}
    counts["over_1M"] = 0
    for b in budgets:
        placed = False
        for name, limit in WINDOWS:
            if b <= limit:
                counts[name] += 1
                placed = True
                break
        if not placed:
            counts["over_1M"] += 1
    return counts


def render(turns: list[dict]) -> str:
    sources = Counter(t.get("source") or "unknown" for t in turns)
    live = sources.get("live", 0)
    synthetic = sources.get("synthetic-shape", 0)
    prompts = [int((t.get("usage") or {}).get("prompt_tokens") or 0) for t in turns]
    cached_fracs = [f for f in (cached_fraction(t) for t in turns) if f is not None]
    users = [user_message_count(t.get("messages") or []) for t in turns]
    tools = [tool_count(t) for t in turns]
    budgets = [budget_of(t) for t in turns]
    has_tools = sum(1 for n in tools if n > 0)
    multi = sum(1 for n in users if n >= 2)
    loops = sum(1 for t in turns if has_tool_loop(t))
    agent_shaped = sum(
        1
        for t, n_tools, n_users in zip(turns, tools, users)
        if n_tools > 0 or n_users >= 2 or has_tool_loop(t)
    )
    windows = window_counts(budgets)
    over_kimi = sum(1 for b in budgets if b > 262144)

    lines = [
        "# Trace histogram",
        "",
        f"Turns: {len(turns)}. source={dict(sources)}.",
    ]
    if live == 0:
        lines += [
            "",
            "**synthetic-shape — not a Claude Code dump.** cached_tokens were not invented.",
            "Do not quote this file as live agent usage.",
        ]
    lines += [
        "",
        f"- prompt_tokens: {summarize_nums(prompts)}",
        f"- cached_fraction: "
        + (
            f"n={len(cached_fracs)} mean={statistics.mean(cached_fracs):.3f} "
            f"max={max(cached_fracs):.3f}"
            if cached_fracs
            else "n/a"
        ),
        f"- user_message_count: {summarize_nums([float(x) for x in users])}",
        f"- tool_count: {summarize_nums([float(x) for x in tools])}",
        f"- estimated context budget (prompt + thinking + max_tokens): {summarize_nums([float(x) for x in budgets])}",
        f"- rows with tools: {has_tools} / {len(turns)}",
        f"- rows with ≥2 user messages: {multi} / {len(turns)}",
        f"- rows with a tool loop: {loops} / {len(turns)}",
        f"- agent-shaped (tools or multi-turn or tool loop): {agent_shaped} / {len(turns)}",
        f"- budget over Kimi 262K: {over_kimi} / {len(turns)}",
        "",
        "Budget vs windows:",
        "",
        "| window | count |",
        "|---|---:|",
    ]
    for name, _ in WINDOWS:
        lines.append(f"| {name} | {windows[name]} |")
    lines.append(f"| over_1M | {windows['over_1M']} |")
    lines += [
        "",
        "Policy note from this shape: coding-agent rows are not short no-tool",
        "MiniMax turns. Context cutover is a budget against 262K, not pad-spam.",
        "Stay on the hot prefix — Kimi cached input is $0.19 vs $0.95.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traces", type=Path, default=DEFAULT_TRACES)
    parser.add_argument("--out-md", type=Path, default=ROOT / "docs" / "trace-histogram.md")
    args = parser.parse_args()

    turns = load_turns(args.traces)
    if not turns:
        text = (
            "# Trace histogram\n\n"
            "No turns in `fixtures/traces/`. Ingest a dump or seed from agent fixtures.\n"
        )
        print(text)
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(text)
        return 0
    text = render(turns)
    print(text)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text(text if text.endswith("\n") else text + "\n")
    print(f"wrote {args.out_md}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
