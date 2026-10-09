API_PORT = int(os.environ.get("HERMES_API_PORT", "8642"))
API_KEY = os.environ.get("HERMES_API_KEY", "")


def _api(method, path, payload=None, timeout=900):
    url = "http://%s:%d%s" % (up_addr(), API_PORT, path)
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError("Hermes API %d: %s" % (e.code, e.read()[:500].decode("utf-8", "replace")))


@tool("hermes_status", "Check this Hermes Agent is running and which model it answers with.")
def hermes_status():
    health = _api("GET", "/health", timeout=15)
    models = [m.get("id") for m in _api("GET", "/v1/models", timeout=15).get("data") or []]
    return {"ok": True, "health": health, "models": models}


@tool("ask_hermes", "Give this Hermes Agent a task or question and return its final answer. "
      "It runs with its own tools, memory and skills, so treat it as delegating to another agent.",
      {"prompt": {"type": "string"},
       "session": {"type": "string", "description": "Optional conversation id to continue an earlier thread"}},
      ["prompt"])
def ask_hermes(prompt, session=None):
    headers_session = {"X-Hermes-Session-Id": str(session)} if session else {}
    url = "http://%s:%d/v1/chat/completions" % (up_addr(), API_PORT)
    body = json.dumps({"model": "hermes-agent", "messages": [{"role": "user", "content": str(prompt)}], "stream": False}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer " + API_KEY, "Content-Type": "application/json", **headers_session})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            d = json.loads(r.read())
            sid = r.headers.get("X-Hermes-Session-Id")
    except urllib.error.HTTPError as e:
        raise RuntimeError("Hermes API %d: %s" % (e.code, e.read()[:500].decode("utf-8", "replace")))
    choice = (d.get("choices") or [{}])[0]
    return {"answer": (choice.get("message") or {}).get("content", ""), "session": sid}
