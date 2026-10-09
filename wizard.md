---
wizard: 0.2
id: hermes-agent
name: Hermes Agent
type: agent
version: 2026.9.24
summary: The self-improving AI agent by Nous Research.
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
view:
  path: /
  port: 9119
provides:
- agent
---

# Hermes Agent

The self-improving AI agent by Nous Research.
