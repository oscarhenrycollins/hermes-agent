---
wizard: 0.2
id: hermes-agent
name: Hermes Agent
type: agent
category: agent
tags: [productivity, automation]
version: 2026.9.24-wizard3
summary: The AI agent itself (Nous Research), with Claude subscription support built in.
icon: icon.png
license: MIT
source: https://github.com/oscarhenrycollins/hermes-agent
runtime:
  kind: runtipi
  config: runtipi/config.json
  compose: runtipi/docker-compose.yml
mcp:
  path: /mcp
  port: 9119
  transport: streamable-http
  auth: bearer:HERMES_API_KEY
skill: SKILL.md
browser_contract:
  # Proposed second reviewed separate-origin exception (wizard-os issue 386; awaiting Miguel's approval): with the
  # browser boundary on, the Hermes dashboard opens only as its own tab on its own origin with its own login, never
  # framed in a Wizard window. No browser data operations. Off, or until the App Manager lists it, nothing changes.
  version: 1
  isolation: own-tab
view:
  path: /
  port: 9119
provides:
- agent
---

# Hermes Agent

The AI agent itself (Nous Research), with Claude subscription support built in.
