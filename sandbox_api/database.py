import os
from pathlib import Path
from typing import Dict, Optional, Set

from sqlalchemy import inspect
from sqlmodel import SQLModel, create_engine

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./sandbox.db")
BASE_DIR = Path(__file__).resolve().parent.parent
ALEMBIC_INI = Path(os.getenv("ALEMBIC_INI", BASE_DIR / "alembic.ini"))

_connect_args: Optional[Dict] = None
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args or {})


def _existing_tables() -> Set[str]:
    try:
        return set(inspect(engine).get_table_names())
    except Exception:  # noqa: BLE001
        return set()


def init_db() -> None:
    """Create or migrate the database schema."""
    if not ALEMBIC_INI.exists():
        SQLModel.metadata.create_all(engine)
        return

    from alembic import command
    from alembic.config import Config

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)

    tables = _existing_tables()
    if tables and "alembic_version" not in tables:
        command.stamp(config, "head")
        return

    command.upgrade(config, "head")
