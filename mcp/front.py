"""Wizard MCP front: serves this app's MCP tools at /mcp and passes every other request,
byte for byte, to the app behind it (HTTP, streaming and WebSockets alike).
Stdlib only, so it runs on a stock python image with no build step.
Generated from the app repo's mcp/ folder; edit there, then run mcp/inline.py."""
import asyncio
import contextvars
import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request
import uuid

APP = os.environ.get("WIZARD_APP", "app")
VERSION = os.environ.get("APP_VERSION", "0")
PORT = int(os.environ.get("FRONT_PORT", "8080"))
UP_HOST = os.environ.get("UPSTREAM_HOST", "")
UP_PORT = int(os.environ.get("UPSTREAM_PORT", str(PORT)))
MCP_PATH = "/mcp"
MCP_TOKEN = os.environ.get("MCP_TOKEN", "")  # when set, /mcp needs Authorization: Bearer <token>
MAX_HEAD = 65536
MAX_BODY = 1 << 20
TOOLS = {}
REQUEST = contextvars.ContextVar("request", default={})  # headers of the current /mcp call


def tool(name, description, props=None, required=()):
    def deco(fn):
        schema = {"type": "object", "properties": props or {}, "required": list(required)}
        TOOLS[name] = ({"name": name, "description": description, "inputSchema": schema}, fn)
        return fn
    return deco


def up_addr():
    try:
        return socket.gethostbyname(UP_HOST)
    except OSError:
        return UP_HOST


def http(method, path, body=None, headers=None, timeout=60):
    url = "http://%s:%d%s" % (up_addr(), UP_PORT, path)
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def err(i, code, message):
    return {"jsonrpc": "2.0", "id": i, "error": {"code": code, "message": message}}


