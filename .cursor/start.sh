#!/usr/bin/env bash
# Per-boot startup for sandboxgrid Cloud Agents.
# Brings up the RabbitMQ broker (job queue) and waits until it is ready.
# The API and worker run as separate terminals (see environment.json).
set -euo pipefail

# Idempotent: if the broker is already up, just confirm readiness and exit.
if sudo rabbitmqctl ping >/dev/null 2>&1; then
  echo "start.sh: RabbitMQ already running."
  sudo rabbitmqctl await_startup >/dev/null 2>&1 || true
  exit 0
fi

# RabbitMQ runs as the 'rabbitmq' user, so its data/log dirs must be owned by it.
export RABBITMQ_LOG_BASE="${RABBITMQ_LOG_BASE:-/tmp/rmq/log}"
export RABBITMQ_MNESIA_BASE="${RABBITMQ_MNESIA_BASE:-/tmp/rmq/mnesia}"
sudo mkdir -p "$RABBITMQ_LOG_BASE" "$RABBITMQ_MNESIA_BASE"
sudo chown -R rabbitmq:rabbitmq "$RABBITMQ_LOG_BASE" "$RABBITMQ_MNESIA_BASE"

echo "start.sh: starting RabbitMQ broker..."
sudo -E bash -c 'nohup rabbitmq-server > "${RABBITMQ_LOG_BASE}/server.out" 2>&1 &'

# Wait for readiness (await_startup blocks until the broker app is up).
for _ in $(seq 1 30); do
  if sudo rabbitmqctl await_startup >/dev/null 2>&1; then
    echo "start.sh: RabbitMQ is ready."
    exit 0
  fi
  sleep 2
done

echo "start.sh: RabbitMQ failed to become ready in time." >&2
sudo cat "${RABBITMQ_LOG_BASE}/server.out" 2>/dev/null | tail -30 >&2 || true
exit 1
