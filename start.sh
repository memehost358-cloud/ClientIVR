#!/bin/bash
# Startup script for Card Validation System
# Starts web server (background) + validation system (foreground).

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "Starting Card Validation System..."
echo "Project root: $PROJECT_ROOT"

# Auto-detect virtualenv: ./venv > /opt/ivr-tester/venv > system
if [ -f "$PROJECT_ROOT/venv/bin/activate" ]; then
    echo "Using venv at $PROJECT_ROOT/venv"
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/venv/bin/activate"
elif [ -f "/opt/ivr-tester/venv/bin/activate" ]; then
    echo "Using venv at /opt/ivr-tester/venv"
    # shellcheck disable=SC1091
    source "/opt/ivr-tester/venv/bin/activate"
else
    echo "No virtualenv found; using system python3"
fi

# Start web server in background
echo "Starting Voice XML/web status server on ${WEB_HOST:-127.0.0.1}:${WEB_PORT:-5000}..."
python3 "$PROJECT_ROOT/web_server.py" &
WEB_SERVER_PID=$!
echo "Web server started with PID: $WEB_SERVER_PID"

# Wait for web server to start
sleep 3
if ! kill -0 "$WEB_SERVER_PID" 2>/dev/null; then
    echo "WARNING: web_server.py exited early; continuing without it."
fi

# Start validation system
echo "Starting card validation system (main.py)..."
python3 "$PROJECT_ROOT/main.py"
MAIN_EXIT=$?

# Cleanup
echo "Stopping web server (PID=$WEB_SERVER_PID)..."
kill "$WEB_SERVER_PID" 2>/dev/null || true
wait "$WEB_SERVER_PID" 2>/dev/null || true

echo "Done (main.py exit=$MAIN_EXIT)."
exit $MAIN_EXIT
