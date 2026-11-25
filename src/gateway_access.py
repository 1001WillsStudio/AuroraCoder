"""Authenticate the backend's existing loopback calls to its gateway."""

import hmac
import os
import re
from ipaddress import ip_address
from urllib.parse import unquote, urlsplit

import requests


HEADER = "X-Aurora-Gateway-Auth"
_EXACT_ROUTES = {
    ("POST", "/api/chat"),
    ("GET", "/api/memory/stance"),
    ("GET", "/api/memory/recall"),
    ("POST", "/api/memory/gaps"),
}
_CHILD_CANCEL = re.compile(r"/api/conversations/[a-f0-9-]{36}/cancel\Z")
_FORGET = re.compile(r"/api/memory/[^/]+\Z")


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _signature(method: str, path: str, password: str) -> str | None:
    if not password:
        return None
    if ((method, path) not in _EXACT_ROUTES
            and not (method == "POST" and _CHILD_CANCEL.fullmatch(path))
            and not (method == "DELETE" and _FORGET.fullmatch(path))):
        return None
    message = f"auroracoder:gateway\n{method}\n{path}".encode("utf-8")
    return hmac.digest(password.encode("utf-8"), message, "sha256").hex()


def gateway_service_allowed(host: str, method: str, path: str, header: str, password: str) -> bool:
    if not header or not _is_loopback(host):
        return False
    expected = _signature(method, path, password)
    return expected is not None and hmac.compare_digest(header.encode("utf-8"), expected.encode("ascii"))


def gateway_tool_request(method: str, url: str, **kwargs) -> requests.Response:
    """Keep ordinary HTTP behavior unless this is an authenticated local tool call."""
    target = urlsplit(url)
    password = os.environ.get("ACCESS_PASSWORD", "").strip()
    signature = None
    if target.scheme in ("http", "https") and target.username is None and _is_loopback(target.hostname or ""):
        signature = _signature(method, unquote(target.path), password)
    if signature is None:
        return requests.request(method, url, **kwargs)

    # Keep the internal header on the stated loopback endpoint, even when
    # the host environment supplies an HTTP proxy or the server redirects.
    with requests.Session() as session:
        session.trust_env = False
        return session.request(method, url, headers={HEADER: signature}, allow_redirects=False, **kwargs)
