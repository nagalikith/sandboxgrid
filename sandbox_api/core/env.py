from __future__ import annotations

import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
CANVAS_ENV_FILE = ROOT_DIR / ".env.canvas-grading"


def load_repo_env() -> None:
    if not CANVAS_ENV_FILE.exists():
        return
    for raw_line in CANVAS_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {"'", '"'}:
            value = value[1:-1]
        if not key:
            continue
        current = os.environ.get(key)
        if current is None or current == "" or current.startswith("replace-with-"):
            os.environ[key] = value
