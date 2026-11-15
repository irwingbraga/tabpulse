#!/bin/bash
# Launched by OpenDeck with: -port N -pluginUUID X -registerEvent Y -info JSON
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
if [ ! -x .venv/bin/python ]; then
	uv venv --python 3.12 .venv >&2 && uv pip install --python .venv/bin/python iterm2 pillow websockets >&2 || exit 1
fi
exec .venv/bin/python -u main.py "$@"
