#!/usr/bin/env python3
"""Run fixtures/prompts.json through the router eval API (routing only, no model call)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURES = ROOT / "fixtures" / "prompts.json"
DEFAULT_EVAL = os.environ.get("ROUTER_EVAL_URL", "http://127.0.0.1:8080/api/v1/eval")


def load_prompts(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array")
    return data


def build_body(item: dict) -> dict:
    if item.get("messages"):
        messages = list(item["messages"])
    else:
        prompt = item.get("prompt") or ""
        pad = int(item.get("pad_tokens") or 0)
        if pad > 0:
            # ~4 chars/token. Do not store 200K tokens in git.
            prompt = prompt + "\n" + ("pad " * pad)
        messages = [{"role": "user", "content": prompt}]
    body: dict = {"messages": messages}
    if item.get("tools"):
        body["tools"] = item["tools"]
    return body


def eval_item(item: dict, endpoint: str, timeout: int) -> dict:
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(build_body(item)).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": True, "payload": json.loads(resp.read().decode())}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": exc.read().decode()[:500] or str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def summarize(payload: dict) -> dict:
    dr = payload.get("decision_result") or {}
    models = payload.get("recommended_models") or []
    return {
        "decision": dr.get("decision_name") or payload.get("routing_decision"),
        "model": models[0] if models else None,
        "matched": dr.get("matched_signals") or {},
        "used": dr.get("used_signals") or {},
    }


def write_markdown(rows: list[dict], path: Path) -> None:
    lines = [
        "# Eval results (routing only, no Fireworks call)",
        "",
        "Source: held-out `fixtures/prompts.json` against the live router `/api/v1/eval`.",
        "",
        "Policy is refuse → over-Kimi-budget GLM → stay/Kimi. Complexity cost",
        "bands are gone. Do not treat a low miss count as a quality claim —",
        "this is routing-only, not a Claude Code session.",
        "",
        "| id | expected | got | model | match? |",
        "|---|---|---|---|---|",
    ]
    for row in rows:
        if not row.get("ok"):
            lines.append(f"| {row.get('id')} | {row.get('expected_decision')} | ERROR |  | no |")
            continue
        ok = row.get("looks_right")
        mark = "yes" if ok else ("no" if ok is False else "")
        lines.append(
            f"| {row.get('id')} | {row.get('expected_decision')} | {row.get('decision')} | "
            f"`{row.get('model') or ''}` | {mark} |"
        )
    misses = [r for r in rows if (not r.get("ok")) or r.get("looks_right") is False]
    lines += ["", f"Mis-routes: {len(misses)} / {len(rows)}.", ""]
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--endpoint", default=DEFAULT_EVAL)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "eval-results.json")
    parser.add_argument("--out-md", type=Path, default=ROOT / "docs" / "eval-results.md")
    args = parser.parse_args()

    rows = []
    for item in load_prompts(args.fixtures):
        result = eval_item(item, args.endpoint, args.timeout)
        row = {
            "id": item.get("id"),
            "expected_band": item.get("expected_band"),
            "expected_decision": item.get("expected_decision"),
            "prompt": item.get("prompt"),
        }
        if not result["ok"]:
            row["ok"] = False
            row["error"] = result["error"]
            print(f"[FAIL] {row['id']}: {result['error'][:120]}")
        else:
            summary = summarize(result["payload"])
            row.update(ok=True, **summary)
            row["looks_right"] = (
                summary["decision"] == item.get("expected_decision")
                if item.get("expected_decision")
                else None
            )
            row["payload"] = result["payload"]
            mark = "ok" if row["looks_right"] else "MIS"
            print(f"[{mark}] {row['id']}: expected={row['expected_decision']} got={row['decision']} model={row['model']}")
        rows.append(row)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2) + "\n")
    write_markdown(rows, args.out_md)
    print(f"wrote {args.out}")
    print(f"wrote {args.out_md}")
    http_ok = all(r.get("ok") for r in rows)
    routes_ok = all(r.get("looks_right") is not False for r in rows)
    if not http_ok:
        return 1
    if not routes_ok:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
