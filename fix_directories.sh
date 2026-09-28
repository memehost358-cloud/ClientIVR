#!/bin/bash
# Creates required directories and sets correct ownership.
#
# Uses paths from .env if loaded; otherwise sensible defaults.

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Pull env vars if present; keep defaults otherwise.
if [ -f .env ]; then
    # shellcheck disable=SC1091
    set -a; source .env; set +a
fi

: "${RESULTS_DIR:=$PROJECT_ROOT/results}"
: "${RECORDINGS_DIR:=/var/spool/asterisk/recordings}"
: "${STATE_FILE:=$PROJECT_ROOT/state/state.json}"
: "${LOG_DIR:=$PROJECT_ROOT/logs}"

STATE_DIR="$(dirname "$STATE_FILE")"

echo "Creating directories..."
echo "  RESULTS_DIR    = $RESULTS_DIR"
echo "  RECORDINGS_DIR = $RECORDINGS_DIR"
echo "  STATE_DIR      = $STATE_DIR"
echo "  LOG_DIR        = $LOG_DIR"
echo "  EXTENSIONS_D   = /etc/asterisk/extensions_custom.d"

sudo mkdir -p "$RESULTS_DIR"
sudo mkdir -p "$STATE_DIR"
sudo mkdir -p "$LOG_DIR"
sudo mkdir -p "$RECORDINGS_DIR"
sudo mkdir -p /etc/asterisk/extensions_custom.d

# Python artifacts owned by current user (the user that runs main.py)
CUR_USER="${SUDO_USER:-$USER}"
CUR_GROUP="$(id -gn)"
sudo chown -R "$CUR_USER:$CUR_GROUP" "$RESULTS_DIR" "$STATE_DIR" "$LOG_DIR"

# Recordings MUST be writable by Asterisk user (MixMonitor runs under it)
sudo chown -R asterisk:asterisk "$RECORDINGS_DIR"
sudo chmod -R 2775 "$RECORDINGS_DIR"

echo "Directories created and permissions set successfully"
