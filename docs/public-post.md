# Local semantic routing in front of Fireworks serverless

A coding-agent client talks to a local [vLLM Semantic Router](https://github.com/vllm-project/semantic-router) (`vllm-sr` 0.3). The router picks a current Fireworks serverless model from the request shape, or refuses the request on this machine if it looks like a leaked secret.

This is a method write-up, not a savings claim.

## Policy

| If | Then | Why |
|---|---|---|
| Secret-shaped prompt | Local refuse | Never send the body to Fireworks |
| Context ≳ 200K tokens | GLM 5.2 | 1M window; Kimi is 262K |
| Tools, multi-turn, or a tool loop | Kimi K2.7 Code | Coding-agent default |
| Short easy single turn | MiniMax M3 | Cheap Standard list |
| Anything else | Kimi K2.7 Code | Fail open to quality |

Fireworks already has Standard / Priority / Fast serving paths. This demo does not reimplement Fast (`accounts/fireworks/routers/glm-5p2-fast`) or Priority (`service_tier`).

Official Standard cells are **input / cached / output** per 1M ([pricing](https://docs.fireworks.ai/serverless/pricing), 2026-09-03):

| Model | Input / cached / output | Context |
|---|---|---|
| MiniMax M3 | $0.30 / $0.06 / $1.20 | 512K |
| Kimi K2.7 Code | $0.95 / $0.19 / $4.00 | 262K |
| GLM 5.2 | $1.40 / $0.14 / $4.40 | 1M |

K2.7 Code thinking is on by default. Cached input is the cost that matters on agent prefixes.

Config: [`config.yaml`](../config.yaml). Runbook: [`ROUTING.md`](../ROUTING.md).

## Eval (routing only, $0)

[`fixtures/prompts.json`](../fixtures/prompts.json) is held-out. Slogans used as complexity candidates live in [`fixtures/complexity-exemplars.json`](../fixtures/complexity-exemplars.json) only.

The previous **0 / 18** table copied the eval set into the candidate bank and dropped `threshold` to `0.35`. That was memorization. Untuned slogans at `0.75` put every coding one-liner in `medium` ([docs/eval-results.before.md](eval-results.before.md)). After serving this config, `python3 scripts/eval_suite.py` is the number — including misses.

## Cost

`scripts/cost_chart.py` prices **the same token counts** on Kimi and GLM, including cached input. That is a token-hold counterfactual, not "what the other model would have generated."

The old four-shot ($0.002449 / 13.5%) is in [`docs/archive/`](archive/). It used two-part prices, a cheap turn that failed the task, and two completions that hit `max_tokens=256`. Do not quote it.

Smoke fixture only: [`docs/cost-comparison.sample.md`](cost-comparison.sample.md).

## How to retry

```bash
export FIREWORKS_API_KEY=...
vllm-sr validate --config config.yaml
vllm-sr serve --config config.yaml
python3 scripts/eval_suite.py
./scripts/e2e_probe.sh
```

Send `"model": "MoM"`. Read `vllm-sr status` for ports. Scripts default to chat on `18080/inference/v1` and eval/replay on `8080`.

## Limitations

- Heuristic containment, not a PII model.
- Synthetic agent/long-context fixtures, not a Claude Code dump.
- Complexity does not separate coding one-liners; context and conversation do the Fireworks-specific work.
- No published agent savings rate until someone prices real cached-prefix traces.
