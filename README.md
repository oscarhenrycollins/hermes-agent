# Hermes Agent

The self-improving AI agent by Nous Research.

A [Wizard app](https://www.aiwizards.com/wizard-apps): install it from the Wizard Apps screen, or with the Wizard App Manager on any box running stock [Runtipi](https://runtipi.io).

- `wizard.md`: what the app is and what it gives the agent (spec: wizard.md v0.2).
- `runtipi/`: stock Runtipi `config.json` and `docker-compose.yml`.
- `mcp/`: the app's MCP front (`front.py` + `tools.py`), inlined into the compose file by `python3 mcp/inline.py`. It serves `/mcp` and passes everything else unchanged to the upstream container.
- `image/`: the Wizard build (official image + pinned Claude Code + NousResearch's Claude Subscription DirectSDK plugin), so Hermes can use the Claude subscription connected in AI Accounts.
- `SKILL.md`: the skill the agent loads when this app is installed.
- `.github/workflows/publish.yml`: every push to `main` re-publishes to the index at apps.aiwizards.com.


## Browser boundary (draft, default-off)

**Proposed** second reviewed separate-origin exception (wizard-os #386), awaiting Miguel's approval; File Browser is
the first. Hermes itself is not patched. With the browser boundary on (`WIZARD_BROWSER_BOUNDARY=1`, written by the App
Manager together with `WIZARD_OWN_HOSTS` only once `hermes-agent` is on its reviewed own-tab list), the dashboard runs
on its **own origin** (port 9119) with **its own login** (dashboard password and signed session cookie) and opens only
as its own tab; Wizard never frames it and never hands it the shell token. The front then refuses, before the
dashboard sees them: frame/embed requests, opaque or foreign `Origin` (WebSocket upgrades included), cross-site and
same-site requests (other Wizard addresses on the same host are same-site, and the dashboard's `SameSite=Lax` session
cookie rides along) other than a plain top-level GET navigation, and with `WIZARD_OWN_HOSTS` any request whose `Host`
is not the dashboard's own address. Replies carry `X-Frame-Options: DENY`, `frame-ancestors 'none'`, CORP and COOP
`same-origin`. The agent's `/mcp` (bearer, no browser headers) is unchanged. Off, nothing changes.

Residual (not fixed here): the engine also joins the Runtipi network (`add_to_main_network`), so other containers can
reach the dashboard and API directly; they still need the dashboard login / API key. Network isolation is card 5.

Checks: `python3 -m unittest discover -s tests -v`, `python3 mcp/inline.py --check`, and `wam check .`.
