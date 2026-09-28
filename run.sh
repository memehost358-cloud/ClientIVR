#!/bin/bash
# Card Validation System - Run Script
# Starts the card validation system.

set -e

echo "==================================="
echo "Starting Card Validation System"
echo "==================================="

# Project root = directory of this script
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# Check if .env file exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found in $PROJECT_ROOT"
    echo "Please run ./configure.sh first or copy .env.example -> .env and edit it."
    exit 1
fi

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

# Run the main application
echo "Running: python3 $PROJECT_ROOT/main.py"
exec python3 "$PROJECT_ROOT/main.py"
