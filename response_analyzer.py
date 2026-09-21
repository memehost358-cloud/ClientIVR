#!/usr/bin/env python3
"""
Response Analyzer - Classifies IVR responses.

NO hardcoded classification phrases live here. All keyword lists come from
the IVRProfile passed at construction time (IVRProfile itself is loaded
100% from environment variables by config.IVRProfile.from_env()).

This means to tune matching for a new IVR or a slightly different prompt,
you only change PHRASES_* in .env.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from config import IVRProfile


class ResponseCategory(Enum):
    SECURITY_CODE_PROMPT = "security_code_prompt"
    INVALID_CARD = "invalid_card"
    VERIFICATION_REQUIRED = "verification_required"
    VALID_CODE = "valid_code"
    INVALID_CODE = "invalid_code"
    UNEXPECTED_RESPONSE = "unexpected_response"


class ResponseAnalyzer:
    """Analyzes IVR transcripts using keyword lists from IVRProfile."""

    def __init__(self, profile: IVRProfile, logger: Optional[logging.Logger] = None):
        self.profile = profile
        self.logger = logger or logging.getLogger(self.__class__.__name__)

    # ---------- Phase 1: after entering card number ----------

    def classify_card_response(self, transcript: str) -> ResponseCategory:
        if not transcript:
            return ResponseCategory.UNEXPECTED_RESPONSE

        t = transcript.lower()

        # Priority order matters. CVV prompt means the card was accepted so
        # it must be checked before "invalid / verification required" phrases
        # even if both accidentally match.
        for phrase in self.profile.phrases_cvv_prompt:
            if phrase and phrase in t:
                self.logger.info(f"MATCH [cvv_prompt]: '{phrase}'")
                return ResponseCategory.SECURITY_CODE_PROMPT

        for phrase in self.profile.phrases_invalid_card:
            if phrase and phrase in t:
                self.logger.info(f"MATCH [invalid_card]: '{phrase}'")
                return ResponseCategory.INVALID_CARD

        for phrase in self.profile.phrases_verification_required:
            if phrase and phrase in t:
                self.logger.info(f"MATCH [verification_required]: '{phrase}'")
                return ResponseCategory.VERIFICATION_REQUIRED

        self.logger.warning(
            f"UNCLASSIFIED (Phase 1) transcript[:200]={transcript[:200]!r}"
        )
        return ResponseCategory.UNEXPECTED_RESPONSE

    # ---------- Phase 2: after entering one CVV candidate ----------

    def classify_security_code_response(self, transcript: str) -> bool:
        """Return True when transcript indicates the *correct* CVV was accepted.

        On any ambiguity (neither explicit success nor explicit invalid),
        return False - better to try next code than falsely stop the loop.
        """
        if not transcript:
            return False

        t = transcript.lower()

        # Explicit success phrases -> return True immediately
        for phrase in self.profile.phrases_valid_code:
            if phrase and phrase in t:
                self.logger.info(f"MATCH [valid_cvv]: '{phrase}'")
                return True

        # Explicit invalid phrases -> return False
        for phrase in self.profile.phrases_invalid_code:
            if phrase and phrase in t:
                self.logger.info(f"MATCH [invalid_cvv]: '{phrase}'")
                return False

        # No strong signal -> conservative false
        self.logger.info(
            f"No explicit success/invalid phrase; treating CVV as INVALID "
            f"(transcript[:160]={transcript[:160]!r})"
        )
        return False
