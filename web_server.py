#!/usr/bin/env python3
"""
Local Web Server (optional admin UI / status helper).

With the switch to local Asterisk AMI originate this server is NO LONGER
used to steer active calls. It remains available for:
  - GET  /health        -> 200 OK (process alive)
  - GET  /status        -> read-only view of state.json (if exists)
  - GET  /voice.xml     -> returns a harmless no-op LAML (keeps endpoint alive)

All hardcoded callerIds, phone numbers and private IPs previously embedded
in XML templates have been removed. The system now configures itself from
.env through the `Config` class.
"""

from flask import Flask, request, Response
import json
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATE_FILE = Path(os.getenv("STATE_FILE", "/var/lib/card-validation-system/state.json"))


@app.route("/health", methods=["GET"])
def health():
    return Response(
        json.dumps({"status": "ok", "service": "card-validation-web"}),
        mimetype="application/json",
        status=200,
    )


@app.route("/status", methods=["GET"])
def show_status():
    payload: dict = {"state_file": str(STATE_FILE), "exists": STATE_FILE.exists()}
    if STATE_FILE.exists():
        try:
            payload["state"] = json.loads(STATE_FILE.read_text())
        except Exception as e:
            payload["error"] = f"Cannot read state file: {e}"
    return Response(json.dumps(payload, indent=2), mimetype="application/json")


@app.route("/voice.xml", methods=["GET", "POST"])
def voice_xml():
    """
    Harmless no-op. The active call flow now goes through Asterisk Originate
    + local dialplan in asterisk/extensions.conf (not LAML). Kept so any
    legacy URL does not 404.
    """
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Hangup/>
</Response>"""
    return Response(xml, mimetype="application/xml")


if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "5000"))
    host = os.getenv("WEB_HOST", "0.0.0.0")
    logger.info(f"Starting admin web server on {host}:{port}")
    app.run(host=host, port=port, debug=False)
