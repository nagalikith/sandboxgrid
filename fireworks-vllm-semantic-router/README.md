# Fireworks coding-agent router

A local sidecar in front of [Fireworks](https://fireworks.ai) serverless. Coding-agent clients send one request; the router decides whether that request stays on this machine, needs a 1M-context model, or should stay on the coding default so a warm prefix keeps cached input.

This is a working demo for B2B teams that already run agents against Fireworks and want the routing policy explained with numbers — not a savings pitch and not a new model vendor.

Built on [vLLM Semantic Router](https://github.com/vllm-project/semantic-router) 0.3. Models are current Fireworks Standard serverless (as of 2026-09-03).

---

## The problem this is for

A coding agent (IDE sidecar, internal copilot, support-engineering bot) typically talks to **one** model ID. That is simple, and it is expensive in the wrong places:

1. **Secrets leave the building.** An API key or private key pasted into chat goes straight to the provider.
2. **Context overflows.** Kimi K2.7 Code has a 262K window. A long repo dump plus thinking plus `max_tokens` does not fit. The request should move to GLM 5.2 (1M), not fail mid-turn.
3. **Hopping models busts cache.** Fireworks cached input on Kimi is **$0.19 / 1M** versus **$0.95 / 1M** uncached. Switching to a cheaper model for a “small” follow-up throws away the prefix you already paid to fill.

Fireworks already sells Standard / Priority / Fast serving paths. This project does not reimplement Fast or Priority. It sits **in front** of Standard models and chooses *which* model (or refuse) from the shape of the request.

---

## What the router does

```mermaid
flowchart TD
  client[Coding-agent client] --> sidecar[Local router]
  sidecar --> secret{Looks like a leaked secret?}
  secret -->|yes| refuse[Refuse on this machine<br/>body never reaches Fireworks]
  secret -->|no| budget{Prompt + thinking + output<br/>no longer fits 262K?}
  budget -->|yes| glm[GLM 5.2 · 1M context]
  budget -->|no| prefix{Same hot prefix as last turn?}
  prefix -->|yes| stay[Stay on the current model]
  prefix -->|no| kimi[Kimi K2.7 Code · default]
```

| Situation | Action | Why |
|---|---|---|
| Prompt looks like an API key or private key | **Refuse locally** | The body never goes to Fireworks |
| Prompt + reserved thinking/output ≳ 262K | **GLM 5.2** | Only model in the pool with a 1M window |
| Follow-up on the same hot prefix | **Stay** | Cached input on Kimi is $0.19 vs $0.95 |
| Everything else | **Kimi K2.7 Code** | Coding-agent default; thinking is on |

We do **not** route “easy” one-liners to MiniMax. Agent traffic is tools, multi-turn, and tool loops — not short no-tool edits. MiniMax M3 stays in the catalog so we can price a counterfactual and run smoke tests. It is not a production route unless live traces later show real short, non-critical, no-tool turns *and* quality holds.

Clients send `"model": "MoM"` to enter this policy. Pinning a Fireworks model ID (`accounts/fireworks/models/kimi-k2p7-code`, and so on) is pass-through: no refuse, no budget cutover, no stay logic. That is intentional — an operator can pin when they want a raw model.

K2.7 thinking is on by default. The next turn must keep `reasoning_content` (or Fireworks’ equivalent) on the previous assistant message, or both quality and prefix cache break. See [Fireworks reasoning](https://docs.fireworks.ai/guides/reasoning).

---

## Models and official prices

Standard list from [Fireworks serverless pricing](https://docs.fireworks.ai/serverless/pricing), **2026-09-03**. Cells are **input / cached input / output** per 1M tokens.

| Role | Model | Input / cached / output | Context |
|---|---|---|---|
| Default / stay | `accounts/fireworks/models/kimi-k2p7-code` | $0.95 / **$0.19** / $4.00 | 262K |
| Over Kimi budget | `accounts/fireworks/models/glm-5p2` | $1.40 / $0.14 / $4.40 | 1M |
| Catalog only (smoke / counterfactual) | `accounts/fireworks/models/minimax-m3` | $0.30 / $0.06 / $1.20 | 512K |

MiniMax M2.5 and Kimi K2.6 Turbo are deprecated on serverless and are not in this pool.

The number that matters on agent prefixes is **cached input**. A policy that hops off Kimi to “save” on a short turn pays $0.95 again to refill the prefix.

---

## What we measured

Two different measurements. They are not interchangeable.

### 1. Routing eval — held-out requests, $0, no Fireworks call

23 held-out prompts (coding one-liners, agent/tool-loop shapes, a budget-sized long context, and well-known fake secrets) against the live router. The suite asks only: *which decision and model would this request hit?*

**Mis-routes: 0 / 23.**

| Kind | Count | Expected | Got |
|---|---:|---|---|
| Coding / agent turns | 18 | Kimi K2.7 Code | Kimi K2.7 Code |
| Secret-shaped prompts | 4 | Local refuse | Local refuse |
| Over-budget context | 1 | GLM 5.2 | GLM 5.2 |

This is routing correctness, not “the model solved the ticket.” Full table: [docs/eval-results.md](docs/eval-results.md).

Secret cases use well-known fakes (`AKIAIOSFODNN7EXAMPLE`, a marked test Fireworks token, a `BEGIN PRIVATE KEY` block). Live credentials are never used.

### 2. Traffic shape — why the policy looks like this

Three seeded agent turns (tools, multi-turn, tool-loop). **Synthetic request shapes**, not a dump of anyone’s IDE history. Cached tokens were not invented.

| | |
|---|---|
| Turns | 3 |
| Agent-shaped (tools or multi-turn or tool loop) | **3 / 3** |
| Short no-tool MiniMax-style edits | **0 / 3** |
| Budget over Kimi 262K | **0 / 3** |
| Cached token fraction | **0** (none recorded) |
| Prompt tokens | min 50 · median 73 · max 85 |
| Estimated budget (prompt + thinking + max_tokens) | ~8.2K — well inside 262K |

That is why complexity bands (“easy / mid / hard → cheap / mid / expensive model”) are gone. On this traffic they would hop models and bust cache, and they never saw a short no-tool edit to send to MiniMax.

### 3. Token-hold cost — wiring check, not a bill

Same three seeds priced with official three-part rates. Every counterfactual uses **the same token counts**. Cache is whatever the turn recorded (`0`). Completions are `0`. This proves the price table is wired. It is not what a customer would be billed and it is not “what the other model would have generated.”

| | Always Kimi | Always GLM | Always MiniMax |
|---|---:|---:|---:|
| 3 synthetic turns | $0.000198 | $0.000291 | $0.000062 |

There is no routed dollar column yet — seeds were not live completions. There is **no “% cheaper”** and no four-chat savings rate. When a client has 20–50 redacted live turns with real `usage` (including `cached_tokens`), the same table becomes a counterfactual they can stand behind.

---

## How a client uses it

1. Point the coding-agent client at the local router instead of `https://api.fireworks.ai/inference/v1`.
2. Set `"model": "MoM"` so the policy runs.
3. Keep Fireworks’ API key on the router host only (`FIREWORKS_API_KEY`). It is not in this repository.
4. Read `vllm-sr status` for the bind. On the box that signed the eval, chat was `http://127.0.0.1:18080/inference/v1/chat/completions` and management / eval stayed on `8080`.

```bash
export FIREWORKS_API_KEY=...          # never commit this
vllm-sr validate --config config.yaml
vllm-sr serve --config config.yaml    # Docker
vllm-sr status                        # real ports
```

To pin a model and skip the policy, send that Fireworks model ID. The refuse path is not applied on a pin — and this project never sends a secret-shaped body on the pin path.

---

## What we will not claim

- **No published “% cheaper.”** Four short chats, capped completions, or a token-hold on synthetic seeds are not an agent savings rate.
- **Containment is a heuristic** (keywords + regex), not a PII classifier. A determined leak can still get through.
- **No live customer session** is in this repo. The histogram is labeled synthetic until someone drops redacted production turns in.
- **Fast / Priority** are Fireworks products. We do not pretend Fast is a cheaper mid-tier.

What we *will* claim: on this policy file, the held-out routing suite is **0 / 23**, secrets are refused locally, over-budget context goes to GLM, and the default is Kimi with stay-on-prefix so cached input stays cheap.

---

## For a working session

Bring 20–50 redacted turns from the agent you actually run (prompt, tools, `usage` with cached tokens, model). We ingest them, rebuild the histogram, and re-price always-Kimi / always-GLM / always-MiniMax / routed on **your** tokens. That is the number a procurement conversation can use. Until then, the cost table stays a wiring check.

License: MIT.
