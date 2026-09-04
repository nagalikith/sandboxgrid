# Token-hold cost (SMOKE FIXTURE — not a published claim)

Generated from `fixtures/replay-sample.json`. Invented token counts. Do not quote these percentages.

Source: `fixtures/replay-sample.json`. Same token counts on every model, including cached input.
This is not what another model would have generated.
The secret-refuse call is $0 and is omitted here (no tokens).

Pricing: official Fireworks Standard input / cached / output as of 2026-09-03 ([docs](https://docs.fireworks.ai/serverless/pricing)).
Baselines: always `fireworks/kimi-k2p7-code` and always `fireworks/glm-5p2` (1M-context list).

| records | dynamic USD | always-Kimi USD | vs Kimi | always-GLM USD | vs GLM |
|---|---:|---:|---:|---:|---:|
| 3 | 0.084252 | 0.075362 | -0.008890 (-11.8%) | 0.084784 | 0.000532 (0.6%) |

| decision | model | prompt | cached | completion | dynamic USD | always-Kimi USD | always-GLM USD |
|---|---|---:|---:|---:|---:|---:|---:|
| trivial_edit | `fireworks/minimax-m3` | 120 | 0 | 40 | 0.000084 | 0.000274 | 0.000344 |
| agent_turn | `fireworks/kimi-k2p7-code` | 4000 | 3200 | 180 | 0.002088 | 0.002088 | 0.002360 |
| long_context_window | `fireworks/glm-5p2` | 220000 | 180000 | 200 | 0.082080 | 0.073000 | 0.082080 |
