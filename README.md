# Jarvis

A planning and reasoning brain layered over [browser-use](browser-use/)'s core tools.

browser-use is used purely as a **tool layer** — CDP browser control plus its action
registry. The autonomous agent loop and the embedded LLM are not used. The reasoning
comes from whatever drives the MCP server: Claude Code, Claude Desktop, or the
optional standalone runner.

```
┌─ Host LLM (Claude Code / Desktop / runner.py) ── the reasoning ─┐
│                          ↕ MCP stdio                             │
├─ BRAIN ──────────────────────────────────────────────────────────┤
│  plan_create · plan_next · plan_observe · plan_revise · status    │
│  skill injection by domain  ·  JSONL run journal                  │
├─ TOOL SURFACE ───────────────────────────────────────────────────┤
│  21 registry actions + 4 native introspection tools               │
├─ browser_use.Tools() + BrowserSession  (vendored, untouched) ────┤
└──────────────────────────────────────────────────────────────────┘
```

Nothing under `jarvis/` imports an LLM except `runner.py`, which is optional. The MCP
path never needs an API key.

## Why not the stock browser-use MCP

[browser_use/mcp/server.py](browser-use/browser_use/mcp/server.py) is a flat dispatcher
with three limits this package addresses:

| | Stock MCP | Jarvis |
|---|---|---|
| Planning | none — every call is stateless | explicit plan state machine |
| Keyboard | **no `send_keys`** — cannot press Enter | full registry, keyboard included |
| JS execution | not exposed | `browser_evaluate` |
| Tool schemas | 16, hand-written, drift from the library | 21, generated from the registry |
| Reasoning | `retry_with_browser_use_agent` spawns a second LLM agent | the host reasons; no embedded LLM |
| Extraction | `extract_content` needs an `OPENAI_API_KEY` | `browser_markdown`, zero LLM |
| Memory | none | JSONL journal + learnable skills |

## Setup

```bash
py -3.14 -m pip install uv
py -3.14 -m uv venv --python 3.12 .venv
py -3.14 -m uv pip install --python .venv/Scripts/python.exe -e ./browser-use
```

browser-use is installed editable, so `browser-use/` stays the live source and can be
updated from upstream without touching `jarvis/`.

## Running it

**As an MCP server.** [.mcp.json](.mcp.json) registers it for this project; restart
Claude Code to pick it up. Set `JARVIS_HEADLESS=true` for an invisible browser,
`JARVIS_ALLOWED_DOMAINS=a.com,b.com` to restrict navigation.

**Standalone.** `runner.py` drives the same brain with the Claude API — for unattended
runs where no MCP host is present:

```bash
pip install anthropic          # not a dependency of the MCP path
python -m jarvis.runner "search youtube for lofi hip hop and list the top 3"
```

## The loop

The brain does not think. It structures thinking and carries memory between tool calls,
so the host has to commit to a plan and observe real outcomes rather than drift.

1. `plan_create(goal, steps)` — commit before touching the browser.
2. `plan_next()` — returns the current step, the domain skill, **and** live page state
   in one call. Collapses three round-trips into one and guarantees the host never acts
   on stale element indices.
3. `browser_*` — take the step's actions.
4. `plan_observe(status, note)` — record what happened; the plan advances.

A step that fails three times is marked failed and forces `plan_revise()`, so a host
cannot loop forever on the same broken approach.

## Skills

One markdown file per domain in [jarvis/skills/](jarvis/skills/), named after the domain
(`youtube.com.md`). `plan_next()` injects the most specific match for the current URL —
`news.ycombinator.com` beats `ycombinator.com`. `skill_learn(domain, lesson)` appends to
one, so a failure discovered in run 1 is knowledge in run 2.

## Journal

Every plan, step and action lands in `~/.jarvis/runs/<run_id>.jsonl`, one JSON object per
line — replayable, diffable, and the raw material for new skills.

## Tests

```bash
export PYTHONPATH=.
.venv/Scripts/python.exe tests/test_brain.py           # state machine, no browser
.venv/Scripts/python.exe tests/test_stdio.py           # real MCP handshake
JARVIS_HEADLESS=true .venv/Scripts/python.exe tests/test_e2e_youtube.py
JARVIS_HEADLESS=true .venv/Scripts/python.exe tests/test_e2e_google.py
```

The two end-to-end tests drive a real browser through a real search on two different
sites, which is what keeps the skill loader and the registry-generated schemas honest.
