"""
HOOD Demo App - Local HTTP Server Entrypoint
Provides health check and user profile endpoints for live server testing.
"""

import sys
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from demo_app.app_service import get_user_profile, calculate_discount, calculate_tax


class DemoAppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "healthy", "service": "demo_app"}).encode("utf-8"))
        elif self.path.startswith("/user/"):
            try:
                uid = int(self.path.split("/")[-1])
                user = get_user_profile(uid)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(user).encode("utf-8"))
            except Exception as e:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(str(e).encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            html = "<html><body><h1>HOOD Demo App</h1><div id='status'>RUNNING</div></body></html>"
            self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        # Suppress noisy default logging
        sys.stderr.write(f"[DemoApp] {format % args}\n")


def run(port: int = 8899):
    server = HTTPServer(("127.0.0.1", port), DemoAppHandler)
    server.serve_forever()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8899
    run(port)
