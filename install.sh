#!/bin/bash
# Copies the plugin into OpenDeck's plugins directory and prints the Claude Code hook config.
# A copy (not a symlink) is required: OpenDeck refuses to serve inspector pages through a
# symlink that points outside its config directory.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
UUID="com.irwingbraga.iterm.sdPlugin"
case "$(uname)" in
	Darwin) DEST="$HOME/Library/Application Support/opendeck/plugins" ;;
	*) echo "TabPulse supports macOS only (iTerm2)." >&2; exit 1 ;;
esac

mkdir -p "$DEST/$UUID"
rsync -a --delete --exclude .venv --exclude __pycache__ "$HERE/$UUID/" "$DEST/$UUID/"
echo "Installed plugin to: $DEST/$UUID"
echo "Reload the plugin in OpenDeck (or restart OpenDeck) to apply."
echo
echo "Add these hooks to ~/.claude/settings.json (merge into any existing \"hooks\" entries):"
echo
sed "s#__TABPULSE_DIR__#$HERE#g" "$HERE/claude-hooks/settings-snippet.json"
