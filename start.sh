#!/bin/bash
# Startup script for Card Validation System
# Starts web server and validation system

echo "Starting Card Validation System..."

# Start web server in background
echo "Starting Voice XML web server on port 5000..."
python3 web_server.py &
WEB_SERVER_PID=$!
echo "Web server started with PID: $WEB_SERVER_PID"

# Wait for web server to start
sleep 3

# Start validation system
echo "Starting card validation system..."
python3 main.py

# Cleanup
echo "Stopping web server..."
kill $WEB_SERVER_PID
