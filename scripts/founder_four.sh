#!/usr/bin/env bash
# Wrapper — the Python script is the source of truth.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/founder_four.py" "$@"
