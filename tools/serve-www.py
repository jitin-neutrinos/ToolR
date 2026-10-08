#!/usr/bin/env python3
"""Static file server for the ToolR installer (toolr.jitinnair.com).

Same policy as neutrinos-designer's serve-www.py (proven against Cloudflare
edge caching): immutable artifacts (content-hash-named) cache forever,
everything mutable (install.sh, checksums) is no-store.

Usage: serve-www.py [directory] [port]
"""
import functools
import http.server
import os
import re
import socketserver
import sys

IMMUTABLE = re.compile(r"-[0-9a-f]{8,}\.(tar\.gz|tgz|zip|whl)$", re.I)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # serve the landing page at /
        if self.path in ("/", "/index.html"):
            self.path = "/landing/index.html"
        super().do_GET()

    def end_headers(self):
        name = os.path.basename(self.path.split("?")[0])
        if IMMUTABLE.search(name):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        pass


def main():
    directory = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
        "~/Work/toolr-www")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8030
    handler = functools.partial(Handler, directory=directory)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
        sys.stderr.write(f"serving {directory} on 127.0.0.1:{port} (immutable-cache policy)\n")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
