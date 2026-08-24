from pathlib import Path

import pytest
from sqlmodel import create_engine

import sandbox_api.core.database as database


def test_init_db_without_alembic(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "ALEMBIC_INI", tmp_path / "missing.ini")

    database.init_db()
    assert db_path.exists()


def test_init_db_with_alembic_stamp(monkeypatch, tmp_path):
    pytest.importorskip("alembic")
    db_path = tmp_path / "test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setattr(database, "engine", test_engine)
    alembic_ini = tmp_path / "alembic.ini"
    alembic_ini.write_text("[alembic]\n")
    monkeypatch.setattr(database, "ALEMBIC_INI", alembic_ini)

    calls = {"stamp": 0, "upgrade": 0}

    class DummyConfig:
        def __init__(self, _path):
            self.path = _path

        def set_main_option(self, *_args, **_kwargs):
            return None

    def fake_stamp(_config, _rev):
        calls["stamp"] += 1

    def fake_upgrade(_config, _rev):
        calls["upgrade"] += 1

    def fake_tables():
        return {"artifacts"}

    monkeypatch.setattr("alembic.config.Config", DummyConfig, raising=False)
    monkeypatch.setattr("alembic.command.stamp", fake_stamp, raising=False)
    monkeypatch.setattr("alembic.command.upgrade", fake_upgrade, raising=False)
    monkeypatch.setattr(database, "_existing_tables", fake_tables)

    database.init_db()
    assert calls["stamp"] == 1
    assert calls["upgrade"] == 0
