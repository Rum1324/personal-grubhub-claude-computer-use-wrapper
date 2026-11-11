# grubhub skill

Claude skill that turns a 7–8 click Grubhub order into: **"grubhub" → pick a number → order # + pickup ETA.**
Built for Berkeley Cal Dining meal equivalencies (free, one every 30 min).

## Requirements
Campus dining / meal equivalencies only exist in the Grubhub **mobile app** (not grubhub.com), so the skill drives the Grubhub Android app in a local emulator over `adb`.
- Android SDK command-line tools: `brew install --cask android-commandlinetools`, then `sdkmanager "platform-tools" "emulator" "system-images;android-35;google_apis_playstore;arm64-v8a"`
- An AVD named `grubhub` with Google Play, Grubhub installed and logged in (one-time; boot it with `python3 ~/.claude/skills/grubhub/scripts/droid.py up --window`).
- Python 3 (stdlib only).

The emulator boots headless per order (~10 s from snapshot, ~2–4 GB RAM) and shuts down after the order screenshot is sent.

## Use
| Say | What happens |
|---|---|
| `grubhub` | numbered favorites + meal-equivalency cooldown |
| `3` / `order 3` | builds the order in the app in one scripted pass (`prepare`), checks payment is 1 meal equivalency (no $ charge), places it — no extra confirmation |
| `dry run 3` | same, but stops before placing |
| `setup` | rebuilds favorites (≤10) from your Grubhub order history |

## Layout
```
skill/grubhub/            source (edit here, then ./install.sh)
  SKILL.md                instructions Claude follows
  favorites.json          seed favorites (≤10)
  references/flow.md      app screen labels · references/locations.md  hours + menus
  scripts/droid.py        adb driver: boot emulator, dump screen, tap/pick by label, screenshot
  scripts/order.py        scripted order: `droid.py prepare <id>` (to a verified checkout) / `droid.py place`
  scripts/mcp_server.py   stdio MCP server so Cowork can drive the emulator
~/.claude/skills/grubhub/ live copy used by Claude Code (any folder) and the Cowork MCP server
  state.json, receipts/   last order + order screenshots (live copy only)
```

## Install / update
```bash
./install.sh
```
Copies code to `~/.claude/skills/grubhub` (never overwrites your favorites, state, notes or receipts) and rebuilds `dist/grubhub-skill.zip`.

The live copy must stay outside `~/Documents`: macOS privacy protection stops the Claude desktop app, and so the Cowork MCP server it launches, from reading `~/Documents` ("Operation not permitted").

## Use from Cowork
Cowork's shell runs in a sandboxed VM that can't reach the emulator, so the skill talks to it through a local MCP server that runs on the Mac:
1. `~/Library/Application Support/Claude/claude_desktop_config.json` → `mcpServers.grubhub = {"command": "/usr/bin/python3", "args": ["/Users/<you>/.claude/skills/grubhub/scripts/mcp_server.py"]}`, then restart the Claude app. Server log: `~/Library/Logs/Claude/mcp-server-grubhub.log`.
2. Upload `dist/grubhub-skill.zip` in claude.ai → Settings → Capabilities → Skills.

Favorites, state and notes live only in the live copy on the Mac (read and written through the MCP tools), so Claude Code and Cowork share them.
