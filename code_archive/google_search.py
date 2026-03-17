import re
import httplib2
from googleapiclient.discovery import build
from urllib.parse import urlparse
# Define proxy details
proxy_host = 'localhost'
proxy_port = 10792

# Create ProxyInfo object
proxy_info = httplib2.ProxyInfo(
    httplib2.socks.PROXY_TYPE_HTTP,
    proxy_host,
    proxy_port,
)

# Create Http object with proxy
http = httplib2.Http(proxy_info=proxy_info)

def google_search(search_term, api_key, cse_id, **kwargs):
    service = build("customsearch", "v1", http=http, developerKey=api_key)
    res = service.cse().list(q=search_term, cx=cse_id, **kwargs).execute()
    return res['items']


def clean_text(text):
    text = re.sub(r'<[^>]+>', '', text)  # Remove HTML tags
    text = re.sub(r'\xa0', ' ', text)  # Replace non-breaking spaces
    text = re.sub(r'\s+', ' ', text).strip()  # Normalize whitespace
    return text


def get_source(link):
    parsed = urlparse(link)
    return parsed.netloc + parsed.path


# Function to process and format results
def format_for_llm(results, search_term):
    output = [f"**Search Results for \"{search_term}\"**", ""]

    for i, result in enumerate(results, 1):
        title = clean_text(result.get('title', 'No title'))
        link = result.get('link', 'No link')
        snippet = clean_text(result.get('snippet', 'No summary available'))
        source = get_source(link)

        entry = [
            f"{i}. **Title:** {title}",
            f"   **Source:** {source}",
            f"   **Summary:** {snippet}",
            ""
        ]
        output.extend(entry)

    return "\n".join(output)

def search_for_llm(search_term):
    my_api_key = "[REDACTED]"
    my_cse_id = "[REDACTED]"
    results = google_search(search_term, my_api_key, my_cse_id, num=10)
    # Format results for LLM
    formatted_output = format_for_llm(results, search_term)
    return formatted_output


# Example usage
if __name__ == "__main__":
    search_term = "when is 5060 released?"

    print(search_for_llm(search_term))

