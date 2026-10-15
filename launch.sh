#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-5001}"
URL="http://127.0.0.1:${PORT}/"
APP_MARKER='name="application-name" content="Greedy Gobbler Local"'

if ! command -v curl >/dev/null 2>&1; then
    printf '%s\n' "Greedy Gobbler needs curl to check whether its local server is ready." >&2
    exit 1
fi

if curl --silent --show-error --fail --max-time 2 "$URL" 2>/dev/null | grep -Fq "$APP_MARKER"; then
    if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
    printf 'Greedy Gobbler is already running at %s\n' "$URL"
    exit 0
elif curl --silent --show-error --fail --max-time 2 "$URL" >/dev/null 2>&1; then
    printf 'Port %s already serves another local page. Set PORT to use a different port.\n' "$PORT" >&2
    exit 1
fi

if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
    APP_PYTHON="$APP_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    APP_PYTHON="$(command -v python3)"
else
    printf '%s\n' "Python 3 was not found. Install Python and the project requirements first." >&2
    exit 1
fi

STATE_ROOT="${XDG_STATE_HOME:-${HOME}/.local/state}"
LOG_DIR="$STATE_ROOT/greedygobbler"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/launch.log"

PORT="$PORT" nohup "$APP_PYTHON" "$APP_DIR/app.py" >>"$LOG_FILE" 2>&1 &

for attempt in {1..15}; do
    if curl --silent --show-error --fail --max-time 2 "$URL" 2>/dev/null | grep -Fq "$APP_MARKER"; then
        if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
        printf 'Greedy Gobbler is ready at %s\n' "$URL"
        exit 0
    fi
    sleep 1
done

printf 'Greedy Gobbler did not start at %s. Recent log output:\n' "$URL" >&2
tail -n 25 "$LOG_FILE" >&2 || true
exit 1
