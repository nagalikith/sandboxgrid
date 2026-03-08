from pathlib import Path

from sandbox_api.platform.core.paths import owner_directory, resolve_artifacts_path


def test_owner_directory_sanitizes_and_stable():
    owner_id = "User Name/Email"
    first = owner_directory(owner_id)
    second = owner_directory(owner_id)
    assert first == second
    assert " " not in first
    assert "/" not in first


def test_resolve_artifacts_path_modes(tmp_path):
    root = tmp_path / "artifacts"
    per_user = resolve_artifacts_path(root, sandbox_id="sbx_123", owner_id="user_a", mode="per-user")
    per_sandbox = resolve_artifacts_path(root, sandbox_id="sbx_123", owner_id="user_a", mode="per-sandbox")
    assert per_user == root / "users" / owner_directory("user_a")
    assert per_sandbox == root / "sbx_123"
