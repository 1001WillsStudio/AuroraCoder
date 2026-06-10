"""
Web content fetching tool — inspired by Claude Code's WebFetchTool.

Self-hosted HTTP fetch → HTML-to-Markdown → secondary-model summarization.
No external API dependency (replaces the old Jina AI reader approach).

Like Claude Code, uses a small/cheap model to process raw page content so only
a concise summary enters the main agent's context window.
"""

import os
import time
import logging
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, urljoin
from collections import OrderedDict

import requests
from markdownify import markdownify as md
from bs4 import BeautifulSoup
from openai import OpenAI

from ..config import (
    DOCKER_MODE, proxy_host, proxy_port,
    WEB_MAX_MARKDOWN_LENGTH, WEB_FETCH_TIMEOUT_S,
    WEB_CACHE_MAX_ENTRIES, WEB_CACHE_TTL_S,
    MODEL_PROVIDERS, DEFAULT_PROVIDER,
)


def _get_web_secondary_config():
    """Read web secondary model config from environment variables.

    Falls back to the default provider config when env vars are not set.
    """
    default_prov = MODEL_PROVIDERS.get(DEFAULT_PROVIDER, {})
    return {
        "base_url": os.environ.get("WEB_SECONDARY_BASE_URL")
                     or default_prov.get("base_url", ""),
        "api_key": os.environ.get("WEB_SECONDARY_API_KEY")
                    or os.environ.get("DEEPSEEK_API_KEY", ""),
        "model_name": os.environ.get("WEB_SECONDARY_MODEL")
                       or default_prov.get("model", ""),
        "max_tokens": int(os.environ.get("WEB_SECONDARY_MAX_TOKENS", "4096")),
    }

logger = logging.getLogger(__name__)

MAX_URL_LENGTH = 2000
MAX_HTTP_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB
MAX_REDIRECTS = 10

USER_AGENT = (
    "Mozilla/5.0 (compatible; ThinkWithTool/1.0; "
    "+https://github.com/ThinkWithTool)"
)


class _TTLCache:
    """Simple LRU cache with per-entry TTL expiry."""

    def __init__(self, max_entries: int = WEB_CACHE_MAX_ENTRIES, ttl_s: float = WEB_CACHE_TTL_S):
        self._store: OrderedDict[str, tuple[float, Dict[str, Any]]] = OrderedDict()
        self._max = max_entries
        self._ttl = ttl_s

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, value = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: Dict[str, Any]) -> None:
        if key in self._store:
            del self._store[key]
        elif len(self._store) >= self._max:
            self._store.popitem(last=False)
        self._store[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._store.clear()


_url_cache = _TTLCache()


def _validate_url(url: str) -> str:
    """Validate and normalise the URL. Returns the cleaned URL or raises ValueError."""
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"URL exceeds {MAX_URL_LENGTH} character limit")

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https", ""):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme!r}")

    if not parsed.hostname or "." not in parsed.hostname:
        raise ValueError(f"Invalid hostname in URL: {url}")

    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are not supported")

    if parsed.scheme == "http":
        url = "https" + url[4:]

    if not url.startswith("https://"):
        url = "https://" + url

    return url


def _is_same_host_redirect(original: str, redirect: str) -> bool:
    """Allow redirects that stay on the same host (with or without www.)."""
    try:
        orig_host = urlparse(original).hostname or ""
        redir_host = urlparse(redirect).hostname or ""
        strip = lambda h: h.removeprefix("www.")
        return strip(orig_host) == strip(redir_host)
    except Exception:
        return False


def _get_proxies() -> Optional[Dict[str, str]]:
    if DOCKER_MODE:
        return None
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    return {"http": proxy_url, "https": proxy_url}


def _fetch_with_redirects(url: str, *, timeout: int = WEB_FETCH_TIMEOUT_S) -> requests.Response:
    """
    Fetch URL following only same-host redirects (safety measure from
    Claude Code: cross-host redirects are reported instead of followed).
    """
    session = requests.Session()
    session.max_redirects = 0
    proxies = _get_proxies()

    headers = {
        "Accept": "text/markdown, text/html, text/plain, */*",
        "User-Agent": USER_AGENT,
    }

    current_url = url
    for _ in range(MAX_REDIRECTS):
        try:
            resp = session.get(
                current_url,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                stream=False,
                proxies=proxies,
            )
        except requests.exceptions.ConnectionError as exc:
            raise ConnectionError(f"Could not connect to {current_url}: {exc}") from exc

        if resp.status_code not in (301, 302, 307, 308):
            return resp

        location = resp.headers.get("Location", "")
        if not location:
            raise ValueError("Redirect response missing Location header")

        redirect_url = urljoin(current_url, location)

        if not _is_same_host_redirect(url, redirect_url):
            resp.headers["X-Redirect-Url"] = redirect_url
            return resp

        current_url = redirect_url

    raise ValueError(f"Too many redirects (exceeded {MAX_REDIRECTS})")


