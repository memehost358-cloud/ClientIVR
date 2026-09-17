#!/usr/bin/env python3
"""
Configuration Management

Handles loading and validating configuration from environment variables.
"""

import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """System configuration from environment variables."""

    # Company Information
    company_name: str
    system_name: str
    operated_by: str
    contact_email: str

    # IVR Configuration
    ivr_phone_number: str
    card_number: str

    # SIP Trunk Configuration (SignalWire)
    signalwire_account_sid: str
    signalwire_auth_token: str
    signalwire_phone_number: str
    
    # SIP Trunk Configuration (Twilio - Optional)
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str

    # Transcription Configuration (ElevenLabs)
    elevenlabs_api_key: str

    # Asterisk AMI Configuration
    ami_host: str
    ami_port: int
    ami_username: str
    ami_secret: str

    # System Configuration
    max_daily_calls: int
    rate_limit_calls_per_hour: int
    call_cooldown_seconds: int
    max_security_attempts_per_call: int

    # Storage Configuration
    results_dir: str
    recordings_dir: str
    state_file: str
    log_dir: str

    # Security Configuration
    mask_card_numbers: bool
    emergency_stop_on_detection: bool

    @classmethod
    def from_env(cls) -> 'Config':
        """Load configuration from environment variables."""
        return cls(
            company_name=os.getenv('COMPANY_NAME', 'UnknownCardCompany'),
            system_name=os.getenv('SYSTEM_NAME', 'CardActivationSystem'),
            operated_by=os.getenv('OPERATED_BY', 'CardOperationsTeam'),
            contact_email=os.getenv('CONTACT_EMAIL', ''),

            ivr_phone_number=os.getenv('IVR_PHONE_NUMBER', ''),
            card_number=os.getenv('CARD_NUMBER', ''),

            # SignalWire credentials (primary)
            signalwire_account_sid=os.getenv('SIGNALWIRE_ACCOUNT_SID', os.getenv('TWILIO_ACCOUNT_SID', '')),
            signalwire_auth_token=os.getenv('SIGNALWIRE_AUTH_TOKEN', os.getenv('TWILIO_AUTH_TOKEN', '')),
            signalwire_phone_number=os.getenv('SIGNALWIRE_PHONE_NUMBER', os.getenv('TWILIO_PHONE_NUMBER', '')),
            
            # Twilio credentials (fallback)
            twilio_account_sid=os.getenv('TWILIO_ACCOUNT_SID', ''),
            twilio_auth_token=os.getenv('TWILIO_AUTH_TOKEN', ''),
            twilio_phone_number=os.getenv('TWILIO_PHONE_NUMBER', ''),

            elevenlabs_api_key=os.getenv('ELEVENLABS_API_KEY', ''),

            ami_host=os.getenv('AMI_HOST', '127.0.0.1'),
            ami_port=int(os.getenv('AMI_PORT', '5038')),
            ami_username=os.getenv('AMI_USERNAME', 'cardvalidator'),
            ami_secret=os.getenv('AMI_SECRET', ''),

            max_daily_calls=int(os.getenv('MAX_DAILY_CALLS', '100')),
            rate_limit_calls_per_hour=int(os.getenv('RATE_LIMIT_CALLS_PER_HOUR', '10')),
            call_cooldown_seconds=int(os.getenv('CALL_COOLDOWN_SECONDS', '30')),
            max_security_attempts_per_call=int(os.getenv('MAX_SECURITY_ATTEMPTS_PER_CALL', '3')),

            results_dir=os.getenv('RESULTS_DIR', '/var/lib/card-validation-system/results'),
            recordings_dir=os.getenv('RECORDINGS_DIR', '/var/spool/asterisk/recordings'),
            state_file=os.getenv('STATE_FILE', '/var/lib/card-validation-system/state.json'),
            log_dir=os.getenv('LOG_DIR', '/var/log/card-validation-system'),

            mask_card_numbers=os.getenv('MASK_CARD_NUMBERS', 'true').lower() == 'true',
            emergency_stop_on_detection=os.getenv('EMERGENCY_STOP_ON_DETECTION', 'true').lower() == 'true',
        )

    def validate(self) -> bool:
        """Validate that required configuration is present."""
        required_fields = [
            ('IVR_PHONE_NUMBER', self.ivr_phone_number),
            ('CARD_NUMBER', self.card_number),
            ('TWILIO_ACCOUNT_SID', self.twilio_account_sid),
            ('TWILIO_AUTH_TOKEN', self.twilio_auth_token),
            ('ELEVENLABS_API_KEY', self.elevenlabs_api_key),
            ('AMI_SECRET', self.ami_secret),
        ]

        for field_name, field_value in required_fields:
            if not field_value or field_value.startswith('your_') or field_value.startswith('YOUR_'):
                print(f"ERROR: {field_name} is not configured properly")
                return False

        return True
