# Semantic routing demo (Fireworks + vLLM-SR)

Local [vLLM Semantic Router](https://github.com/vllm-project/semantic-router) in front of current Fireworks serverless coding models.

- Secret-shaped prompts are refused locally.
- Prompts over ~200K tokens go to GLM 5.2 (1M context). That is why GLM is in the pool — not because it is a cheaper "mid" band.
- Tool definitions, multi-turn user chat, or an active tool loop go to Kimi K2.7 Code.
- Short, easy, single-turn edits go to MiniMax M3.
- Everything else fail-opens to Kimi.

Fireworks already ships Standard / Priority / Fast serving paths (for example `accounts/fireworks/routers/glm-5p2-fast`). This demo does not reimplement them.

This file is the demo. The repo README is the sandboxgrid product and is unrelated.

## Prices (official Standard, 2026-09-03)

From [Fireworks serverless pricing](https://docs.fireworks.ai/serverless/pricing). Cells are **input / cached input / output** per 1M tokens.

| Route | Model | Input / cached / output | Context |
|---|---|---|---|
| easy short | `accounts/fireworks/models/minimax-m3` | $0.30 / $0.06 / $1.20 | 512K |
| agent / default | `accounts/fireworks/models/kimi-k2p7-code` | $0.95 / $0.19 / $4.00 | 262K |
| long context | `accounts/fireworks/models/glm-5p2` | $1.40 / $0.14 / $4.40 | 1M |

K2.7 Code thinking is on by default. Do not send `chat_template_kwargs.enable_thinking`.

MiniMax M2.5 and Kimi K2.6 Turbo are deprecated on serverless. Do not put them back.

## Setup

```bash
export FIREWORKS_API_KEY=...          # never commit this
vllm-sr validate --config config.yaml
vllm-sr serve --config config.yaml    # needs Docker
```

Read `vllm-sr status` for the chat and management ports. Do not copy ports from this file. On the box that last ran this demo, chat was `http://127.0.0.1:18080/inference/v1/chat/completions` and eval/replay stayed on `8080`. Scripts default to those values via `ROUTER_CHAT_URL`, `ROUTER_EVAL_URL`, and `ROUTER_REPLAY_URL`.

Send `"model": "MoM"`. Pinning a Fireworks model ID bypasses the recipe.

```bash
# Routing-only suite (free). Held-out prompts. Exits non-zero on mis-routes.
python3 scripts/eval_suite.py

# One routed completion (not a pinned model).
./scripts/e2e_probe.sh

# Four live calls. Smoke only. Capped completions are incomplete.
python3 scripts/founder_four.py

# Token-hold counterfactual from a dump (same tokens on every model, including cache).
python3 scripts/cost_chart.py --source fixtures/replay-sample.json
```

## Test set

[`fixtures/complexity-exemplars.json`](fixtures/complexity-exemplars.json) is the slogan bank in `config.yaml`. [`fixtures/prompts.json`](fixtures/prompts.json) is held-out: easy/mid/hard one-liners, synthetic agent/tool-loop turns, a padded long-context case, and fake secrets. Do not paste eval prompts into the candidate lists.

Complexity at `0.75` historically classified every coding one-liner as `medium` ([docs/eval-results.before.md](docs/eval-results.before.md)). That is expected. Those rows should land on Kimi via `moderate_change`, not MiniMax.

Secret cases use well-known fakes. Do not paste live credentials.

## What the numbers are allowed to say

| Source | What it proves |
|---|---|
| `python3 scripts/eval_suite.py` | Which decision/model a held-out request would hit. Free. Not dollars. |
| Live chat `usage` | Tokens actually billed, including cached input when the API reports it. |
| `scripts/cost_chart.py` | Token-hold counterfactual: same token counts priced on Kimi vs GLM vs the routed model. Not "what that model would have generated." |

Do not publish a "% cheaper" from four short chats. The old four-shot is in [`docs/archive/`](docs/archive/) — previous GLM-mid policy, a failed cheap turn, two capped completions, two-part prices.

[`docs/cost-comparison.sample.*`](docs/cost-comparison.sample.md) is a smoke run on [`fixtures/replay-sample.json`](fixtures/replay-sample.json). Do not quote it.

## Limitations

- **Heuristic containment.** Keyword + regex. Not a PII model. A determined leak can still slip through.
- **No real Claude Code history.** Agent fixtures are synthetic `messages` / `tools` shapes.
- **Complexity is weak on coding one-liners.** Context and conversation carry the Fireworks-specific routes.
- **This workspace also contains sandboxgrid.** Ignore it for this demo. Do not commit `.vllm-sr/` or `models/`.
