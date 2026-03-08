# Education App

This package contains education-specific product logic built on top of the
agent-image platform in `sandbox_api.platform`.

## Scope

- grading APIs
- grading job persistence
- assessment dashboard generation
- grading automation runners

## Dependency Direction

`apps.education` depends on the platform layer.

It should consume:

- platform auth / queue / database primitives
- platform sandbox execution
- platform dashboard models and rendering
- platform artifact storage

It should not become the place where generic sandbox platform behavior lives.
