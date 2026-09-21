#!/usr/bin/env python3
"""
IVRClient - Asterisk AMI (panoramisk) Originate client.

Uses:
  * ProviderPolicy (SIP trunk list + failover threshold) - on N
    consecutive originate failures, auto-switches to the next provider.
  * IVRProfile - supplies IVR phone number + all DTMF/wait timings
    that are forwarded as channel variables to the Asterisk dialplan
    in asterisk/extensions.conf.

PANORAMISK NOTES (real behaviour):
  * manager.send_action() blocks until a response is received. For
    Originate with Async=false, the first response is Action:
    OriginateResponse (not the generic "Response: Success"). We handle
    both response formats defensively.
  * "Variable" accepts a dict[str,str] OR a list of "k=v" strings (NOT
    a single comma-separated string). We pass a dict.
  * The Originate action has a "Channel" (the outgoing leg), plus the
    optional Context/Exten/Priority/Application for the B-leg. We use
    Local/s@card-validation/n so the outgoing call itself executes
    our dialplan (not the callee's side of the Local channel).

Waits for Hangup AMI event, then polls for the .wav recording file
written by MixMonitor in the dialplan. Returns recording_path so the
caller can pass it straight to TranscriptionService.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from panoramisk import Manager

from config import Config, IVRProfile, ProviderPolicy, ProviderSpec


class IVRClient:
    def __init__(
        self,
        config: Config,
        profile: IVRProfile,
        provider_policy: ProviderPolicy,
        logger: logging.Logger,
    ):
        self.config = config
        self.profile = profile
        self.provider_policy = provider_policy
        self.logger = logger

        self.manager: Optional[Manager] = None
        self._pending_call_id: Optional[str] = None
        self._call_done: asyncio.Event = asyncio.Event()
        self._hangup_cause: Optional[str] = None
        self._dialstatus: Optional[str] = None
        self._originate_response: Optional[Dict[str, Any]] = None
        self._current_provider_idx = 0
        self._consecutive_failures = 0
        self._listeners_registered = False

    # ----------------------------- Provider rotation -----------------------------

    @property
    def current_provider(self) -> ProviderSpec:
        if not self.provider_policy.order:
            raise Exception("PROVIDER_ORDER list is empty - no provider to use")
        return self.provider_policy.order[
            self._current_provider_idx % len(self.provider_policy.order)
        ]

    def _mark_originate_success(self) -> None:
        self._consecutive_failures = 0

    def _mark_originate_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        n = len(self.provider_policy.order)
        self.logger.warning(
            f"Provider '{self.current_provider.name}' originate failed "
            f"({self._consecutive_failures}/{self.provider_policy.failover_consecutive_failures}): {reason}"
        )
        threshold = max(1, self.provider_policy.failover_consecutive_failures)
        if self._consecutive_failures >= threshold and n > 1:
            old = self.current_provider.name
            self._current_provider_idx = (self._current_provider_idx + 1) % n
            self._consecutive_failures = 0
            self.logger.warning(
                f"FAILOVER: switching provider {old} -> {self.current_provider.name}"
            )
        elif self._consecutive_failures >= threshold:
            self.logger.warning(
                "Only one provider configured - cannot failover; will keep retrying."
            )

    # ----------------------------- AMI lifecycle -----------------------------

    async def connect(self) -> None:
        if not self.config.ami_secret:
            raise Exception("AMI_SECRET not configured in .env")
        if not self.profile.ivr_phone_number:
            raise Exception("IVR_PHONE_NUMBER not configured under IVRProfile")

        # Close existing manager first (safe reconnect)
        await self.disconnect()

        self.logger.info(
            f"Connecting AMI {self.config.ami_host}:{self.config.ami_port} "
            f"user={self.config.ami_username} "
            f"(provider_order={[p.name for p in self.provider_policy.order]}, "
            f"initial={self.current_provider.name})"
        )

        self.manager = Manager(
            host=str(self.config.ami_host),
            port=int(self.config.ami_port),
            username=str(self.config.ami_username),
            secret=str(self.config.ami_secret),
            ping_delay=10,
            ping_interval=30,
        )
        if not self._listeners_registered:
            self.manager.register_event("OriginateResponse", self._on_originate_response)
            self.manager.register_event("Hangup", self._on_hangup)
            self.manager.register_event("DialEnd", self._on_dial_end)
            self._listeners_registered = True
        try:
            await self.manager.connect()
        except Exception as e:
            # Common error: port unreachable, bad secret, wrong host,
            # or manager.conf bindaddr != 127.0.0.1
            self.logger.error(
                f"AMI connect FAILED: {e}. Check: (1) asterisk manager.conf "
                f"user={self.config.ami_username} secret={bool(self.config.ami_secret)} "
                f"listen={self.config.ami_host}:{self.config.ami_port}; "
                f"(2) asterisk running; (3) firewall allows loopback 5038"
            )
            raise
        self.logger.info("Asterisk AMI connected")

    async def disconnect(self) -> None:
        if self.manager is not None:
            try:
                await self.manager.close()
            except Exception as e:
                self.logger.debug(f"AMI close error (ignored): {e}")
            self.manager = None

    async def _on_originate_response(self, manager, event) -> None:
        resp = str(event.get("Response", "") or event.get("response", "")).lower()
        reason = event.get("Reason") or event.get("ReasonText") or event.get("Message") or ""
        self.logger.info(f"OriginateResponse event: {resp or 'n/a'} reason={reason}")
        self._originate_response = dict(event) if hasattr(event, "get") else {}
        if resp and resp not in ("success", ""):
            # Failure response - end immediately
            self._call_done.set()

    async def _on_hangup(self, manager, event) -> None:
        cause = event.get("Cause-txt") or event.get("Cause") or ""
        self._hangup_cause = str(cause) if cause is not None else None
        self.logger.debug(f"Hangup event cause={self._hangup_cause}")
        # Call is over once the IVR hangs up our outgoing leg
        self._call_done.set()

    async def _on_dial_end(self, manager, event) -> None:
        status = event.get("DialStatus", "")
        if status:
            self._dialstatus = str(status)
            self.logger.info(f"DialEnd event DialStatus={self._dialstatus}")

    # ----------------------------- Helpers -----------------------------

    def _recording_file(self, call_id: str) -> str:
        rec_dir = self.config.recordings_dir or "/var/spool/asterisk/recordings"
        return os.path.join(rec_dir, f"{call_id}.wav")

    def _wait_timeout_s(self, has_security: bool) -> int:
        base = int(self.profile.max_call_wait_s)
        if base <= 0:
            pad = self.profile.wait_after_connect_s + self.profile.wait_after_card_digits_s
            if has_security:
                pad += self.profile.wait_after_cvv_digits_s
            base = int(pad) + 25
        return max(15, min(base, 180))

    # ----------------------------- Core originate -----------------------------

    @staticmethod
    def _check_success(resp: Any) -> (bool, str):
        """Return (ok, message) after parsing panoramisk Originate response.

        panoramisk returns either:
          - dict: {'Response': 'Success', ...}
          - panoramisk.message.Message with .get() like dict
          - list of such dicts (rare: multi-line response)
        For Originate specifically, the response can also be an
        OriginateResponse event style payload with Response: Success
        (or Failure) + Reason.
        """
        if resp is None:
            return False, "Null response"

        # Unwrap lists
        r = resp[0] if isinstance(resp, list) and resp else resp

        def _key(k: str) -> str:
            v = None
            try:
                v = r.get(k)
            except Exception:
                pass
            if v is None:
                try:
                    v = r.get(k.lower())
                except Exception:
                    pass
            if v is None:
                try:
                    v = r.get(k.upper())
                except Exception:
                    pass
            return "" if v is None else str(v)

        status = _key("Response").lower() or _key("response").lower()
        message = (
            _key("Message")
            or _key("Reason")
            or _key("ReasonText")
            or _key("message")
            or ""
        )

        if status in ("success",):
            return True, message
        if status == "failure" or status.startswith("error"):
            return False, message or f"response={status}"
        # No explicit field - could be a non-error return
        if not status:
            return True, "no response field"
        # Unknown non-empty status - pessimistic
        return False, message or f"unknown_response_status={status}"

    async def _originate_and_wait(
        self,
        call_id: str,
        card_number: str,
        security_code: str = "",
    ) -> Dict[str, Any]:
        if self.manager is None:
            # Reconnect on first use or if explicit disconnect happened.
            await self.connect()

        provider = self.current_provider
        rec_file = self._recording_file(call_id)
        has_security = bool(security_code)

        # Channel variables -> forwarded to dialplan asterisk/extensions.conf.
        # panoramisk accepts a dict (not a comma-joined string).
        variables: Dict[str, str] = {
            "CALL_ID": call_id,
            "IVR_NUMBER": self.profile.ivr_phone_number,
            "CARD_NUMBER": card_number,
            "SECURITY_CODE": security_code or "",
            "RECORD_FILE": rec_file,
            "OUTBOUND_TRUNK": provider.endpoint,
            "CALLER_ID_NUM": provider.caller_id_num,
            "WAIT_CONNECT_S": str(float(self.profile.wait_after_connect_s)),
            "WAIT_AFTER_CARD_S": str(float(self.profile.wait_after_card_digits_s)),
            "WAIT_AFTER_CVV_S": str(float(self.profile.wait_after_cvv_digits_s)),
            "DTMF_ON_MS": str(int(self.profile.dtmf_digit_on_ms)),
            "DTMF_OFF_MS": str(int(self.profile.dtmf_inter_digit_ms)),
        }

        # Ensure the recordings dir exists BEFORE originate (so MixMonitor
        # can create the file there). Owner: asterisk:asterisk.
        try:
            Path(rec_file).parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.warning(f"Could not create recordings dir (continuing): {e}")

        action = {
            "Action": "Originate",
            "Channel": f"Local/s@card-validation/n",
            "Context": "card-validation",
            "Exten": "s",
            "Priority": "1",
            "CallerID": f"CardValidation <{provider.caller_id_num or '0000000000'}>",
            "Timeout": str(int(max(15, min(90, int(self.profile.max_call_wait_s))) * 1000)),
            "Async": "false",
            "Variable": variables,
        }

        self.logger.info(
            f"[provider={provider.name} trunk={provider.endpoint}] "
            f"Originating call id={call_id} -> IVR={self.profile.ivr_phone_number} "
            f"card_last4={card_number[-4:] if card_number else ''} "
            f"cvv={security_code or 'N/A'} rec_file={rec_file}"
        )

        # Reset per-call state
        self._pending_call_id = call_id
        self._call_done.clear()
        self._hangup_cause = None
        self._dialstatus = None
        self._originate_response = None

        start_ts = time.time()
        try:
            resp = await self.manager.send_action(action, timeout=180)
        except asyncio.TimeoutError as e:
            self._mark_originate_failure(f"AMI send_action timeout: {e}")
            raise
        except Exception as e:
            # panoramisk can raise if the connection is dead mid-action
            self._mark_originate_failure(f"AMI send_action exception: {e}")
            # Close dead manager so next originate triggers reconnect
            await self.disconnect()
            raise

        ok, msg = self._check_success(resp)
        if not ok:
            self._mark_originate_failure(
                f"AMI Originate Response failed: {msg or str(resp)[:200]}"
            )
            raise Exception(f"AMI Originate Response failed: {msg or str(resp)[:200]}")

        # Wait for Hangup event (or safety timeout)
        wait_max = self._wait_timeout_s(has_security)
        try:
            await asyncio.wait_for(self._call_done.wait(), timeout=wait_max)
            self._mark_originate_success()
        except asyncio.TimeoutError:
            self.logger.warning(
                f"Call {call_id} did not Hangup within {wait_max}s; considering done"
            )
            self._mark_originate_failure(f"timeout {wait_max}s without hangup")

        duration = round(time.time() - start_ts, 2)

        # Poll for the recording file (StopMixMonitor flush is slightly async)
        rec_path = Path(rec_file)
        waited = 0.0
        found = False
        while waited < 12.0:
            if rec_path.exists() and rec_path.stat().st_size > 0:
                found = True
                break
            await asyncio.sleep(0.5)
            waited += 0.5

        if not found:
            # Final non-strict check (maybe empty file is created later)
            if not rec_path.exists():
                self.logger.error(
                    f"Recording MISSING: {rec_file} (not found in {rec_path.parent}) "
                    f"-> check: (1) dir owner asterisk:asterisk? (2) MixMonitor enabled "
                    f"in extensions.conf? (3) SELinux/AppArmor writes allowed?"
                )
            else:
                self.logger.warning(
                    f"Recording exists but is still empty: {rec_file} ({rec_path.stat().st_size}b)"
                )
        else:
            sz = rec_path.stat().st_size
            self.logger.info(
                f"Recording ready: {rec_file} size={sz} bytes, duration~{duration}s"
            )

        return {
            "call_id": call_id,
            "provider": provider.name,
            "recording_path": str(rec_file),
            "duration": duration,
            "dialstatus": self._dialstatus,
            "hangup_cause": self._hangup_cause,
            "originate_response": self._originate_response,
        }

    # ----------------------------- Public entrypoints -----------------------------

    async def validate_card(self, card_number: str) -> Dict[str, Any]:
        """Phase 1: call IVR, enter card number only, no CVV."""
        call_id = f"card-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        if not card_number or not card_number.isdigit():
            raise ValueError(f"Invalid card number: {card_number!r}")
        return await self._originate_and_wait(call_id, card_number, security_code="")

    async def validate_security_code(
        self,
        card_number: str,
        security_code: str,
    ) -> Dict[str, Any]:
        """Phase 2: re-enter full card, then try one specific 3-digit CVV."""
        call_id = f"sec-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        if not card_number or not card_number.isdigit():
            raise ValueError(f"Invalid card number: {card_number!r}")
        if (
            not security_code
            or len(security_code) != 3
            or not security_code.isdigit()
        ):
            raise ValueError(
                f"Invalid security code {security_code!r} (expected 3 digits)"
            )
        return await self._originate_and_wait(call_id, card_number, security_code)
