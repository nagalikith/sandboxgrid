# Eval results (historical, previous GLM-mid config)

Untuned baseline on a **previous** recipe (complexity bands, GLM as mid). Not a run of this repo's `config.yaml`. Kept so the miss pattern is visible; do not quote it as the signed result.


Source: `fixtures/prompts.json` against the live router `/api/v1/eval`.

| id | expected | got | model | match? |
|---|---|---|---|---|
| easy-01 | trivial_edit | moderate_change | `fireworks/glm-5p2` | no |
| easy-02 | trivial_edit | moderate_change | `fireworks/glm-5p2` | no |
| easy-03 | trivial_edit | moderate_change | `fireworks/glm-5p2` | no |
| easy-04 | trivial_edit | moderate_change | `fireworks/glm-5p2` | no |
| easy-05 | trivial_edit | moderate_change | `fireworks/glm-5p2` | no |
| mid-01 | moderate_change | moderate_change | `fireworks/glm-5p2` | yes |
| mid-02 | moderate_change | moderate_change | `fireworks/glm-5p2` | yes |
| mid-03 | moderate_change | moderate_change | `fireworks/glm-5p2` | yes |
| mid-04 | moderate_change | moderate_change | `fireworks/glm-5p2` | yes |
| mid-05 | moderate_change | moderate_change | `fireworks/glm-5p2` | yes |
| hard-01 | complex_reasoning | moderate_change | `fireworks/glm-5p2` | no |
| hard-02 | complex_reasoning | moderate_change | `fireworks/glm-5p2` | no |
| hard-03 | complex_reasoning | moderate_change | `fireworks/glm-5p2` | no |
| hard-04 | complex_reasoning | moderate_change | `fireworks/glm-5p2` | no |
| hard-05 | complex_reasoning | moderate_change | `fireworks/glm-5p2` | no |
| secret-01 | contain_secrets | contain_secrets | `fireworks/minimax-m3` | yes |
| secret-02 | contain_secrets | contain_secrets | `fireworks/minimax-m3` | yes |
| secret-03 | contain_secrets | contain_secrets | `fireworks/minimax-m3` | yes |

Mis-routes: 10 / 18.