def rpc(msg):
    m, i, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if m == "initialize":
        r = {"protocolVersion": p.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
             "serverInfo": {"name": APP, "version": VERSION}}
    elif m == "tools/list":
        r = {"tools": [t[0] for t in TOOLS.values()]}
    elif m == "tools/call":
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in TOOLS:
            return err(i, -32602, "unknown tool %s" % name)
        try:
            out = TOOLS[name][1](**args)
            if isinstance(out, dict) and "_content" in out:
                r = {"content": out["_content"]}
            else:
                text = out if isinstance(out, str) else json.dumps(out, indent=2)
                r = {"content": [{"type": "text", "text": text[:200000]}]}
        except Exception as e:  # tool errors go back to the agent, not the transport
            r = {"content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}], "isError": True}
    elif m == "ping":
        r = {}
    elif i is None:
        return None  # notification
    else:
        return err(i, -32601, "unknown method %s" % m)
    return {"jsonrpc": "2.0", "id": i, "result": r}


REASONS = {200: "OK", 202: "Accepted", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed",
           401: "Unauthorized", 413: "Payload Too Large", 502: "Bad Gateway"}


async def respond(writer, code, obj=None, extra=""):
    body = b"" if obj is None else json.dumps(obj).encode()
    head = "HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n%s\r\n" % (
        code, REASONS.get(code, "OK"), len(body), extra)
    writer.write(head.encode() + body)
    try:
        await writer.drain()
    finally:
        writer.close()


# Browser boundary (wizard-os issues 384/386; default-off). PROPOSED second reviewed separate-origin exception, awaiting
# Miguel's approval: the Hermes dashboard keeps its OWN login (its password and signed session cookie) and runs on its
# OWN origin (its own HTTPS port), opened only as its own tab and never framed in a Wizard window. Hermes itself is
# unchanged. With WIZARD_BROWSER_BOUNDARY=1 this front refuses, before the dashboard sees anything:
#   - any frame/embed request (Sec-Fetch-Dest iframe, frame, embed, object, fencedframe);
#   - a foreign or opaque Origin (`null` from a sandboxed app frame included), WebSocket upgrades too;
#   - cross-site and same-site requests (other Wizard origins on the same host are same-site, and the dashboard's
#     SameSite=Lax session cookie rides along with those), except a plain top-level GET/HEAD navigation (the shell
#     opening the tab; it cannot read the page);
#   - with WIZARD_OWN_HOSTS set, any browser/network request whose Host is not the dashboard's own authority.
# Every response then says DENY to framing (X-Frame-Options + CSP frame-ancestors 'none') and same-origin to
# cross-origin readers (CORP) and openers (COOP). Box callers (no browser or network headers, e.g. the agent's /mcp)
# are unchanged. Off (unset), nothing here runs and responses are byte for byte as before.
BOUNDARY = os.environ.get("WIZARD_BROWSER_BOUNDARY") == "1"
OWN_HOSTS = {x.strip().lower() for x in os.environ.get("WIZARD_OWN_HOSTS", "").split(",") if x.strip()}
FRAME_DESTS = {"iframe", "frame", "embed", "object", "fencedframe"}
BROWSER_HEADERS = {"origin", "cookie", "referer", "sec-fetch-site", "sec-fetch-dest", "sec-fetch-user"}
OWN_TAB_HEADERS = ("X-Frame-Options: DENY\r\nContent-Security-Policy: frame-ancestors 'none'\r\n"
                   "Cross-Origin-Resource-Policy: same-origin\r\nCross-Origin-Opener-Policy: same-origin\r\n")


def header_pairs(rest):
    out = []
    for line in rest.decode("latin-1").split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            out.append((k.strip().lower(), v.strip()))
    return out


def networked(pairs):
    """The request came through Tailscale Serve, a tunnel or a proxy (not from a process on this box)."""
    return any(k.startswith(("tailscale-", "x-forwarded-", "cf-")) or k in ("forwarded", "x-real-ip") for k, _ in pairs)


def own_tab_refusal(method, pairs):
    """Why a request must not reach the dashboard under the boundary, or None. Box callers pass."""
    if not BOUNDARY:
        return None
    if not networked(pairs) and not any(k in BROWSER_HEADERS for k, _ in pairs):
        return None
    h = dict(pairs)
    host = h.get("host", "").lower()
    if OWN_HOSTS and host not in OWN_HOSTS:
        return "Hermes opens in its own tab, not at this address. Open it from your Wizard."
    dest, site, mode = h.get("sec-fetch-dest", ""), h.get("sec-fetch-site", ""), h.get("sec-fetch-mode", "")
    if dest in FRAME_DESTS:
        return "Hermes never opens inside another page. Open it from your Wizard."
    origin = h.get("origin")
    if origin is not None and (origin == "null" or urllib.parse.urlsplit(origin).netloc.lower() != host):
        return "Hermes answers only its own page."
    if site in ("cross-site", "same-site") and not (mode == "navigate" and dest == "document" and method in ("GET", "HEAD")):
        return "Hermes answers only its own page."
    return None


async def mcp(reader, writer, method, rest):
    headers = {}
    for line in rest.decode("latin-1").split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    if MCP_TOKEN:
        import hmac
        if not hmac.compare_digest(headers.get("authorization", "").encode(), ("Bearer " + MCP_TOKEN).encode()):
            return await respond(writer, 401, {"error": "this MCP needs Authorization: Bearer <token>"}, 'WWW-Authenticate: Bearer\r\n')
    if method != "POST":
        return await respond(writer, 405, {"error": "use POST %s" % MCP_PATH}, "Allow: POST\r\n")
    n = int(headers.get("content-length") or 0)
    if n > MAX_BODY:
        return await respond(writer, 413, {"error": "request too large"})
    body = await reader.readexactly(n) if n else b""
    try:
        msg = json.loads(body or b"{}")
    except ValueError:
        return await respond(writer, 400, err(None, -32700, "parse error"))
    REQUEST.set(headers)
    batch = msg if isinstance(msg, list) else [msg]
    results = await asyncio.gather(*(asyncio.to_thread(rpc, x) for x in batch if isinstance(x, dict)))
    out = [x for x in results if x]
    if not out:
        return await respond(writer, 202)
    await respond(writer, 200, out if isinstance(msg, list) else out[0])


async def pipe(r, w):
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def handle(reader, writer):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    line, _, rest = head.partition(b"\r\n")
    parts = line.decode("latin-1").split(" ")
    path = parts[1].split("?", 1)[0] if len(parts) >= 3 else ""
    refused = own_tab_refusal(parts[0] if parts else "", header_pairs(rest))
    if refused:
        return await respond(writer, 403, {"error": refused}, OWN_TAB_HEADERS)
    if path == MCP_PATH:
        return await mcp(reader, writer, parts[0], rest)
    if not UP_HOST:
        return await respond(writer, 404, {"error": "not found"})
    try:
        ur, uw = await asyncio.open_connection(UP_HOST, UP_PORT)
    except OSError:
        return await respond(writer, 502, {"error": "%s is starting or not reachable" % APP})
    # One request per proxied connection (unless it is an upgrade such as a WebSocket), so a
    # later /mcp request can never ride an upstream keep-alive connection.
    lines = head.decode("latin-1").split("\r\n")
    if not any(l.lower().startswith("upgrade:") for l in lines):
        lines = [l for l in lines if l and not l.lower().startswith(("connection:", "keep-alive:"))]
        head = ("\r\n".join(lines) + "\r\nConnection: close\r\n\r\n").encode("latin-1")
    uw.write(head)
    await uw.drain()
    if BOUNDARY:
        # Own-tab headers on the dashboard's reply head (one request per connection, or one upgrade, so this is its
        # only head). The request body keeps flowing meanwhile; the dashboard answers only after reading it.
        upload = asyncio.ensure_future(pipe(reader, uw))
        try:
            uhead = await ur.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            upload.cancel()
            writer.close()
            return
        keep = [l for l in uhead.decode("latin-1").split("\r\n") if l and not l.lower().startswith(
            ("x-frame-options:", "cross-origin-resource-policy:", "cross-origin-opener-policy:"))]
        writer.write(("\r\n".join(keep) + "\r\n" + OWN_TAB_HEADERS + "\r\n").encode("latin-1"))
        await asyncio.gather(upload, pipe(ur, writer))
        return
    await asyncio.gather(pipe(reader, uw), pipe(ur, writer))


async def main():
    srv = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=MAX_HEAD)
    async with srv:
        await srv.serve_forever()
