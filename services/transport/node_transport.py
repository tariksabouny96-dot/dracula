"""
HOOD Node Transport & Multi-Process Communication Layer
Provides authenticated, message-integrity-protected node-to-node transport.
Supports local multi-process sockets/HTTP on localhost without exposing HOOD to the Internet.
Governed by Master System Specification Sections 2, 6 & V0.5A Transport Spec.
"""

import sys
import json
import time
import socket
import threading
from typing import Dict, Any, Optional, Callable
from urllib.request import Request, urlopen
from http.server import HTTPServer, BaseHTTPRequestHandler

from services.nodes.contracts import DistributedTaskEnvelope, RemoteApprovalEvent, CryptographicNodeIdentity
from services.nodes.manager import NodeManager


class NodeTransportServer:
    """Listens on local port for incoming authenticated node envelopes and events."""

    def __init__(self, port: int, node_id: str, node_manager: NodeManager, on_envelope: Callable[[DistributedTaskEnvelope], Dict[str, Any]]):
        self.port = port
        self.node_id = node_id
        self.node_manager = node_manager
        self.on_envelope = on_envelope
        self.httpd = None
        self.thread = None

        class Handler(BaseHTTPRequestHandler):
            transport = self

            def do_POST(self):
                content_len = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_len)
                try:
                    data = json.loads(body.decode("utf-8"))
                    envelope = DistributedTaskEnvelope(**data)

                    # Validate trust and cryptographic signature
                    if not self.transport.node_manager.verify_envelope(envelope):
                        self.send_response(403)
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Untrusted origin or invalid signature"}).encode("utf-8"))
                        return

                    # Execute handler
                    result = self.transport.on_envelope(envelope)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(result).encode("utf-8"))

                except Exception as e:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))

            def log_message(self, format, *args):
                pass

        self.handler_cls = Handler

    def start(self):
        self.httpd = HTTPServer(("127.0.0.1", self.port), self.handler_cls)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class NodeTransportClient:
    """Dispatches signed envelopes to remote nodes via local transport."""

    @staticmethod
    def send_envelope(target_port: int, envelope: DistributedTaskEnvelope, sender_identity: CryptographicNodeIdentity) -> Dict[str, Any]:
        # Sign digest before sending
        digest = envelope.compute_digest()
        envelope.signature_hex = sender_identity.sign(digest)

        url = f"http://127.0.0.1:{target_port}/"
        payload = envelope.model_dump()
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "HOOD-Node-Transport/0.5A"}
        )
        with urlopen(req, timeout=10.0) as resp:
            data = resp.read()
            return json.loads(data.decode("utf-8"))
