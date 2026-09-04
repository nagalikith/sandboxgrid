# INVALID — archived four-shot (do not quote)

Previous GLM-mid policy. Cheap turn failed the task ("I don't see the code"). GLM and Kimi hit the 256-token cap. Two-part prices (no cache). Not a run of the current config.

# Static vs dynamic cost

Measured under the previous GLM-mid policy.

Source: `docs/archive/founder-four.measured.json`. Two-part prices, previous policy. Do not quote.
The secret-refuse call is $0 and is omitted here (no tokens).

Pricing: official Fireworks Standard list as of 2026-09-03 ([docs](https://docs.fireworks.ai/serverless/pricing)).
Baselines: always `fireworks/kimi-k2p7-code` and always `fireworks/glm-5p2`.

GLM 5.2 Standard list is **higher** than K2.7 Code. Mid is a quality band, not a cheaper band.

| records | dynamic USD | always-strong USD | vs strong | always-mid USD | vs mid |
|---|---:|---:|---:|---:|---:|
| 3 | 0.002449 | 0.002832 | 0.000384 (13.5%) | 0.003192 | 0.000743 (23.3%) |

| decision | model | prompt tok | completion tok | dynamic USD | always-strong USD | always-mid USD |
|---|---|---:|---:|---:|---:|---:|
| cheap | `accounts/fireworks/models/minimax-m3` | 141 | 145 | 0.000216 | 0.000714 | 0.000835 |
| mid | `accounts/fireworks/models/glm-5p2` | 26 | 256 | 0.001163 | 0.001049 | 0.001163 |
| hard | `accounts/fireworks/models/kimi-k2p7-code` | 48 | 256 | 0.001070 | 0.001070 | 0.001194 |
