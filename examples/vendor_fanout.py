"""Vendor-portal fan-out demo.

Provisions N parallel sandbox sessions (one per vendor portal), each with its
own persistent context, logs in, scrapes invoice statuses, and downloads the
invoice PDFs — all concurrently, with wall-clock timing.

Runs 100% offline: uses MockBrowserbaseClient for provisioning and talks to
in-process mock portals over httpx's ASGI transport. Point it at real
portals by swapping the driver and setting BROWSERBASE_API_KEY.

Usage:
    python examples/vendor_fanout.py [--portals 3]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.mock_vendor_portal.app import Invoice, create_portal  # noqa: E402
from sandbox_api.sandboxes.browserbase import MockBrowserbaseClient  # noqa: E402
from sandbox_api.sandboxes.provisioner import BrowserbaseProvisioner  # noqa: E402


@dataclass
class Portal:
    name: str
    username: str
    password: str
    app: object


@dataclass
class FetchResult:
    portal: str
    statuses: Dict[str, str]
    pdfs_downloaded: int
    seconds: float


def build_portals(count: int) -> List[Portal]:
    catalogues = [
        [("INV-101", "$1,200.00", "PAID"), ("INV-102", "$840.50", "PROCESSING")],
        [("INV-201", "$15,000.00", "OVERDUE"), ("INV-202", "$2,410.00", "PAID")],
        [("INV-301", "$96.20", "PROCESSING"), ("INV-302", "$5,500.00", "DISPUTED")],
        [("INV-401", "$777.00", "PAID")],
    ]
    portals = []
    for i in range(count):
        invoices = [Invoice(id_, f"Vendor{i + 1}", amt, st) for id_, amt, st in catalogues[i % len(catalogues)]]
        portals.append(
            Portal(
                name=f"Vendor{i + 1}",
                username=f"user{i + 1}",
                password=f"pass{i + 1}",
                app=create_portal(name=f"Vendor{i + 1}", invoices=invoices),
            )
        )
    return portals


async def drive_portal(
    provisioner: BrowserbaseProvisioner,
    client: httpx.AsyncClient,
    portal: Portal,
    context_id: str,
) -> Tuple[str, FetchResult]:
    start = time.monotonic()
    session = await provisioner._client.create_session(context_id=context_id)
    try:
        response = await client.post(
            "/login",
            data={"username": portal.username, "password": portal.password},
            follow_redirects=True,
        )
        response.raise_for_status()
        page = await client.get("/invoices")
        statuses: Dict[str, str] = {}
        pdf_count = 0
        for line in page.text.splitlines():
            if 'href="/invoices/' in line and line.strip().startswith("<tr>"):
                inv_id = line.split('href="/invoices/')[1].split(".pdf")[0]
                status = line.split("<td>")[3].split("</td>")[0]
                statuses[inv_id] = status
                pdf = await client.get(f"/invoices/{inv_id}.pdf")
                assert pdf.content.startswith(b"%PDF"), "expected a PDF"
                pdf_count += 1
        elapsed = time.monotonic() - start
        return session.id, FetchResult(portal.name, statuses, pdf_count, elapsed)
    finally:
        await provisioner._client.release_session(session.id)


async def fan_out(portal_count: int) -> List[FetchResult]:
    portals = build_portals(portal_count)
    client_mock = MockBrowserbaseClient()
    provisioner = BrowserbaseProvisioner(client=client_mock)

    contexts = await asyncio.gather(*(client_mock.create_context() for _ in portals))

    started = time.monotonic()

    async def run_one(index: int) -> FetchResult:
        portal = portals[index]
        transport = httpx.ASGITransport(app=portal.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://portal.local") as client:
            _, result = await drive_portal(provisioner, client, portal, contexts[index])
            return result

    results = list(
        await asyncio.gather(*(run_one(i) for i in range(len(portals))))
    )
    wall = time.monotonic() - started

    print(f"\n{'Portal':<10} {'Invoices':<9} {'PDFs':<6} {'Seconds':<8}")
    print("-" * 36)
    for r in sorted(results, key=lambda x: x.portal):
        print(f"{r.portal:<10} {len(r.statuses):<9} {r.pdfs_downloaded:<6} {r.seconds:>6.2f}")
    print("-" * 36)
    print(f"wall clock for {len(results)} parallel portals: {wall:.2f}s")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portals", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(fan_out(args.portals))
