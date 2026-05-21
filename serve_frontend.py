"""Tiny SPA-aware static file server for the built frontend.

Serves frontend/dist/ on the port given by FRONTEND_PORT (default 3000).
All non-file routes fall back to index.html so client-side routing works.
"""

import http.server
import os

PORT = int(os.environ.get("FRONTEND_PORT", "3000"))
DIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")


class SPAHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIST_DIR, **kwargs)

    def do_GET(self):
        file_path = os.path.join(DIST_DIR, self.path.lstrip("/"))
        if not os.path.isfile(file_path):
            self.path = "/index.html"
        return super().do_GET()


if __name__ == "__main__":
    print(f"[frontend] Serving {DIST_DIR} on 0.0.0.0:{PORT}")
    http.server.HTTPServer(("0.0.0.0", PORT), SPAHandler).serve_forever()
