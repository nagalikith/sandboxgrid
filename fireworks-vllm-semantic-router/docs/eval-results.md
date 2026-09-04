# Eval results (routing only, no Fireworks call)

Source: held-out `fixtures/prompts.json` against this repo's `config.yaml` on the live router `/api/v1/eval` (chat listener 18080, eval 8080).

Policy is refuse → over-Kimi-budget GLM → stay/Kimi. Complexity cost
bands are gone. Do not treat a low miss count as a quality claim —
this is routing-only, not a Claude Code session.

| id | expected | got | model | match? |
|---|---|---|---|---|
| easy-01 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| easy-02 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| easy-03 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| easy-04 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| easy-05 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| mid-01 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| mid-02 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| mid-03 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| mid-04 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| mid-05 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| hard-01 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| hard-02 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| hard-03 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| hard-04 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| hard-05 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| secret-01 | contain_secrets | contain_secrets | `fireworks/minimax-m3` | yes |
| secret-02 | contain_secrets | contain_secrets | `fireworks/minimax-m3` | yes |
| secret-03 | contain_secrets | contain_secrets | `fireworks/minimax-m3` | yes |
| secret-04 | contain_secrets | contain_secrets | `fireworks/minimax-m3` | yes |
| agent-01 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| agent-02 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| agent-03 | coding_default | coding_default | `fireworks/kimi-k2p7-code` | yes |
| long-01 | over_kimi_budget | over_kimi_budget | `fireworks/glm-5p2` | yes |

Mis-routes: 0 / 23.

