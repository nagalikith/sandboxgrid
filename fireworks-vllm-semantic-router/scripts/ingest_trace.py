#!/usr/bin/env python3
"""Normalize saved OpenAI-compat request+response dumps into trace JSONL.

Accepts a file, a directory of JSON/JSONL, or fixtures/prompts.json (agent rows).
Does not invent cached_tokens. Redacts secret-shaped strings.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "fixtures" / "traces" / "turns.jsonl"

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
    r"fw_[A-Za-z0-9]{16,}|FIREWORKS_API_KEY\s*=\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


def redact_text(value: str) -> str:
    return SECRET_RE.sub("[REDACTED]", value)


def redact(obj):
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    return obj


def estimate_tokens(messages: list, tools: list | None) -> int:
    blob = json.dumps(messages, ensure_ascii=False)
    if tools:
        blob += json.dumps(tools, ensure_ascii=False)
    return max(1, len(blob) // 4)


def cached_of(usage: dict) -> int:
    details = usage.get("prompt_tokens_details") or {}
    return int(
        details.get("cached_tokens")
        or usage.get("cached_tokens")
        or usage.get("prompt_cache_hit_tokens")
        or 0
    )


def thinking_of(usage: dict) -> int | None:
    details = usage.get("completion_tokens_details") or {}
    for key in ("reasoning_tokens", "thinking_tokens"):
        if details.get(key) is not None:
            return int(details[key])
        if usage.get(key) is not None:
            return int(usage[key])
    return None


def load_json_bytes(path: Path):
    text = path.read_text()
    if path.suffix == ".jsonl":
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows
    data = json.loads(text)
    return data


def as_request_response(raw: dict) -> tuple[dict, dict]:
    if "request" in raw or "response" in raw:
        return raw.get("request") or {}, raw.get("response") or {}
    if "messages" in raw:
        resp = {}
        if raw.get("usage") or raw.get("choices"):
            resp = {
                k: raw[k]
                for k in ("usage", "choices", "model")
                if k in raw
            }
        req = {k: v for k, v in raw.items() if k not in ("usage", "choices")}
        return req, resp
    raise ValueError("not an OpenAI-compat request, response wrapper, or chat object")


def normalize_dump(raw: dict, *, source: str, default_id: str) -> dict | None:
    req, resp = as_request_response(raw)
    messages = req.get("messages") or raw.get("messages")
    if not messages:
        return None
    tools = req.get("tools") or raw.get("tools")
    usage_src = (resp.get("usage") or raw.get("usage") or {})
    prompt_tokens = int(usage_src.get("prompt_tokens") or 0)
    completion_tokens = int(usage_src.get("completion_tokens") or 0)
    cached = cached_of(usage_src) if usage_src else 0
    if prompt_tokens == 0:
        prompt_tokens = estimate_tokens(messages, tools)
    usage = {
        "prompt_tokens": prompt_tokens,
        "cached_tokens": cached,
        "completion_tokens": completion_tokens,
    }
    thinking = thinking_of(usage_src)
    if thinking is not None:
        usage["thinking_tokens"] = thinking
    model = (
        resp.get("model")
        or req.get("model")
        or raw.get("model")
        or "MoM"
    )
    turn = {
        "id": str(raw.get("id") or req.get("id") or default_id),
        "source": source,
        "messages": redact(messages),
        "usage": usage,
        "model": model,
    }
    if tools:
        turn["tools"] = redact(tools)
    max_tokens = req.get("max_tokens") or raw.get("max_tokens")
    if max_tokens:
        turn["max_tokens"] = int(max_tokens)
    routed = raw.get("routed_model") or resp.get("model")
    if routed:
        turn["routed_model"] = routed
    if raw.get("quality"):
        turn["quality"] = raw["quality"]
    elif source == "synthetic-shape":
        turn["quality"] = "synthetic-shape — not a Claude Code dump; cached_tokens not invented"
    return turn


def from_prompt_item(item: dict) -> dict | None:
    if item.get("messages"):
        messages = list(item["messages"])
    else:
        prompt = item.get("prompt") or ""
        pad = int(item.get("pad_tokens") or 0)
        if pad > 0:
            prompt = prompt + "\n" + ("pad " * pad)
        messages = [{"role": "user", "content": prompt}]
    tools = item.get("tools")
    raw = {
        "id": item.get("id"),
        "messages": messages,
        "tools": tools,
        "model": "MoM",
        "max_tokens": item.get("max_tokens") or 8192,
        "quality": "synthetic-shape — seeded from fixtures/prompts.json",
    }
    return normalize_dump(raw, source="synthetic-shape", default_id=str(item.get("id") or "row"))


def iter_inputs(path: Path, only_ids: set[str] | None):
    data = load_json_bytes(path)
    if isinstance(data, list):
        looks_like_prompts = data and isinstance(data[0], dict) and (
            "expected_decision" in data[0] or "prompt" in data[0]
        )
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            if only_ids and str(item.get("id")) not in only_ids:
                continue
            if looks_like_prompts:
                turn = from_prompt_item(item)
            else:
                turn = normalize_dump(
                    item, source="live", default_id=f"{path.stem}-{i+1}"
                )
            if turn:
                yield turn
        return
    if isinstance(data, dict):
        turn = normalize_dump(data, source="live", default_id=path.stem)
        if turn:
            yield turn


def collect(paths: list[Path], only_ids: set[str] | None, synthetic: bool) -> list[dict]:
    turns = []
    for path in paths:
        if path.is_dir():
            files = sorted(
                p
                for p in path.iterdir()
                if p.suffix in {".json", ".jsonl"} and p.name != "schema.json"
            )
            for f in files:
                if f.name == "turns.jsonl":
                    continue
                turns.extend(collect([f], only_ids, synthetic))
            continue
        for turn in iter_inputs(path, only_ids):
            if synthetic:
                turn["source"] = "synthetic-shape"
                if "quality" not in turn:
                    turn["quality"] = "synthetic-shape — not a Claude Code dump"
            turns.append(turn)
    return turns


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path, help="JSON/JSONL file or directory")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--only-ids",
        default="",
        help="Comma-separated ids (for prompts.json agent rows)",
    )
    parser.add_argument(
        "--synthetic-shape",
        action="store_true",
        help="Label emitted rows synthetic-shape (no live Claude Code dump)",
    )
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()

    only_ids = {x.strip() for x in args.only_ids.split(",") if x.strip()} or None
    turns = collect(args.inputs, only_ids, args.synthetic_shape)
    if not turns:
        print("no turns ingested", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "w"
    with args.out.open(mode) as fh:
        for turn in turns:
            fh.write(json.dumps(turn, ensure_ascii=False) + "\n")
    print(f"wrote {len(turns)} turns -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
