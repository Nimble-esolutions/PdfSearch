#!/usr/bin/env python3
"""Credential-free HTTP bridge into the disposable internal lifecycle network."""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os


TARGET_HOST = os.getenv("PROXY_TARGET_HOST", "web")
TARGET_PORT = int(os.getenv("PROXY_TARGET_PORT", "8000"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"connection", "content-length"}
        }
        connection = HTTPConnection(TARGET_HOST, TARGET_PORT, timeout=30)
        connection.request(self.command, self.path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in {
                "connection",
                "content-length",
                "transfer-encoding",
            }:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        connection.close()

    do_GET = _forward
    do_POST = _forward

    def log_message(self, *_args):
        return


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
