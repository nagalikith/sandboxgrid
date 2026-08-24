"""A tiny "retail site with a bot wall" for the stealth demo.

Unauthenticated scrapers get a 403 bot-wall page. Agents must fetch a nonce
from /challenge, find a proof-of-work suffix locally (sha256(nonce+answer)
starting with "0000"), and submit both to /challenge/verify — which issues an
anti-bot cookie on success. Only then does /prices return data. Lets the
price-watch demo show bot-wall handling deterministically, offline.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Dict, Optional, Set

from fastapi import Cookie, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

PREFIX = "0000"


def proof_of_work(nonce: str) -> Optional[str]:
    """Validate an answer for a nonce; return the matching suffix or None."""
    # answers are small ints in this demo; callers iterate candidates
    counter = 0
    while counter < 10_000_000:
        candidate = f"{nonce}{counter}"
        if hashlib.sha256(candidate.encode()).hexdigest().startswith(PREFIX):
            return candidate[len(nonce):]
        counter += 1
    return None


def solve_challenge(nonce: str) -> str:
    """Client-side helper: find a valid answer for the nonce."""
    counter = 0
    while True:
        candidate = f"{nonce}{counter}"
        if hashlib.sha256(candidate.encode()).hexdigest().startswith(PREFIX):
            return str(counter)
        counter += 1


def create_storefront() -> FastAPI:
    app = FastAPI(title="Demo Storefront")
    issued: Set[str] = set()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Response:
        return HTMLResponse(
            "<h1>Demo Storefront</h1><p>Automated traffic is blocked."
            ' Solve <a href="/challenge">the challenge</a> first.</p>'
        )

    @app.get("/challenge")
    async def challenge() -> JSONResponse:
        return JSONResponse({"nonce": secrets.token_hex(8), "prefix": PREFIX})

    @app.post("/challenge/verify/{nonce}/{answer}")
    async def verify(nonce: str, answer: str) -> Response:
        candidate = f"{nonce}{answer}"
        if not hashlib.sha256(candidate.encode()).hexdigest().startswith(PREFIX):
            return JSONResponse(status_code=403, content={"error": "bad proof of work"})
        from examples.mock_storefront.app import issue_token  # local helper

        token = issue_token(candidate)
        issued.add(token)
        response = JSONResponse({"ok": True})
        response.set_cookie("antibot", token)
        return response

    @app.get("/prices")
    async def prices(antibot: Optional[str] = Cookie(None)) -> Response:
        if not antibot or antibot not in issued:
            return JSONResponse(
                status_code=403,
                content={"error": "automated access blocked", "hint": "solve /challenge"},
            )
        return JSONResponse(
            {
                "prices": {
                    "SKU-1": {"name": "Wireless Mouse", "price_usd": 24.99},
                    "SKU-2": {"name": "Mechanical Keyboard", "price_usd": 89.0},
                    "SKU-3": {"name": "USB-C Hub", "price_usd": 42.5},
                }
            }
        )

    return app


_SECRET = b"storefront-demo-secret"


def issue_token(candidate: str) -> str:
    import hmac as _hmac

    return _hmac.new(_SECRET, candidate.encode(), hashlib.sha256).hexdigest()


app = create_storefront()