def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean Markdown via BeautifulSoup + markdownify."""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(["script", "style", "noscript", "svg", "iframe", "img"]):
        tag.decompose()

    return md(str(soup), heading_style="ATX").strip()


def _summarize_with_secondary_model(markdown_content: str, prompt: str) -> str:
    """
    Use a cheap/fast secondary model to extract relevant information from
    the raw page content, so only a concise answer enters the main agent's
    context.
    """
    truncated = markdown_content
    if len(truncated) > WEB_MAX_MARKDOWN_LENGTH:
        truncated = truncated[:WEB_MAX_MARKDOWN_LENGTH] + "\n\n[Content truncated due to length...]"

    system_msg = (
        "You are a web content extraction assistant. "
        "You will be given the Markdown content of a web page and a user prompt. "
        "Your job is to extract and summarize the relevant information concisely. "
        "Include specific details, numbers, code examples, and key facts as needed. "
        "Do NOT add information that is not present in the page content."
    )

    user_msg = (
        f"Web page content:\n"
        f"---\n"
        f"{truncated}\n"
        f"---\n\n"
        f"{prompt}\n\n"
        f"Provide a concise, focused response based only on the content above."
    )

    sec_cfg = _get_web_secondary_config()
    try:
        client = OpenAI(
            base_url=sec_cfg["base_url"],
            api_key=sec_cfg["api_key"],
        )
        response = client.chat.completions.create(
            model=sec_cfg["model_name"],
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=sec_cfg["max_tokens"],
            temperature=0.1,
        )
        result = response.choices[0].message.content or ""
        return result.strip()
    except Exception as exc:
        logger.warning("Secondary model summarization failed: %s — returning raw content", exc)
        return truncated


def web_fetch(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    target_url = arguments["target_url"]
    prompt = arguments.get("prompt", "")
    """
    Fetch a URL, convert HTML to Markdown, and optionally summarize via
    a secondary model.

    Two modes:

    1. **With prompt** (recommended): The raw page is sent to a cheap secondary
       model along with the prompt. Only the concise summary enters the main
       agent's context. This dramatically reduces token usage.

    2. **Without prompt**: Returns the raw Markdown content directly (truncated
       to WEB_MAX_MARKDOWN_LENGTH). Use sparingly — full pages consume a lot of
       context.
    """
    try:
        url = _validate_url(target_url)
    except ValueError as exc:
        return f"Error: {exc}", arguments

    cache_key = f"{url}||{prompt}"
    cached = _url_cache.get(cache_key)
    if cached is not None:
        logger.debug("Cache hit for %s", cache_key)
        return cached["content"]

    try:
        resp = _fetch_with_redirects(url)
    except Exception as exc:
        return f"Error fetching URL: {exc}", arguments

    redirect_url = resp.headers.get("X-Redirect-Url")
    if redirect_url and resp.status_code in (301, 302, 307, 308):
        return (
            f"REDIRECT: The URL redirects to a different host.\n"
            f"Original: {url}\n"
            f"Redirect: {redirect_url}\n"
            f"Status: {resp.status_code}\n\n"
            f"Please call web_browser again with target_url=\"{redirect_url}\""
        ), arguments

    if resp.status_code >= 400:
        return f"HTTP {resp.status_code} — could not fetch {url}", arguments

    content_type = resp.headers.get("Content-Type", "")
    raw_bytes = len(resp.content)

    if raw_bytes > MAX_HTTP_CONTENT_LENGTH:
        return f"Error: Response too large ({raw_bytes:,} bytes, limit is {MAX_HTTP_CONTENT_LENGTH:,})", arguments

    if "text/html" in content_type:
        markdown_content = _html_to_markdown(resp.text)
    else:
        markdown_content = resp.text

    if prompt.strip():
        result_body = _summarize_with_secondary_model(markdown_content, prompt)
        header = (
            f"[Web content from {url} — summarized by secondary model]\n\n"
        )
    else:
        if len(markdown_content) > WEB_MAX_MARKDOWN_LENGTH:
            markdown_content = markdown_content[:WEB_MAX_MARKDOWN_LENGTH] + \
                "\n\n[Content truncated due to length...]"
        header = (
            f"URL: {url}\n"
            f"Status: {resp.status_code}\n"
            f"Content-Type: {content_type}\n"
            f"Size: {raw_bytes:,} bytes\n"
            f"---\n\n"
        )
        result_body = markdown_content

    result = header + result_body
    _url_cache.set(cache_key, {"content": result})

    return result, arguments
