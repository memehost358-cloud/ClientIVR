#!/usr/bin/env python3
"""
IVR Client - Handles interaction with IVR systems via Asterisk AMI

Manages Asterisk Manager Interface connections, call origination,
and DTMF sending for card validation.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional
import requests

from config import Config


class IVRClient:
    """IVR interaction client using Asterisk AMI."""

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.call_state: Dict[str, Any] = {}

    async def connect(self) -> None:
        """Connect to SignalWire REST API (no AMI needed for REST API)."""
        self.logger.info("Using SignalWire REST API - no AMI connection needed")

    async def disconnect(self) -> None:
        """Disconnect from SignalWire REST API (no AMI needed)."""
        self.logger.info("SignalWire REST API call completed")

    async def validate_card(self, card_number: str) -> Dict[str, Any]:
        """Originate a call to validate card number using SignalWire REST API."""
        call_id = f"card-validation-{int(time.time())}"
        masked_card = self._mask_card_number(card_number)

        self.logger.info(f"Validating card: {masked_card}")

        # Use SignalWire REST API to place call
        account_sid = self.config.signalwire_account_sid or self.config.twilio_account_sid
        auth_token = self.config.signalwire_auth_token or self.config.twilio_auth_token
        phone_number = self.config.signalwire_phone_number or self.config.twilio_phone_number

        if not account_sid or not auth_token:
            raise Exception("SignalWire credentials not configured")

        # Use localhost for now - will need public URL with ngrok or similar
        voice_url = f"http://10.0.0.157:5000/voice.xml?stage=card_entry&card_number={card_number}"
        
        url = f"https://{account_sid}.signalwire.com/api/laml/2010-04-01/Accounts/{account_sid}/Calls.json"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'From': phone_number,
            'To': self.config.ivr_phone_number,
            'Url': voice_url,
            'Method': 'GET',
            'StatusCallback': f'http://10.0.0.157:5000/status/{call_id}',
            'StatusCallbackEvent': 'completed',
        }

        try:
            response = requests.post(url, headers=headers, data=data, auth=(account_sid, auth_token))
            self.logger.info(f"SignalWire API response: {response.status_code}")
            self.logger.info(f"SignalWire API body: {response.text}")
            
            if response.status_code not in [200, 201]:
                raise Exception(f"SignalWire API error: {response.text}")

            # Wait for call completion
            await asyncio.sleep(30)

            return {
                "call_id": call_id,
                "recording_path": f"{self.config.recordings_dir}/{call_id}.wav",
                "duration": 30.0,
                "status": "completed"
            }

        except Exception as e:
            self.logger.error(f"SignalWire API error: {e}")
            raise

    async def validate_security_code(self, card_number: str, security_code: str) -> Dict[str, Any]:
        """Originate a call to validate security code using SignalWire REST API."""
        call_id = f"security-validation-{int(time.time())}"
        masked_card = self._mask_card_number(card_number)

        self.logger.info(f"Validating security code {security_code} for card: {masked_card}")

        # Use SignalWire REST API to place call
        account_sid = self.config.signalwire_account_sid or self.config.twilio_account_sid
        auth_token = self.config.signalwire_auth_token or self.config.twilio_auth_token
        phone_number = self.config.signalwire_phone_number or self.config.twilio_phone_number

        if not account_sid or not auth_token:
            raise Exception("SignalWire credentials not configured")

        # Use localhost for now - will need public URL with ngrok or similar
        voice_url = f"http://10.0.0.157:5000/voice.xml?stage=security_code&security_code={security_code}"
        
        url = f"https://{account_sid}.signalwire.com/api/laml/2010-04-01/Accounts/{account_sid}/Calls.json"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'From': phone_number,
            'To': self.config.ivr_phone_number,
            'Url': voice_url,
            'Method': 'GET',
            'StatusCallback': f'http://10.0.0.157:5000/status/{call_id}',
            'StatusCallbackEvent': 'completed',
        }

        try:
            response = requests.post(url, headers=headers, data=data, auth=(account_sid, auth_token))
            self.logger.info(f"SignalWire API response: {response.status_code}")
            self.logger.info(f"SignalWire API body: {response.text}")
            
            if response.status_code not in [200, 201]:
                raise Exception(f"SignalWire API error: {response.text}")

            # Wait for call completion
            await asyncio.sleep(20)

            return {
                "call_id": call_id,
                "recording_path": f"{self.config.recordings_dir}/{call_id}.wav",
                "duration": 20.0,
                "status": "completed"
            }

        except Exception as e:
            self.logger.error(f"SignalWire API error: {e}")
            raise

    def _mask_card_number(self, card_number: str) -> str:
        """Mask card number for logging."""
        if len(card_number) <= 4:
            return "*" * len(card_number)
        return "*" * (len(card_number) - 4) + card_number[-4:]
