# Browserbase vs SandboxGrid API

> Disposable browser sandboxes (self-hosted) vs managed browser infrastructure — capability-by-capability comparison.
>
> Sources: full read of the sandboxgrid source tree (this repo) + all sections of [docs.browserbase.com](https://docs.browserbase.com) (core platform, identity, observability, agents, runtime, pricing) + [Stagehand v4](https://docs.stagehand.dev) + MCP docs.

## Positioning

**sandboxgrid** (this repo, MIT, Python/FastAPI) is a self-hostable *"give me a disposable browser"* platform: session lifecycle, live view, CDP access, artifact store, and audit trail behind one API — with **pluggable backends**, one of which **is Browserbase itself**.

**Browserbase** is the managed commercial superset: same skeleton, plus the anti-detection / identity / runtime / compliance layers that are hard to self-host.

Context: this platform was extracted from a private monorepo (`cua-lab` commit `881179d`); the private copy's `sandbox_api/` is now empty.

---

## Overlapping features

| Capability | Browserbase | sandboxgrid |
|---|---|---|
| Disposable session lifecycle | `POST /v1/sessions` → RUNNING → `REQUEST_RELEASE`; timeout per session | `requested→provisioning→ready→error→terminated`; TTL 60s–24h auto-terminate (`sandboxes/models.py`, `worker.py:enforce_ttl`) |
| Live view + human takeover | Live View: watch/click/type, embeddable iframe, per-tab URLs (`debuggerFullscreenUrl`) | noVNC `browser_url` (docker backend) or Browserbase live view passthrough; `InteractiveController` terminal takeover (`run_artifact.py`) |
| CDP automation endpoint | `connectUrl` (`wss://connect.browserbase.com`) | `cdp_url` on every sandbox; Playwright attaches over CDP (`worker.py:build_runtime_config`) |
| Recording & replay | Auto video per session (up to 10 tabs), HLS replay streaming, MP4 downloads | `record` → action-log tar.gz bundle artifact; deterministic `replay` with speed control (`SessionRecorder`/`SessionReplayer`) |
| Identity persistence ("Contexts") | Contexts API: encrypted Chromium user-data-dir, `persist` flag, lives indefinitely | `capture_profile` → Playwright storage-state artifact; reuse via `profile_artifact_id`; `/share-session` imports cookies + localStorage into a state artifact (`share_session.py`) |
| LLM-driven browsing | Stagehand `act`/`extract`/`observe`; managed Agents with `resultSchema` | `POST /commands/agent`: LLM plans a step list from task + page context (OpenAI-compatible JSON mode, `agent_planner.py`), then deterministic execution |
| Screenshots / DOM observability | Screenshots/PDFs returned directly; logs API (CDP events) | `screenshot`, `dom_snapshot` (html/a11y_json), `page_state` (forms/text/a11y) as versioned artifacts |
| Multitab | Up to 10 tabs, per-tab live view/replay/MP4 | `get_live_view()` returns `pages[]` list (`sandboxes/browserbase.py:125-136`) |
| Region selection | us-west-2 / us-east-1 / eu-central-1 / ap-southeast-1 | `region` param passed through to Browserbase sessions |

## What sandboxgrid adds that Browserbase doesn't have

- **Pluggable/self-hosted backends** — `Provisioner` protocol = `local` | `docker` (Chromium+noVNC container with port probing, CDP readiness checks, startup diagnostics) | `browserbase`. Browserbase explicitly argues *against* self-hosting; no such option exists there.
- **Meta-layer over Browserbase** — with `SANDBOX_PROVISIONER=browserbase`, sandboxgrid adds its artifact registry, audit trail, dashboards, and job queue *on top of* raw BB sessions.
- **Artifact system with lineage** — typed records (type/format/sensitivity/volatility/checksum), parent-child `derive` links, per-owner sharded storage, session manifests, retention sweep + purge-on-TTL (`artifacts.py`). BB stores downloads/recordings but has no general artifact graph API.
- **Deterministic step DSL with GUI-agent primitives** — 13 validated actions including `draw_path`, `draw_rect`, `point`, `freetext`, executed via CDP `Input.dispatchMouseEvent` with an injected visual cursor overlay (`worker.py:_execute_steps`). BB's deterministic layer is plain Playwright.
- **Service-to-service HMAC auth** — timestamp + method + path + body-SHA256 signatures, skew window, per-request `X-User-Id` ownership scoping (`core/internal_auth.py`). BB is API-key/project based.
- **Async job architecture** — RabbitMQ queue separating API from execution worker, command receipts, SSE event stream with sequence numbers + `Last-Event-ID` replay + bounded streams. BB's REST is synchronous; agent runs are polled.
- **Dashboards product** — per-sandbox metrics/charts/tables, ECharts server-side PNG rendering registered as artifacts (`dashboards/`). No BB analogue.
- **Offline-first** — `MockBrowserbaseClient` mirrors the full client surface so demos/tests run credential-free.

## What Browserbase adds that sandboxgrid doesn't have

| Area | What BB adds |
|---|---|
| Anti-bot identity | Fingerprint rotation, **Verified** browsers (Scale plan), Web Bot Auth signed agents |
| Proxies | Built-in residential pool, geo-targeting across 201 countries, BYO HTTP proxy, IP allowlisting |
| CAPTCHA | Auto-solving by default (~5–30s), console events, custom selectors |
| Managed Agents | Natural-language runs with `resultSchema`, message transcripts, Optimize tool, script generation |
| Search / Fetch APIs | `POST /v1/search`; markdown/json page fetch with schema extraction |
| Runtime | Serverless **Functions** (deploy Playwright agents, invoke via HTTP) |
| Model Gateway | OpenAI/Anthropic/Google billed through the BB key |
| Extensions & certificates | Upload MV3 extensions/certificates, attach to sessions |
| Files | Downloads auto-synced to cloud storage, uploads API, PDFs |
| Scale/compliance | Concurrency quotas + 429s, keep-alive reconnect, SOC 2 Type II, HIPAA BAA, ZDR, BYOS S3, SSO |
| Ecosystem | Node/Python/Go SDKs, hosted MCP server, ~30 integrations (LangChain, CrewAI, n8n, Temporal, Vercel AI…), Browse CLI/SKILL.md |

---

## Verdict: who did it better?

**Browserbase wins 7 of 9 rows — but not unanimously, and the two losses are instructive.**

| # | Row | Winner | Why |
|---|---|---|---|
| 1 | Lifecycle | **Browserbase** | BB timeouts are enforced server-side by infra; sandboxgrid's `enforce_ttl` is `asyncio.sleep(delay)` per sandbox (`worker.py:1231`) — if the worker restarts mid-TTL, nothing terminates the sandbox. No DB sweep for expired rows exists (only the artifact retention sweep). sandboxgrid's purge-artifacts-on-expiry is a nice touch BB lacks, though. |
| 2 | Live view | **Browserbase** | Pixel-streaming iframe with per-tab URLs, mobile viewports, read-only mode vs noVNC websocket (heavier, one URL per sandbox, no per-tab). In the browserbase-backend case sandboxgrid just proxies BB's own live view — it didn't build this row. |
| 3 | CDP endpoint | **Browserbase** (narrowly) | Functionally equivalent locally, but sandboxgrid's CDP port is unauthenticated on the host — anyone local can attach. BB's wss requires an API key. |
| 4 | Recording & replay | **Split** | As debugging *evidence*, BB wins: real video beats an action log. But sandboxgrid's record→tar.gz→`replay` re-*executes* deterministic steps — it's a repairable automation trace, not a movie. Different tool, genuinely useful. |
| 5 | Identity persistence | **Split** | BB Contexts persist the whole user-data-dir (IndexedDB, service workers, autofill, prefs, encrypted at rest); sandboxgrid's `storage_state` is cookies+localStorage only — much shallower. But `/share-session` *imports* credentials from an existing origin, which BB has no API for (you must log in inside their session or via live view). Depth BB, importability sandboxgrid. |
| 6 | LLM browsing | **Browserbase**, decisively | Stagehand is a closed loop (`observe → act → extract`, self-healing, per-action inference, caching). sandboxgrid's `plan_steps` is **open-loop**: one-shot plan from initial `page_context`, then blind execution — if step 5's selector dies, nothing replans. Demo-grade vs product. |
| 7 | Observability | **sandboxgrid** | Everything becomes a versioned artifact with type/format/checksum/sensitivity/volatility, parent-child derive links, session manifests, retention sweeps. BB hands you screenshots inline and logs via an API and expects you to build (or buy BYOS/ZDR for) the rest. This is sandboxgrid's best-designed subsystem. |
| 8 | Multitab | **Browserbase** | Per-tab live view + replay + MP4. sandboxgrid's engine drives a single `page` object; `pages[]` listing only exists when riding on BB. |
| 9 | Regions | **Browserbase** | Real infra in 4 regions. sandboxgrid's `region` param only exists by forwarding to BB; its own backends are single-host. |

### Honest caveats

- Rows 1, 2, 8, 9 measure **production scale** — a funded company vs an extracted solo repo, so BB winning them is expected, not damning.
- sandboxgrid isn't competing on those rows: it *embeds* BB as a pluggable backend and competes on the **control plane** BB deliberately doesn't sell — HMAC service auth with ownership scoping, RabbitMQ job isolation, the artifact graph with lineage, dashboards, offline mock, self-hosting, and a TTL-expiry purge that's arguably a stronger privacy story than BB's retention windows unless you pay for ZDR/BYOS.

**Net:** Browserbase built the better browser infrastructure. sandboxgrid built a respectable thin control plane around the same idea — its two wins (audit artifacts, executable replay) plus pluggability are exactly the layer Browserbase leaves to customers.

---

## Anomalies found while reading the code

1. `dashboards/charts_renderer.py` references `charts/render.js` for local Node rendering — the `charts/` directory isn't in the repo; only the Docker fallback (`cua-echarts:latest`) can work. Extraction leftover.
2. `SandboxRequest.allow_network` allowlist field exists but nothing enforces it — aspirational schema.
3. `platform/` package is pure `sys.modules` re-export shims kept for old import paths; `apps/` is an empty placeholder.
4. The Browserbase integration covers Sessions/LiveView/Contexts/Recording only — no proxy config beyond `proxies: bool`, no captcha toggle, no extension passthrough.
