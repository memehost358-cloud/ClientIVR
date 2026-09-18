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
from panoramisk import Manager
import requests

from config import Config


class IVRClient:
    """IVR interaction client using Asterisk AMI."""

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.manager: Optional[Manager] = None
        self.call_state: Dict[str, Any] = {}

    async def connect(self) -> None:
        """Connect to Asterisk AMI."""
        self.logger.info(f"Connecting to AMI {self.config.ami_host}:{self.config.ami_port}")

        self.manager = Manager(
            host=self.config.ami_host,
            port=self.config.ami_port,
            username=self.config.ami_username,
            secret=self.config.ami_secret,
        )

        await self.manager.connect()
        self.logger.info("AMI connection established")

    async def disconnect(self) -> None:
        """Disconnect from Asterisk AMI."""
        if self.manager:
            await self.manager.close()
            self.logger.info("AMI connection closed")

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

        url = f"https://{account_sid}.signalwire.com/api/laml/2010-04-01/Accounts/{account_sid}/Calls.json"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'From': phone_number,
            'To': self.config.ivr_phone_number,
            'Url': 'http://your-server.com/voice.xml',  # Need to provide voice instructions
            'Method': 'GET',
            'StatusCallback': f'http://your-server.com/status/{call_id}',
            'StatusCallbackEvent': 'completed',
        }

        try:
            response = requests.post(url, headers=headers, data=data, auth=(account_sid, auth_token))
            self.logger.info(f"SignalWire API response: {response.status_code}")
            
            if response.status_code != 201:
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

        url = f"https://{account_sid}.signalwire.com/api/laml/2010-04-01/Accounts/{account_sid}/Calls.json"
        
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        
        data = {
            'From': phone_number,
            'To': self.config.ivr_phone_number,
            'Url': 'http://your-server.com/voice.xml',  # Need to provide voice instructions
            'Method': 'GET',
            'StatusCallback': f'http://your-server.com/status/{call_id}',
            'StatusCallbackEvent': 'completed',
        }

        try:
            response = requests.post(url, headers=headers, data=data, auth=(account_sid, auth_token))
            self.logger.info(f"SignalWire API response: {response.status_code}")
            
            if response.status_code != 201:
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
