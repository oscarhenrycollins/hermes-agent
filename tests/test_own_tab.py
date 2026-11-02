"""Browser boundary, PROPOSED second reviewed separate-origin exception (wizard-os issue 386; awaiting Miguel's
approval): with WIZARD_BROWSER_BOUNDARY=1 the Hermes dashboard opens only as its own tab on its own origin, with its
own login. The real front process refuses frame, opaque/foreign-Origin (WebSocket upgrades included) and cross-/
same-site requests before the dashboard sees them, refuses any other Host when WIZARD_OWN_HOSTS is set, and marks
every reply DENY for framing. The agent's /mcp (bearer, box-local) is unchanged. Off, nothing changes. Synthetic
data; a stub dashboard records each request. Hermes itself is not modified."""
import http.client, http.server, json, os, socket, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HITS = []
OWN = "box.example.ts.net:9119"        # the dashboard's own origin (its own HTTPS port)
SHELL = "box.example.ts.net:8444"      # the Wizard (owner) origin on the same host: same-site, different origin
FRAME = [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "iframe")]
OPAQUE_FETCH = [("Origin", "null"), ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "cors"), ("Sec-Fetch-Dest", "empty")]
# Another Wizard origin on the same host: same-site, so the dashboard's SameSite=Lax session cookie is sent.
SAME_SITE_FETCH = [("Origin", "https://" + SHELL), ("Sec-Fetch-Site", "same-site"), ("Sec-Fetch-Mode", "cors"),
                   ("Sec-Fetch-Dest", "empty"), ("Cookie", "hermes_session_at=synthetic")]
SAME_SITE_NAV = [("Sec-Fetch-Site", "same-site"), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "document"), ("Sec-Fetch-User", "?1")]
OWN_PAGE = [("Origin", "https://" + OWN), ("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Mode", "cors"), ("Sec-Fetch-Dest", "empty"),
            ("Cookie", "hermes_session_at=synthetic")]
WS = [("Connection", "Upgrade"), ("Upgrade", "websocket"), ("Sec-WebSocket-Version", "13"),
      ("Sec-WebSocket-Key", "c3ludGhldGljLWtleS0xMjM0NQ=="), ("Sec-Fetch-Mode", "websocket"), ("Sec-Fetch-Dest", "websocket")]


class Engine(http.server.BaseHTTPRequestHandler):
    def reply(self):
        HITS.append((self.command, self.path))
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        body = b'{"sessions":[{"id":"synthetic"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = reply

    def log_message(self, format, *args):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Front:
    def __init__(self, tmp, up, **env):
        self.port = free_port()
        src = (ROOT / "mcp/front.py").read_text() + "\n\n" + (ROOT / "mcp/tools.py").read_text()
        f = Path(tmp) / ("front-%d.py" % self.port)
        f.write_text(src + '\n\nif __name__ == "__main__":\n    asyncio.run(main())\n')
        e = {k: v for k, v in os.environ.items() if not k.startswith(("WIZARD_", "MCP_", "HERMES_"))}
        e.update(WIZARD_APP="hermes-agent", FRONT_PORT=str(self.port), UPSTREAM_HOST="127.0.0.1", UPSTREAM_PORT=str(up),
                 PYTHONDONTWRITEBYTECODE="1", MCP_TOKEN="synthetic-mcp-token", **env)
        self.proc = subprocess.Popen([sys.executable, str(f)], env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), 0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("front did not start: " + self.proc.communicate(timeout=5)[0].decode()[-500:])

    def stop(self):
        self.proc.terminate()
        self.proc.communicate(timeout=10)


def serve():
    return [("X-Forwarded-For", "100.64.0.7"), ("X-Forwarded-Proto", "https"), ("Tailscale-User-Login", "owner@example.com")]


def req(front, method, path, headers=(), host=OWN, body=None):
    c = http.client.HTTPConnection("127.0.0.1", front.port, timeout=10)
    c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    c.putheader("Host", host)
    for k, v in headers:
        c.putheader(k, v)
    if body is not None:
        c.putheader("Content-Length", str(len(body)))
    c.endheaders(body)
    r = c.getresponse()
    data = r.read()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
    c.close()
    return out


class OwnTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Engine)
        threading.Thread(target=cls.engine.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        up = cls.engine.server_address[1]
        cls.off = Front(cls.tmp.name, up)
        cls.on = Front(cls.tmp.name, up, WIZARD_BROWSER_BOUNDARY="1")
        cls.pinned = Front(cls.tmp.name, up, WIZARD_BROWSER_BOUNDARY="1", WIZARD_OWN_HOSTS=OWN)

    @classmethod
    def tearDownClass(cls):
        for f in (cls.off, cls.on, cls.pinned):
            f.stop()
        cls.engine.shutdown()
        cls.tmp.cleanup()

    def setUp(self):
        HITS.clear()

    def test_off_is_unchanged(self):
        status, headers, _ = req(self.off, "GET", "/api/sessions", serve() + FRAME)
        self.assertEqual(status, 200)
        self.assertNotIn("x-frame-options", headers)
        status, _, _ = req(self.off, "POST", "/api/config", serve() + SAME_SITE_FETCH, body=b"{}")
        self.assertEqual(status, 200)
        self.assertEqual(HITS, [("GET", "/api/sessions"), ("POST", "/api/config")])

    def test_frames_refused_before_the_dashboard(self):
        for front in (self.on, self.pinned):
            HITS.clear()
            status, headers, body = req(front, "GET", "/", serve() + FRAME)
            self.assertEqual(status, 403)
            self.assertIn(b"never opens inside another page", body)
            self.assertEqual(headers.get("x-frame-options"), "DENY")
            self.assertEqual(headers.get("content-security-policy"), "frame-ancestors 'none'")
            self.assertEqual(HITS, [])

    def test_opaque_frame_fetch_and_simple_post_refused(self):
        for method, path, body in (("GET", "/api/sessions", None), ("POST", "/auth/password-login", b"{}"),
                                   ("PUT", "/api/config", b"{}")):
            status, _, _ = req(self.on, method, path, serve() + OPAQUE_FETCH, body=body)
            self.assertEqual(status, 403, (method, path))
        status, _, _ = req(self.on, "GET", "/assets/logo.png", serve() + [("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "no-cors"), ("Sec-Fetch-Dest", "image")])
        self.assertEqual(status, 403)
        self.assertEqual(HITS, [])

    def test_other_wizard_origin_on_same_host_refused_even_with_session_cookie(self):
        # SameSite=Lax sends the dashboard session cookie on same-site requests, so the front refuses them itself.
        status, _, _ = req(self.on, "GET", "/api/sessions", serve() + SAME_SITE_FETCH)
        self.assertEqual(status, 403)
        status, _, _ = req(self.on, "POST", "/api/config", serve() + SAME_SITE_FETCH, body=b"{}")
        self.assertEqual(status, 403)
        self.assertEqual(HITS, [])

    def test_websocket_from_another_origin_refused_own_page_passes(self):
        status, _, _ = req(self.pinned, "GET", "/api/ws", serve() + WS + [("Origin", "https://" + SHELL), ("Sec-Fetch-Site", "same-site")])
        self.assertEqual(status, 403)
        status, _, _ = req(self.pinned, "GET", "/api/ws", serve() + WS + [("Origin", "null"), ("Sec-Fetch-Site", "cross-site")])
        self.assertEqual(status, 403)
        self.assertEqual(HITS, [])
        status, headers, _ = req(self.pinned, "GET", "/api/ws", serve() + WS + [("Origin", "https://" + OWN), ("Sec-Fetch-Site", "same-origin")])
        self.assertEqual(status, 200)   # reached the (stub) dashboard, which decides the upgrade itself
        self.assertEqual(HITS, [("GET", "/api/ws")])

    def test_shell_opening_the_tab_is_a_plain_navigation(self):
        status, headers, _ = req(self.pinned, "GET", "/", serve() + SAME_SITE_NAV)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("x-frame-options"), "DENY")
        self.assertEqual(headers.get("content-security-policy"), "frame-ancestors 'none'")
        self.assertEqual(headers.get("cross-origin-opener-policy"), "same-origin")
        self.assertEqual(headers.get("cross-origin-resource-policy"), "same-origin")
        # A same-site POST navigation (CSRF form into the dashboard) is not a plain navigation.
        status, _, _ = req(self.pinned, "POST", "/auth/password-login", serve() + SAME_SITE_NAV, body=b"username=x")
        self.assertEqual(status, 403)
        self.assertEqual(HITS, [("GET", "/")])

    def test_own_page_keeps_working(self):
        status, headers, _ = req(self.pinned, "GET", "/api/sessions", serve() + OWN_PAGE)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("x-frame-options"), "DENY")
        status, _, _ = req(self.pinned, "POST", "/api/config", serve() + OWN_PAGE, body=b"x" * 200000)
        self.assertEqual(status, 200)
        self.assertEqual(HITS, [("GET", "/api/sessions"), ("POST", "/api/config")])

    def test_other_address_refused_when_pinned(self):
        status, _, body = req(self.pinned, "GET", "/", serve() + [("Sec-Fetch-Site", "none"), ("Sec-Fetch-Mode", "navigate"), ("Sec-Fetch-Dest", "document")], host=SHELL)
        self.assertEqual(status, 403)
        self.assertIn(b"own tab", body)
        self.assertEqual(HITS, [])

    def test_box_callers_unchanged(self):
        status, _, _ = req(self.pinned, "GET", "/api/status", [], host="hermes-agent:9119")
        self.assertEqual(status, 200)
        self.assertEqual(HITS, [("GET", "/api/status")])

    def test_agent_mcp_unchanged_but_never_from_a_page(self):
        rpc = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
        auth = [("Authorization", "Bearer synthetic-mcp-token"), ("Content-Type", "application/json")]
        status, _, body = req(self.pinned, "POST", "/mcp", auth, host="127.0.0.1:9119", body=rpc)
        self.assertEqual(status, 200)
        self.assertIn(b"ask_hermes", body)
        status, _, _ = req(self.on, "POST", "/mcp", OPAQUE_FETCH + [("Content-Type", "text/plain")], body=rpc)
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
