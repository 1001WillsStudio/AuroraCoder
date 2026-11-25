"""Check the Docker services needed to use the desktop chat."""

import sys
import time
from urllib.request import ProxyHandler, build_opener


SERVICES = {
    "backend": "http://127.0.0.1:8080/api/health",
    "gateway": "http://127.0.0.1:8081/health",
    "desktop": "http://127.0.0.1:3000/",
}


def wait_for_services(services: dict[str, str], timeout: float = 45.0) -> list[str]:
    pending = dict(services)
    deadline = time.monotonic() + timeout
    http = build_opener(ProxyHandler({}))
    while pending and time.monotonic() < deadline:
        for name, url in tuple(pending.items()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return list(pending)
            try:
                with http.open(url, timeout=min(remaining, 1.0)) as response:
                    if 200 <= response.status < 300:
                        pending.pop(name)
            except OSError:
                continue
        if pending:
            time.sleep(max(0, min(deadline - time.monotonic(), 0.25)))
    return list(pending)


if __name__ == "__main__":
    waiting = wait_for_services(SERVICES)
    if waiting:
        print("Timed out waiting for " + ", ".join(waiting) + ".", file=sys.stderr)
        sys.exit(1)
    print("Backend, gateway, and desktop are ready.")
