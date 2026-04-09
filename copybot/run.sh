#!/usr/bin/env bash
# Run the copy trading bot.
# Called by cron — uses absolute paths so it works regardless of working directory.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Use the system python3 or a venv if present
if [ -f "$SCRIPT_DIR/venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/venv/bin/python"
else
    PYTHON="$(which python3)"
fi

exec "$PYTHON" "$SCRIPT_DIR/bot.py" "$@"
