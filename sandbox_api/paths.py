from __future__ import annotations

import hashlib
import re
from pathlib import Path


def normalize_artifacts_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized in {"per-user", "user", "owner"}:
        return "per-user"
    if normalized in {"per-sandbox", "sandbox"}:
        return "per-sandbox"
    raise ValueError(f"Unsupported SANDBOX_ARTIFACTS_MODE: {mode}")


def owner_directory(owner_id: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", owner_id).strip("_")
    if not sanitized:
        sanitized = "user"
    digest = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:8]
    return f"{sanitized}-{digest}"


def resolve_artifacts_path(
    artifacts_root: Path,
    *,
    sandbox_id: str,
    owner_id: str,
    mode: str,
) -> Path:
    normalized_mode = normalize_artifacts_mode(mode)
    if normalized_mode == "per-user":
        return artifacts_root / "users" / owner_directory(owner_id)
    return artifacts_root / sandbox_id
