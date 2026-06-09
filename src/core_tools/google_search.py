import re
import httplib2
from googleapiclient.discovery import build
from urllib.parse import urlparse
from typing import Any, List, Dict, Tuple

from ..config import proxy_host, proxy_port, DOCKER_MODE


def _build_http():
    """Build httplib2.Http with proxy only when not in Docker."""
    if DOCKER_MODE:
        return httplib2.Http()
    proxy_info = httplib2.ProxyInfo(
        httplib2.socks.PROXY_TYPE_HTTP,
        proxy_host,
        proxy_port,
    )
    return httplib2.Http(proxy_info=proxy_info)


def google_search(search_term: str, api_key: str, cse_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
    http = _build_http()
    service = build("customsearch", "v1", http=http, developerKey=api_key)
    res = service.cse().list(q=search_term, cx=cse_id, **kwargs).execute()
    return res.get('items', [])


def clean_text(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
    text = re.sub(r'\xa0', ' ', text)  # Replace non-breaking spaces
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    return text


def get_source(link: str) -> str:
    parsed = urlparse(link)
    return parsed.netloc + parsed.path


# Function to process and format results
def format_for_llm(results: List[Dict[str, Any]], search_term: str) -> str:
    output: List[str] = [f"**Search Results for \"{search_term}\"**", ""]

    for i, result in enumerate(results, 1):
        title: str = clean_text(result.get('title', 'No title'))
        link: str = result.get('link', 'No link')
        snippet: str = clean_text(result.get('snippet', 'No summary available'))
        source: str = get_source(link)

        entry: List[str] = [
            f"{i}. **Title:** {title}",
            f"   **Source:** {source}",
            f"   **Summary:** {snippet}",
            ""
        ]
        output.extend(entry)

    return "\n".join(output)

def _get_google_config():
    """Read Google Search API key and CSE ID from environment variables.

    The gateway (``gateway.provider_registry._sync_tool_env_vars``) pushes
    these into the environment on startup and after every settings save.
    """
    import os
    api_key = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
    cse_id = os.environ.get("GOOGLE_CSE_ID", "")
    return api_key, cse_id


def search_for_llm(arguments: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    search_term = arguments["search_term"]
    my_api_key, my_cse_id = _get_google_config()
    if not my_api_key or not my_cse_id:
        return (
            "Google Search is not configured. "
            "Set GOOGLE_SEARCH_API_KEY and GOOGLE_CSE_ID in Settings "
            "or via environment variables."
        ), arguments
    results: List[Dict[str, Any]] = google_search(search_term, my_api_key, my_cse_id, num=10)

    if not results:
        return f"Google search for \"{search_term}\" did not find any results.", arguments

    # Format results for LLM
    formatted_output: str = format_for_llm(results, search_term)
    return formatted_output, arguments


# Example usage
if __name__ == "__main__":
    search_term = "when is 5060 released?"

    print(search_for_llm(search_term))
