"""Sandbox API package."""

from importlib import import_module
from typing import Any

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        return import_module(".main", __name__).app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
