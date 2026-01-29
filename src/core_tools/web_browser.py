import asyncio
import requests
from crawl4ai import *

from ..config import JINA_TOKEN


# TOKEN = "[REDACTED]"



def jina_ai_reader(target_url: str, bearer_token: str = JINA_TOKEN, timeout: int = 1800) -> str:
    """
    Fetches the Jina AI reduced-content page for *target_url* with the required
    Bearer-token authorization header.

    Parameters
    ----------
    target_url : str
        The original article URL (with or without leading "http(s)://").
    bearer_token : str
        Your API access token, e.g. the "xxx" in the cURL example.
    timeout : int, optional
        Network timeout in seconds, by default 30.

    Returns
    -------
    str
        The text returned by https://r.jina.ai/…
    """
    # Ensure the article URL is fully-qualified
    # if not target_url.startswith(("http://", "https://")):
    #     target_url = "https://" + target_url

    # Jina AI expects the raw URL appended directly after its endpoint
    jina_api_url = f"https://r.jina.ai/{target_url}"

    # Compose the required Authorization header
    headers = {"Authorization": f"Bearer {bearer_token}",
               "X-Respond-With": "readerlm-v2"}

    # Perform the request
    response = requests.get(jina_api_url, headers=headers, timeout=timeout)
    response.raise_for_status()

    return response.text