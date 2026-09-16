#!/usr/bin/env python3
"""
Response Analyzer - Classifies IVR responses

Analyzes transcribed IVR responses to determine card validation status
and security code validity.
"""

import logging
from enum import Enum
from typing import Optional
from config import Config


class ResponseCategory(Enum):
    """Categories for IVR responses."""
    SECURITY_CODE_PROMPT = "security_code_prompt"
    INVALID_CARD = "invalid_card"
    VERIFICATION_REQUIRED = "verification_required"
    VALID_CODE = "valid_code"
    INVALID_CODE = "invalid_code"
    UNEXPECTED_RESPONSE = "unexpected_response"


class ResponseAnalyzer:
    """Analyzes IVR responses for card validation."""

    def __init__(self, config: Config):
        self.config = config
        self.logger = logging.getLogger(__name__)

        # Response phrases
        self.security_code_prompts = [
            "enter the 3 digit security code",
            "three digit security code",
            "security code",
            "3 digit security code"
        ]

        self.invalid_card_phrases = [
            "card number does not exist",
            "invalid card number",
            "card is invalid"
        ]

        self.verification_phrases = [
            "one time verification code",
            "confirm your identity",
            "text you a code",
            "verification code"
        ]

        self.valid_code_responses = [
            "activated",
            "successful",
            "complete",
            "thank you"
        ]

        self.invalid_code_responses = [
            "invalid security code",
            "incorrect security code",
            "wrong code"
        ]

    def classify_card_response(self, transcript: str) -> ResponseCategory:
        """Classify the IVR response after card number entry."""
        if not transcript:
            return ResponseCategory.UNEXPECTED_RESPONSE

        transcript_lower = transcript.lower()

        # Check for security code prompt
        for phrase in self.security_code_prompts:
            if phrase.lower() in transcript_lower:
                self.logger.info(f"Security code prompt detected: '{phrase}'")
                return ResponseCategory.SECURITY_CODE_PROMPT

        # Check for invalid card
        for phrase in self.invalid_card_phrases:
            if phrase.lower() in transcript_lower:
                self.logger.info(f"Invalid card response detected: '{phrase}'")
                return ResponseCategory.INVALID_CARD

        # Check for verification required
        for phrase in self.verification_phrases:
            if phrase.lower() in transcript_lower:
                self.logger.info(f"Verification required detected: '{phrase}'")
                return ResponseCategory.VERIFICATION_REQUIRED

        self.logger.warning(f"Unexpected response: {transcript[:200]}")
        return ResponseCategory.UNEXPECTED_RESPONSE

    def classify_security_code_response(self, transcript: str) -> bool:
        """Classify the IVR response after security code entry."""
        if not transcript:
            return False

        transcript_lower = transcript.lower()

        # Check for valid code (success)
        for phrase in self.valid_code_responses:
            if phrase.lower() in transcript_lower:
                self.logger.info(f"Valid security code detected: '{phrase}'")
                return True

        # Check for invalid code
        for phrase in self.invalid_code_responses:
            if phrase.lower() in transcript_lower:
                self.logger.info(f"Invalid security code detected: '{phrase}'")
                return False

        return False
