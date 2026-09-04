# Trace cost (token-hold, not a savings rate)

Source: `fixtures/traces/turns.jsonl`. Official Standard input / cached / output as of 2026-09-03
([pricing](https://docs.fireworks.ai/serverless/pricing)).
Same `usage` on every counterfactual. Cache is whatever the turn recorded — never invented.

**No live usage yet.** Seed traces are synthetic-shape (request structure,
estimated prompt tokens, `cached_tokens = 0`, usually `completion_tokens = 0`).
This table is a wiring check, not a bill.

| rows | live | synthetic-shape | capped | always-Kimi USD | always-GLM USD | always-MiniMax USD | routed USD |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 | 0 | 3 | 0 | 0.000198 | 0.000291 | 0.000062 | — |

| id | source | prompt | cached | completion | capped? | Kimi | GLM | MiniMax | routed |
|---|---|---:|---:|---:|---|---:|---:|---:|---:|
| agent-01 | synthetic-shape | 73 | 0 | 0 |  | 0.000069 | 0.000102 | 0.000022 |  |
| agent-02 | synthetic-shape | 50 | 0 | 0 |  | 0.000048 | 0.000070 | 0.000015 |  |
| agent-03 | synthetic-shape | 85 | 0 | 0 |  | 0.000081 | 0.000119 | 0.000025 |  |

Capped rows (`completion_tokens >= max_tokens`) are incomplete work. Do not quote a %.
Do not add a Fast latency column unless a real Fast call happened.
