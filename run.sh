#!/bin/bash
# Card Validation System - Run Script
# Starts the card validation system

set -e

echo "==================================="
echo "Starting Card Validation System"
echo "==================================="

# Check if .env file exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found"
    echo "Please run ./configure.sh first"
    exit 1
fi

# Source environment variables
source .env

# Activate virtual environment
source /var/lib/card-validation-system/venv/bin/activate

# Run the main application
cd /var/lib/card-validation-system
python3 main.py
