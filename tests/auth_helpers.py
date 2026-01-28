import hashlib
import hmac
import time
from urllib.parse import urlencode


def build_internal_headers(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    user_id: str = "user_a",
    params=None,
    content_type: str | None = None,
    secret: bytes = b"test-secret",
):
    timestamp = str(int(time.time()))
    query = urlencode(params, doseq=True) if params else ""
    full_path = f"{path}?{query}" if query else path
    body_hash = hashlib.sha256(body).hexdigest()
    signature_payload = f"{timestamp}\n{method.upper()}\n{full_path}\n{body_hash}"
    signature = hmac.new(
        secret,
        signature_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Internal-Timestamp": timestamp,
        "X-Body-SHA256": body_hash,
        "X-Internal-Signature": signature,
        "X-User-Id": user_id,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers
