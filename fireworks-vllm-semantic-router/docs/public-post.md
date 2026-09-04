# Local semantic routing in front of Fireworks serverless

A coding-agent client talks to a local [vLLM Semantic Router](https://github.com/vllm-project/semantic-router) (`vllm-sr` 0.3). The router refuses secret-shaped prompts on this machine, sends turns that no longer fit Kimi's 262K window to GLM 5.2, and otherwise stays on Kimi K2.7 Code so a hot prefix keeps cached input.

This is a method write-up. It is not a savings claim. There is no "% cheaper."

## Policy

| If | Then | Why |
|---|---|---|
| Secret-shaped prompt | Local refuse | Never send the body to Fireworks |
| Prompt + reserved thinking/output ≳ 262K | GLM 5.2 | 1M window |
| Same hot prefix as last turn | Stay | Kimi cached input is $0.19 vs $0.95 |
| Anything else | Kimi K2.7 Code | Coding-agent default |

MiniMax M3 stays in the catalog for smoke and counterfactual pricing. It is not a route. Seed traces (`fixtures/traces/`) are **synthetic-shape** agent turns, not short no-tool edits.

Fireworks already has Standard / Priority / Fast serving paths. This demo does not reimplement Fast (`accounts/fireworks/routers/glm-5p2-fast`) or Priority (`service_tier`).

Official Standard cells are **input / cached / output** per 1M ([pricing](https://docs.fireworks.ai/serverless/pricing), 2026-09-03):

| Model | Input / cached / output | Context |
|---|---|---|
| MiniMax M3 | $0.30 / $0.06 / $1.20 | 512K |
| Kimi K2.7 Code | $0.95 / $0.19 / $4.00 | 262K |
| GLM 5.2 | $1.40 / $0.14 / $4.40 | 1M |

K2.7 Code thinking is on by default. Next-turn `messages` must keep `reasoning_content` or quality and cache both break ([Fireworks reasoning](https://docs.fireworks.ai/guides/reasoning)).

`"model": "MoM"` enters the recipe. Pinning `accounts/fireworks/models/...` skips signals, plugins, and cache-aware stay. Ports come from `vllm-sr status`.

Config: [`config.yaml`](../config.yaml). Runbook: [`README.md`](../README.md).

## Histogram (synthetic-shape)

Seeded from `agent-01`…`agent-03` only. Not a Claude Code dump. See [`docs/trace-histogram.md`](trace-histogram.md). Cached tokens were not invented.

## Eval (routing only, $0)

Held-out [`fixtures/prompts.json`](../fixtures/prompts.json) against this `config.yaml`: **0 / 23** mis-routes ([docs/eval-results.md](eval-results.md)). Routing-only. Not a Claude Code session and not a savings rate. No four-shot percentage.

## Cost

[`docs/trace-cost.md`](trace-cost.md) prices whatever traces exist with official three-part rates: always-Kimi / always-GLM / always-MiniMax / routed. Flag capped rows. No four-shot percentage.

Smoke fixture only (invented cache): [`docs/cost-comparison.sample.md`](cost-comparison.sample.md).

## How to retry

```bash
export FIREWORKS_API_KEY=...
vllm-sr validate --config config.yaml
vllm-sr serve --config config.yaml
vllm-sr status
python3 scripts/eval_suite.py
python3 scripts/bypass_check.py
python3 scripts/trace_histogram.py
python3 scripts/price_traces.py
```

## Limitations

- Heuristic containment, not a PII model.
- Synthetic-shape traces until someone captures redacted MoM turns.
- No published agent savings rate.
