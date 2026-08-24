"""A tiny in-process "vendor portal" for demos.

Simulates the login-walled invoice portals agents face in the wild:
form login -> session cookie -> invoice table -> PDF downloads.
Run standalone with `uvicorn mock_vendor_portal.app:app` or import
`create_portal()` to spin up per-vendor instances inside a demo script.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from fastapi import Cookie, FastAPI, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse


@dataclass
class Invoice:
    id: str
    vendor: str
    amount: str
    status: str


def _token(username: str) -> str:
    raw = f"{username}:{int(time.time())}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _pdf(text: str) -> bytes:
    body = text.encode()
    return (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length "
        + str(len(body)).encode()
        + b">>stream\n"
        + body
        + b"\nendstream endobj\n5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF"
    )


_LOGIN_PAGE = """<!doctype html><html><body style="font-family:sans-serif">
<h1>{name} Supplier Portal</h1>
<form method="post" action="/login">
<input name="username" placeholder="user"/><input name="password" type="password" placeholder="pass"/>
<button type="submit">Sign in</button></form></body></html>"""

_INVOICES_PAGE = """<!doctype html><html><body style="font-family:sans-serif">
<h1>{name} — Invoices ({username})</h1>
<table border="1" cellpadding="6"><tr><th>ID</th><th>Amount</th><th>Status</th><th>PDF</th></tr>
{rows}</table></body></html>"""

_ROW = '<tr><td>{id}</td><td>{amount}</td><td>{status}</td><td><a href="/invoices/{id}.pdf">download</a></td></tr>'


def create_portal(
    *,
    name: str = "Acme",
    username: str = "supplier",
    password: str = "hunter2",
    invoices: Optional[List[Invoice]] = None,
) -> FastAPI:
    app = FastAPI(title=f"{name} Vendor Portal")
    invoices = invoices or [
        Invoice("INV-001", name, "$1,200.00", "PAID"),
        Invoice("INV-002", name, "$840.50", "PROCESSING"),
    ]
    sessions: Dict[str, str] = {}

    @app.get("/", response_class=HTMLResponse)
    async def index() -> Response:
        return HTMLResponse(_LOGIN_PAGE.format(name=name))

    @app.get("/login", response_class=HTMLResponse)
    async def login_form() -> Response:
        return HTMLResponse(_LOGIN_PAGE.format(name=name))

    @app.post("/login")
    async def login(username: str = Form(), password: str = Form()) -> Response:
        if username != username or password != password:  # pragma: no cover
            raise HTTPException(401)
        token = _token(username)
        sessions[token] = username
        response = RedirectResponse("/invoices", status_code=303)
        response.set_cookie("session", token)
        return response

    def _require(session_cookie: Optional[str]) -> str:
        if session_cookie not in sessions:
            raise HTTPException(403, "not authenticated")
        return sessions[session_cookie]

    @app.get("/invoices", response_class=HTMLResponse)
    async def list_invoices(session: Optional[str] = Cookie(None)) -> Response:
        user = _require(session)
        rows = "\n".join(
            _ROW.format(id=inv.id, amount=inv.amount, status=inv.status) for inv in invoices
        )
        return HTMLResponse(_INVOICES_PAGE.format(name=name, username=user, rows=rows))

    @app.get("/invoices/{invoice_id}.pdf")
    async def invoice_pdf(invoice_id: str, session: Optional[str] = Cookie(None)) -> Response:
        _require(session)
        for inv in invoices:
            if inv.id == invoice_id:
                return Response(
                    content=_pdf(f"{inv.id} {inv.vendor} {inv.amount} {inv.status}"),
                    media_type="application/pdf",
                )
        raise HTTPException(404)

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"ok": "1"}

    return app


app = create_portal()
