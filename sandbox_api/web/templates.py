from __future__ import annotations

from pathlib import Path


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def template_path(name: str) -> Path:
    return TEMPLATES_DIR / name


def load_template_text(name: str) -> str:
    return template_path(name).read_text(encoding="utf-8")
