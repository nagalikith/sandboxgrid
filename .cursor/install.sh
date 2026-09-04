#!/usr/bin/env bash
# Idempotent dependency setup for sandboxgrid Cloud Agents.
# Installs system packages (python venv support + RabbitMQ broker) and the
# Python project with dev, worker, and examples extras into a local venv.
set -euo pipefail

cd "$(dirname "$0")/.."

# System packages: python3-venv (ensurepip) and the RabbitMQ broker used by
# the API + worker job queue. Skip apt work if everything is already present.
missing_pkgs=()
command -v rabbitmq-server >/dev/null 2>&1 || missing_pkgs+=(rabbitmq-server)
python3 -c "import ensurepip" >/dev/null 2>&1 || missing_pkgs+=(python3-venv)
if [ "${#missing_pkgs[@]}" -gt 0 ]; then
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing_pkgs[@]}"
fi

# Python virtual environment (idempotent).
if [ ! -x ".venv/bin/python" ]; then
  python3 -m venv .venv
fi
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -e ".[dev,worker,examples]"

# Local runtime data directory for the sqlite DB + artifacts.
mkdir -p .devdata/artifacts

echo "install.sh: sandboxgrid dependencies ready."
