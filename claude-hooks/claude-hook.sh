#!/bin/bash
# Usage: claude-hook.sh <working|attention|done|idle|notification>
# Reports Claude Code state for the current iTerm2 session to the OpenDeck iTerm plugin.
# "notification" reads the hook JSON on stdin: idle/auth notices are ignored,
# anything else (permission prompt, elicitation) becomes "attention".
SOCK="$HOME/.cache/opendeck-iterm/state.sock"
STATE="$1"
if [ "$STATE" = "notification" ]; then
	INPUT=$(cat)
	echo "$(date +%T) notification: $INPUT" >> "$HOME/.cache/opendeck-iterm/hooks.log" 2>/dev/null
	case "$INPUT" in
		*idle_prompt*|*auth_success*) exit 0 ;;
	esac
	STATE="attention"
fi
[ -S "$SOCK" ] && [ -n "$ITERM_SESSION_ID" ] || exit 0
printf '{"session":"%s","state":"%s"}\n' "${ITERM_SESSION_ID#*:}" "$STATE" | nc -U -w1 "$SOCK" >/dev/null 2>&1
exit 0
