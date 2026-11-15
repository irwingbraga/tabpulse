# TabPulse

Your iTerm2 tabs on your Stream Deck, with live activity. One button per tab, and each button shows at a glance whether that tab is idle, busy, waiting for you, or done. Built for people who run several Claude Code sessions at once.

A plugin for [OpenDeck](https://github.com/nekename/opendeck), the open source Stream Deck software for macOS, Windows and Linux.

## States

The whole button takes the color of the tab's state, with the tab name in bold.

| State | Look | Triggered by |
|---|---|---|
| Idle | Gray, ring with the tab number | Default |
| Working | Yellow, spinning arc | Claude Code is running (`UserPromptSubmit`, `PostToolUse` hooks), or a background tab is printing output |
| Needs input | Red-orange, pulsing `!` | Claude Code is asking for permission or input (`PermissionRequest`, `Notification` hooks) |
| Done | Green, check mark | Claude Code finished its turn (`Stop` hook). Clears when you focus the tab |

The focused tab gets a white border. Pressing a button switches iTerm2 to that tab and brings it to the front.

Notes on behavior:

- A "working" state with no hook update and no screen output for 15 seconds is treated as interrupted and returns to idle (covers pressing Esc in Claude Code).
- Idle notifications from Claude Code ("waiting for your input") are ignored, so a finished tab stays green.
- Tab names come from iTerm2's tab title, so a tab you rename shows your name. The trailing `(claude)` or `(-zsh)` suffix iTerm2 adds is hidden. Long names wrap to three lines and break at hyphens and slashes.

## How it works

```
iTerm2 Python API -----------+
(tabs, titles, focus,        |
 screen activity)            +--> TabPulse daemon (Python) --> OpenDeck --> Stream Deck
                             |         |
Claude Code hooks -----------+         +-- renders a 144x144 PNG per button
(unix socket, per session)
```

A single Python process talks to OpenDeck over the OpenAction websocket and to iTerm2 over its Python API (event driven, no polling of the terminal). Claude Code hooks report exact state per iTerm2 session through a unix socket at `~/.cache/opendeck-iterm/state.sock`, keyed by `ITERM_SESSION_ID`.

## Requirements

- macOS with [iTerm2](https://iterm2.com) (Settings > General > Magic > **Enable Python API**)
- [OpenDeck](https://github.com/nekename/opendeck)
- [uv](https://docs.astral.sh/uv/) (creates the plugin's virtualenv on first launch, needs network once)
- [Claude Code](https://claude.com/claude-code) for the working, needs-input and done states (optional: without it you only get generic output activity)

## Install

```bash
git clone https://github.com/irwingbraga/tabpulse.git
cd tabpulse
./install.sh
```

The script copies the plugin into `~/Library/Application Support/opendeck/plugins/` and prints the Claude Code hook config with the correct paths. It must be a copy, not a symlink: OpenDeck refuses to serve a plugin's settings page through a symlink that points outside its config directory.

Then:

1. Reload the plugin in OpenDeck (or restart OpenDeck).
2. Merge the printed `hooks` block into `~/.claude/settings.json`. Add to existing entries for the same events, do not replace them.
3. Restart any running Claude Code sessions (or open `/hooks` in them), since hooks are read at startup.

## Use

1. In OpenDeck, drag **iTerm Tab** (category **iTerm**) onto a key.
2. Left-click the key to open its settings and pick a tab from the **iTerm tab** dropdown. It lists tabs from every window; press **Refresh tabs** after opening new ones.
3. The binding follows that tab across reordering and windows until the tab is closed. Choose **Auto (by button order)** to map the Nth unpinned button to the Nth tab of the current window instead.

## Troubleshooting

- **Clicking a key shows no settings panel.** The plugin folder must be a real directory inside OpenDeck's plugins folder, not a symlink. Run `./install.sh` again.
- **Buttons stay blank.** Check that the iTerm2 Python API is enabled and that the plugin is running (`pgrep -fl "main.py -port"`). OpenDeck does not restart a plugin you killed; reload it from OpenDeck.
- **Claude tabs never turn yellow or green.** The hooks are not active in that session. Restart the session, and check `~/.claude/settings.json`.
- **A tab pulses `!` unexpectedly.** Notifications are logged to `~/.cache/opendeck-iterm/hooks.log`; the `notification_type` field tells you what fired.
- Plugin logs: `~/Library/Logs/opendeck/plugins/com.irwingbraga.iterm.sdPlugin.log`.

## Development

```
com.irwingbraga.iterm.sdPlugin/   the plugin (manifest, main.py, run.sh, property inspector, icons)
claude-hooks/                     hook script and settings snippet
install.sh                        copy plugin into OpenDeck
```

Edit the plugin in this repo, run `./install.sh`, then reload the plugin in OpenDeck. The plugin UUID (`com.irwingbraga.iterm`) is kept stable so existing button assignments survive updates.

## License

MIT
