"""Offline smoke tests for the examples/ demo scripts."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
sys.path.insert(0, str(EXAMPLES.parent))


@pytest.mark.asyncio
async def test_vendor_fanout_smoke(capsys):
    from examples.vendor_fanout import fan_out

    results = await fan_out(3)
    assert len(results) == 3
    for r in results:
        assert r.statuses, f"{r.portal} returned no invoice statuses"
        assert r.pdfs_downloaded == len(r.statuses)
        assert all(s in {"PAID", "PROCESSING", "OVERDUE", "DISPUTED"} for s in r.statuses.values())
    out = capsys.readouterr().out
    assert "parallel portals" in out


@pytest.mark.asyncio
async def test_price_watch_smoke(capsys):
    from examples.price_watch import watch_all

    results = await watch_all(2)
    assert len(results) == 2
    for prices in results:
        assert set(prices) == {"SKU-1", "SKU-2", "SKU-3"}
        assert all("price_usd" in p for p in prices.values())
    out = capsys.readouterr().out
    assert "parallel sessions" in out


def test_storefront_blocks_without_challenge():
    from fastapi.testclient import TestClient

    from examples.mock_storefront.app import create_storefront

    with TestClient(create_storefront()) as client:
        assert client.get("/prices").status_code == 403


def test_vendor_portal_login_flow():
    from fastapi.testclient import TestClient

    from examples.mock_vendor_portal.app import create_portal

    with TestClient(create_portal()) as client:
        login = client.post(
            "/login",
            data={"username": "supplier", "password": "hunter2"},
            follow_redirects=True,
        )
        assert login.status_code == 200
        page = client.get("/invoices")
        assert "INV-001" in page.text
        pdf = client.get("/invoices/INV-001.pdf")
        assert pdf.content.startswith(b"%PDF")
