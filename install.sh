#!/bin/sh
# Install the grubhub skill from this repo (skill/grubhub) to ~/.claude/skills/grubhub.
#
# The live copy must live outside ~/Documents: macOS privacy protection blocks the Claude
# desktop app (and so the Cowork MCP server it launches) from reading ~/Documents.
#
# Code (SKILL.md, scripts/) is always overwritten. User data the skill edits at runtime
# (favorites.json, references/*.md) is only seeded when missing; state.json and receipts/
# are never touched.
set -eu
REPO="$(cd "$(dirname "$0")" && pwd)"
SRC="$REPO/skill/grubhub"
DST="$HOME/.claude/skills/grubhub"

mkdir -p "$DST/scripts" "$DST/references"
cp "$SRC/SKILL.md" "$DST/SKILL.md"
cp "$SRC"/scripts/*.py "$DST/scripts/"
[ -f "$DST/favorites.json" ] || cp "$SRC/favorites.json" "$DST/favorites.json"
for f in "$SRC"/references/*.md; do
  [ -f "$DST/references/$(basename "$f")" ] || cp "$f" "$DST/references/"
done

# Skill bundle for claude.ai → Settings → Capabilities → Skills (used by Cowork).
mkdir -p "$REPO/dist"
ZIP="$REPO/dist/grubhub-skill.zip"
rm -f "$ZIP"
(cd "$HOME/.claude/skills" && zip -qr -X "$ZIP" grubhub -x 'grubhub/state.json' 'grubhub/receipts/*' 'grubhub/server.log' '*/__pycache__/*' '*.DS_Store')
echo "installed $DST"
echo "bundle    $ZIP"
