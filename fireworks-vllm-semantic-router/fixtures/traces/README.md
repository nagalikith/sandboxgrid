# Traces

One JSON object per turn, JSONL in `turns.jsonl`. Schema: [`schema.json`](schema.json).

Seed rows come from held-out `agent-01`…`agent-03` in `fixtures/prompts.json`. They are **synthetic-shape**: structural `messages` / `tools`, estimated prompt tokens, **cached_tokens = 0**. Not a Claude Code history.

Drop live OpenAI-compat dumps here (redact secrets) and run:

```bash
python3 scripts/ingest_trace.py path/to/dump.json --out fixtures/traces/turns.jsonl
python3 scripts/trace_histogram.py
python3 scripts/price_traces.py
```

Until live rows exist, every public number that mentions this file must say synthetic-shape.
