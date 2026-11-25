"""Authentication used on the private gateway-to-backend calls."""

import hashlib
import os
import secrets
from collections.abc import Mapping


HEADER = "X-Aurora-Backend-Auth"


def backend_auth_headers() -> dict[str, str]:
    password = os.environ.get("ACCESS_PASSWORD", "").strip()
    if not password:
        return {}
    material = b"auroracoder:backend:" + password.encode("utf-8")
    return {HEADER: hashlib.sha256(material).hexdigest()}


def backend_access_allowed(headers: Mapping[str, str]) -> bool:
    expected = backend_auth_headers().get(HEADER)
    if expected is None:
        return True
    supplied = headers.get(HEADER, "")
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("ascii"))
