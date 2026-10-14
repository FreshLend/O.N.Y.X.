import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen, Request
from urllib.error import HTTPError


class _ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")

    def do_PUT(self):
        self._forward("PUT")

    def do_PATCH(self):
        self._forward("PATCH")

    def do_DELETE(self):
        self._forward("DELETE")

    def do_HEAD(self):
        self._forward("HEAD")

    def _forward(self, method):
        url = self.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else None

        skip = {"host", "proxy-connection", "connection", "content-length"}
        headers = {k: v for k, v in self.headers.items() if k.lower() not in skip}

        try:
            req = Request(url, data=body, headers=headers, method=method)
            with urlopen(req, timeout=10) as resp:
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except HTTPError as e:
            self.send_response(e.code)
            self.end_headers()
            try:
                self.wfile.write(e.read())
            except Exception:
                pass
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            try:
                self.wfile.write(str(e).encode())
            except Exception:
                pass

    def log_message(self, *args):
        pass


class TestProxy:
    def __init__(self, port=8888):
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        if self.server is not None:
            return True
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), _ProxyHandler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            return True
        except Exception:
            self.server = None
            self.thread = None
            return False

    def stop(self):
        if self.server is None:
            return
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        self.server = None
        self.thread = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"