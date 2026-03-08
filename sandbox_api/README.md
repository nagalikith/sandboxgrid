# `sandbox_api` Package Layout

This package is now split into a reusable agent-image platform layer and
application-specific packages built on top of it, while keeping the existing
import surface stable for tests and scripts.

## Top-Level Split

- `platform/`
  Reusable sandbox/agent-image platform surface.
- `apps/education/`
  Education-specific grading application built on the platform.

## Internal Layout

- `core/`
  Shared infrastructure: database, auth, events, queue, jobs, and path helpers.
- `sandboxes/`
  Sandbox domain: models, storage, orchestrator, provisioner, command schemas, planner, and command routes.
- `dashboards/`
  Dashboard route layer, payload models, and chart rendering helpers.
- `api/`
  FastAPI app assembly plus route modules.
- `web/`
  HTML templates and template helpers.
- `main.py`
  Compatibility wrapper that exposes `app` for `uvicorn sandbox_api.main:app`.
- Flat modules kept in place
  Legacy module paths now act as compatibility shims so existing imports keep
  working while implementation lives under the folders above.

## Current Conventions

- Put app bootstrap and route composition under `api/`.
- Put shared infrastructure under `core/`.
- Put sandbox-related business logic under `sandboxes/`.
- Put dashboard-specific code under `dashboards/`.
- Put product-specific education code under `apps/education/`.
- Treat the grading tool as a consumer of the platform layer.
- Put UI templates under `web/templates/`.
- Keep worker entrypoints stable unless a dedicated worker package is introduced.
