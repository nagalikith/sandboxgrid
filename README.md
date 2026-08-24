# sandboxgrid

> Disposable browser sandboxes for AI agents — provisioning, live view, artifacts, and audit trails behind one API.

sandboxgrid turns "give me a browser" into an API call. Each sandbox is an isolated Chromium environment with its own lifecycle (request → provision → ready → terminated), a live-view URL for humans to watch or take over, CDP access for automation code, and an artifact store that records everything the agent touched.

```
POST /sandboxes ──► Provisioner ──► browser_url + cdp_url + dashboard_url
                                    │
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
              Live view (watch/take over)    CDP endpoint (drive it)
                     │
                     ▼
        Artifacts: screenshots, steps, session recordings ──► audit trail
```

## Features

- **Pluggable provisioners** — run sandboxes as local processes (`local`), Docker containers with noVNC + CDP (`docker`), or any backend implementing the `Provisioner` protocol.
- **Live view + human-in-the-loop** — every sandbox exposes a watchable, controllable browser surface.
- **Artifact pipeline** — screenshots, step logs, and session recordings are captured, stored per-owner, versioned, and derivable (artifact lineage links).
- **Internal HMAC auth** — timestamped, signed requests between services; no bearer tokens on the wire.
- **Event streaming** — Server-Sent Events per sandbox with sequence numbers, replay from `Last-Event-ID`, and bounded streams via `?max_events=`.
- **Job queue worker** — RabbitMQ-backed worker handles provision/command/dashboard jobs; TTL enforcement auto-terminates expired sandboxes.
- **Dashboards** — server-rendered sandbox dashboards with chart rendering.

## Quickstart

```bash
pip install -e ".[dev]"
uvicorn sandbox_api.main:app --reload --port 8000
```

Create a sandbox:

```bash
python -m tests.auth_helpers  # helper reference
curl -X POST http://localhost:8000/sandboxes \
  -H "X-User-Id: agent_1" \
  -d '{"ttl_seconds": 600, "capabilities": ["browser"]}'
```

Run the test suite:

```bash
pytest
```

### Docker

```bash
docker-compose up -d          # api + worker + rabbitmq
```

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `sqlite:///./sandbox.db` | Any SQLAlchemy URL |
| `SANDBOX_PROVISIONER` | `local` | `local` \| `docker` |
| `SANDBOX_ARTIFACTS_ROOT` | `./artifacts` | Artifact storage root |
| `INTERNAL_AUTH_SECRET` | — | HMAC secret for internal routes |
| `RABBITMQ_URL` | `amqp://guest:guest@localhost/` | Job queue broker |
| `SANDBOX_PUBLIC_HOST` | `http://localhost` | Public base for browser/live URLs |

## Architecture

```
sandbox_api/
├── api/            # FastAPI app assembly + route modules
├── core/           # database, auth, events, queue, jobs, paths
├── sandboxes/      # domain: models, provisioner, orchestrator, storage, planner
├── dashboards/     # dashboard routes + chart rendering
├── web/            # templates
├── artifacts.py    # artifact records, blob store, lineage links
├── share_session.py# shareable session tokens
└── worker.py       # RabbitMQ job consumer (provision/command/dashboard)
```

The [`Provisioner`](sandbox_api/sandboxes/provisioner.py) protocol is three methods:

```python
class Provisioner(Protocol):
    async def provision(self, sandbox_id, request, *, owner_id) -> ProvisionResult: ...
    async def stop(self, sandbox_id, backend_ref) -> None: ...
    def cdp_host(self) -> str: ...
```

Implement it to back sandboxes with any browser infrastructure.

## License

MIT
