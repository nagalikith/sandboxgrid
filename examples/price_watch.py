"""Price-intelligence demo against a bot-walled storefront.

Each "session" provisions a sandbox (mock or Browserbase), hits the bot wall,
solves the proof-of-work challenge, and scrapes prices — repeated across N
sessions to show parallelism. Fully offline via the mock client; point it at
real storefronts by swapping the httpx transport for Playwright over the
sandbox's CDP URL.

Usage:
    python examples/price_watch.py [--sessions 3]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Dict, List

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.mock_storefront.app import create_storefront, solve_challenge  # noqa: E402
from sandbox_api.sandboxes.browserbase import MockBrowserbaseClient  # noqa: E402
from sandbox_api.sandboxes.provisioner import BrowserbaseProvisioner  # noqa: E402


async def watch_once(provisioner: BrowserbaseProvisioner, app) -> Dict[str, dict]:
    session = await provisioner._client.create_session(proxies=True)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://store.local") as client:
            blocked = await client.get("/prices")
            assert blocked.status_code == 403, "bot wall should block first"

            nonce = (await client.get("/challenge")).json()["nonce"]
            answer = solve_challenge(nonce)
            verified = await client.post(f"/challenge/verify/{nonce}/{answer}")
            verified.raise_for_status()

            page = await client.get("/prices")
            page.raise_for_status()
            return page.json()["prices"]
    finally:
        await provisioner._client.release_session(session.id)


async def watch_all(count: int) -> List[Dict[str, dict]]:
    app = create_storefront()
    provisioner = BrowserbaseProvisioner(client=MockBrowserbaseClient())
    started = time.monotonic()
    results = await asyncio.gather(*(watch_once(provisioner, app) for _ in range(count)))
    wall = time.monotonic() - started
    for i, prices in enumerate(results, 1):
        summary = ", ".join(f"{v['name']}=${v['price_usd']}" for v in prices.values())
        print(f"session {i}: {summary}")
    print(f"\n{count} parallel sessions completed in {wall:.2f}s")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(watch_all(args.sessions))
