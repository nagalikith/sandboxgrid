# Agent Platform

This package is the reusable agent-image platform layer.

## Includes

- API assembly and platform routes
- shared infrastructure (`core`)
- sandbox lifecycle and browser execution (`sandboxes`)
- dashboards
- artifacts
- session sharing
- worker runtime
- web templates

## Used By

- `sandbox_api.apps.education`

The education grading tool should be understood as an app on top of this
platform, not as the platform itself.
