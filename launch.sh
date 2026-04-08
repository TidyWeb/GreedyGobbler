#!/bin/bash
# Dirty Gobbler launcher — starts Flask if not already running, then opens browser.

APP_DIR="/home/Phil/projects/DirtyGobbler"
URL="http://127.0.0.1:5001/"
LOG="/tmp/dirtygobbler.log"

cd "$APP_DIR"

# If already running, just open the browser
if curl -s "$URL" > /dev/null 2>&1; then
    xdg-open "$URL"
    exit 0
fi

# Start Flask in the background
source .venv/bin/activate
nohup python app.py > "$LOG" 2>&1 &

# Wait up to 10 seconds for Flask to become ready
for i in $(seq 1 10); do
    if curl -s "$URL" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

xdg-open "$URL"
