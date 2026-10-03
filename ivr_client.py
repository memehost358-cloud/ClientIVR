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
    a single comma-separated string). We pass a list of "K=v" strings
    so panoramisk emits one "Variable: K=v" AMI header per item.
  * The Originate action has a "Channel" (the outgoing leg), plus the
    optional Context/Exten/Priority/Application for the B-leg. This
    client uses a direct SIP originate and runs Gosub on the answered
    outbound channel.

Waits for Hangup AMI event, then polls for the .wav recording file
written by MixMonitor in the dialplan. Returns recording_path so the
caller can pass it straight to TranscriptionService.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
            self.manager.register_event("DialBegin", self._on_dial_begin)
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
        # NOTE: with Async=true the OriginateResponse often reports
        # "Error" even though the SIP call is still in progress. We now rely
        # on DialBegin/DialEnd events on the SIP/ channel to detect success
        # or failure. Do not short-circuit here.
        pass

    async def _on_hangup(self, manager, event) -> None:
        cause = event.get("Cause-txt") or event.get("Cause") or ""
        self._hangup_cause = str(cause) if cause is not None else None
        self.logger.debug(f"Hangup event cause={self._hangup_cause}")
        # Call is over once the IVR hangs up our outgoing leg
        self._call_done.set()

    async def _on_dial_begin(self, manager, event) -> None:
        channel = str(event.get("Channel", "") or "")
        dest    = str(event.get("Destination", "") or "")
        self.logger.info(f"DialBegin event: channel={channel} dest={dest}")

    async def _on_dial_end(self, manager, event) -> None:
        channel = str(event.get("Channel", "") or "")
        status  = event.get("DialStatus", "")
        if not status:
            return
        self._dialstatus = str(status)
        self.logger.info(f"DialEnd event: channel={channel} DialStatus={self._dialstatus}")
        if not channel.startswith("SIP/"):
            return
        if self._dialstatus == "ANSWER":
            # The call is ANSWERED — the dialplan will keep running
            # (DTMF sequence, recording, etc.) until it calls Hangup().
            # Do NOT set _call_done here; wait for the Hangup event.
            self.logger.info("DialEnd=ANSWER — call active, waiting for Hangup event")
            return
        # Non-answer status (BUSY, NOANSWER, CONGESTION, CANCEL) — call is over
        self._call_done.set()

    # ----------------------------- Helpers -----------------------------

    def _recording_file(self, call_id: str) -> str:
        MON_DIR = "/var/spool/asterisk/monitor"
        os.makedirs(MON_DIR, exist_ok=True)
        return os.path.join(MON_DIR, f"ivr-{call_id}.wav")

    @staticmethod
    def _find_recording_anywhere(expected_path: str) -> str:
        import glob as _glob
        import shutil as _shutil
        if os.path.exists(expected_path) and os.path.getsize(expected_path) > 44:
            return expected_path
        bn = os.path.basename(expected_path)
        call_id = bn.split("ivr-", 1)[1].rsplit(".", 1)[0] if "ivr-" in bn else ""
        search_dirs = [
            "/var/spool/asterisk/monitor",
            "/var/spool/asterisk/recordings",
            "/tmp",
        ]
        found = []
        for d in search_dirs:
            if not os.path.isdir(d):
                continue
            for pat in (f"ivr-{call_id}*.wav", f"*{call_id}*.wav"):
                found.extend(_glob.glob(os.path.join(d, pat)))
        found.sort(
            key=lambda f: os.path.getsize(f) if os.path.exists(f) else 0,
            reverse=True,
        )
        for f in found:
            if os.path.exists(f) and os.path.getsize(f) > 44:
                try:
                    _shutil.copy2(f, expected_path)
                    return expected_path
                except Exception:
                    return f
        return expected_path

    @staticmethod
    def _dialable_number(number: str) -> str:
        # chan_sip peers often expect the request user part as digits only even
        # when the logical destination is stored in E.164 form with a leading +.
        # HOWEVER, SignalWire and many modern providers REQUIRE the + for E.164.
        if not number:
            return ""
        s = str(number).strip()
        prefix = "+" if s.startswith("+") else ""
        digits = "".join(ch for ch in s if ch.isdigit())
        return prefix + digits

    def _outbound_channels(self, endpoint: str, number: str) -> List[str]:
        dial_number = self._dialable_number(number)
        # PRIMARY strategy: use a Local channel that enters [card-validation]
        # internally and lets the DIAL command there actually place the SIP outbound.
        # (This is how Asterisk Originate expects to work and what the
        # Sep-29 14:40 working calls actually succeeded on this server.)
        #
        # Direct-SIP Originate to Application=Gosub also requires the called
        # number to match a SIP dialpeer + endpoint but it's failing INSTANTLY
        # "Originate failed" on both SIP/signalwire/ digits today (the
        # exact same lines did work on Sep 29, so there may be an endpoint
        # registration issue or an AMI/endpoint mismatch -- but Local channel always
        # works for Originate since it just enters the dialplan context.)
        #
        # Ordering:
        #   1) Local/s@card-validation    (PRIMARY, works for this exact box)
        #   2) Local/s@card-validation       (no /n option just in case)
        candidates = [
            f"SIP/{endpoint}/{number}",
        ]
        return candidates

    def _wait_timeout_s(self, has_security: bool) -> int:
        # The TD IVR flow takes ~66 seconds (press 2 at 15s, card at 39s,
        # CVV at 45s, expiry at 51s, then 15s for the result). Ensure we
        # never time out before the whole sequence finishes.
        return 150

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

    @staticmethod
    def _cleanup_stale_channels(logger: logging.Logger) -> None:
        """Hang up leftover Local/s@run-call-* and SIP/* outbound channels from
        prior runs that were left dangling (e.g. due to timeouts or crashes).

        Uses `channel request hangup all` with a pattern-safe fallback:
        list channels with `core show channels concise` and hang up only those
        that belong to this app (Local/s@run-call, SIP/$trunk with the
        validation U()-sub). Safe even if no stale channels exist.
        """
        try:
            list_r = subprocess.run(
                ["sudo", "asterisk", "-rx", "core show channels concise"],
                check=False, capture_output=True, text=True, timeout=15,
            )
            lines = [ln.strip() for ln in (list_r.stdout or "").splitlines() if ln.strip()]
            candidates: List[str] = []
            for ln in lines:
                parts = ln.split("!")
                if not parts:
                    continue
                channel = parts[0]
                if channel.startswith("Local/s@run-call-"):
                    candidates.append(channel)
                elif channel.startswith("SIP/") and "card-val-gosub" in ln:
                    candidates.append(channel)
                elif channel.startswith("SIP/") and "card-validation" in ln:
                    candidates.append(channel)
            if not candidates:
                logger.info("No stale channels found — starting clean.")
                return
            logger.warning(
                f"Found {len(candidates)} stale channel(s); requesting hangup: "
                f"{candidates[:5]}{'...' if len(candidates) > 5 else ''}"
            )
            for ch in candidates:
                subprocess.run(
                    ["sudo", "asterisk", "-rx",
                     f"channel request hangup {ch}"],
                    check=False, capture_output=True, text=True, timeout=10,
                )
                time.sleep(0.2)
        except Exception as e:
            logger.warning(f"Stale channel cleanup skipped (non-fatal): {e}")

    @staticmethod
    def _sanitize_security_code(value: Any) -> str:
        """Return only digits. Treat empty/None/NONE strings as empty."""
        if value is None:
            return ""
        s = str(value).strip()
        if not s:
            return ""
        if s.lower() == "none":
            return ""
        return "".join(ch for ch in s if ch.isdigit())

    async def _originate_and_wait(
        self,
        call_id: str,
        card_number: str,
        security_code: str = "",
    ) -> Dict[str, Any]:
        if self.manager is None:
            await self.connect()

        self._cleanup_stale_channels(self.logger)

        provider = self.current_provider
        rec_file = self._recording_file(call_id)
        sec_clean = self._sanitize_security_code(security_code)
        has_security = bool(sec_clean)

        card_clean = "".join(ch for ch in str(card_number or "") if ch.isdigit())

        variables: List[str] = [
            f"CALL_ID={call_id}",
            f"IVR_NUMBER={self.profile.ivr_phone_number}",
            f"CARD_NUMBER={card_clean}",
            f"SECURITY_CODE={sec_clean}",
            f"RECORD_FILE={rec_file}",
            f"OUTBOUND_TRUNK={provider.endpoint}",
            f"CALLER_ID_NUM={provider.caller_id_num}",
            f"DIAL_NUMBER={self._dialable_number(self.profile.ivr_phone_number)}",
            f"WAIT_CONNECT_S={float(self.profile.wait_after_connect_s)}",
            f"WAIT_AFTER_CARD_S={float(self.profile.wait_after_card_digits_s)}",
            f"WAIT_AFTER_CVV_S={float(self.profile.wait_after_cvv_digits_s)}",
            f"DTMF_ON_MS={int(self.profile.dtmf_digit_on_ms)}",
            f"DTMF_OFF_MS={int(self.profile.dtmf_inter_digit_ms)}",
        ]

        # Ensure the recordings dir exists BEFORE originate (so MixMonitor
        # can create the file there). Owner: asterisk:asterisk.
        try:
            Path(rec_file).parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.warning(f"Could not create recordings dir (continuing): {e}")

        # Direct SIP originate with a Gosub to the recording wrapper card-val-gosub.
        # THIS IS THE PROVEN-WORKING PATTERN ON THIS SERVER (commit 9be6951):
        #   Channel = direct SIP/<trunk>/<digits>
        #   Application = Gosub
        #   Data = context,s,1(args)     <-- runs on the ANSWERED outbound leg
        #
        # NOTE: Context/Exten/Priority does NOT work for direct-SIP outbound
        # Originate on this Asterisk 9.0.0 (AMI rejects instantly "Originate
        # failed" with no attempt to dial).  ONLY Application=Gosub works.
        dial_number = self._dialable_number(self.profile.ivr_phone_number)
        outbound_channels = self._outbound_channels(
            provider.endpoint, self.profile.ivr_phone_number
        )
        gosub_args = f"{rec_file},{card_clean},{sec_clean},{call_id}"

        self._pending_call_id = call_id
        self._call_done.clear()
        self._hangup_cause = None
        self._dialstatus = None
        self._originate_response = None

        start_ts = time.time()

        def _set_global(name: str, value: str) -> None:
            subprocess.run(
                ["sudo", "asterisk", "-rx", f"dialplan set global {name} {value}"],
                check=False, capture_output=True, text=True,
            )

        _set_global("IVR_NUMBER", str(self.profile.ivr_phone_number))
        _set_global("CALL_ID", call_id)
        _set_global("CARD_NUMBER", card_clean)
        _set_global("SECURITY_CODE", sec_clean)
        _set_global("RECORD_FILE", rec_file)
        _set_global("OUTBOUND_TRUNK", provider.endpoint)
        _set_global("CALLER_ID_NUM", provider.caller_id_num or "")

        self.logger.info(
            f"[provider={provider.name}] CLI originate call_id={call_id} "
            f"dest={self.profile.ivr_phone_number} rec_file={rec_file}"
        )

        subprocess.run(
            ["sudo", "asterisk", "-rx",
             "channel originate Local/s@run-call application Wait 60"],
            check=False, capture_output=True, text=True,
        )

        wait_max = self._wait_timeout_s(bool(sec_clean))
        try:
            await asyncio.wait_for(self._call_done.wait(), timeout=wait_max)
        except asyncio.TimeoutError:
            self.logger.warning(
                f"AMI events did not fire _call_done within {wait_max}s; "
                f"continuing to recording poll anyway."
            )
        if self._dialstatus and self._dialstatus not in ("ANSWER",):
            self._mark_originate_failure(f"DialStatus={self._dialstatus}")
        else:
            self._mark_originate_success()

        MAX_WAIT_S: float = 150.0
        POLL_INTERVAL_S: float = 1.0
        STABLE_WINDOW_S: float = 2.0
        MIN_SIZE_BYTES: int = 100 * 1024

        rec_path = Path(rec_file)
        poll_start = time.time()
        history: List[Tuple[float, int]] = []
        last_logged_size = -1
        stabilized = False

        while (time.time() - poll_start) < MAX_WAIT_S:
            if rec_path.exists():
                sz = rec_path.stat().st_size
                now = time.time()
                history.append((now, sz))
                while len(history) > 6:
                    history.pop(0)

                sizes = [s for (_, s) in history]
                t_span = history[-1][0] - history[0][0]
                if (len(sizes) >= 2
                        and len(set(sizes)) == 1
                        and t_span >= STABLE_WINDOW_S
                        and sz > MIN_SIZE_BYTES):
                    stabilized = True
                    waited_total = round(now - poll_start, 1)
                    self.logger.info(
                        f"Recording STABILIZED: {rec_file} size={sz} bytes "
                        f"(>100KB OK, stable for {round(t_span, 1)}s, "
                        f"polled {waited_total}s)"
                    )
                    break

                if sz != last_logged_size:
                    self.logger.info(
                        f"Recording growing: {rec_file} size={sz} bytes "
                        f"(waited {round(time.time() - poll_start, 1)}s of {MAX_WAIT_S}s max)"
                    )
                    last_logged_size = sz

            await asyncio.sleep(POLL_INTERVAL_S)

        duration = round(time.time() - start_ts, 2)

        if not stabilized:
            waited_total = round(time.time() - poll_start, 1)
            final_sz = rec_path.stat().st_size if rec_path.exists() else 0
            if final_sz <= 0 and not rec_path.exists():
                self.logger.error(
                    f"Recording MISSING: {rec_file} (not found in {rec_path.parent}) "
                    f"-> check: (1) dir owner asterisk:asterisk? (2) MixMonitor enabled "
                    f"in extensions.conf? (3) SELinux/AppArmor writes allowed?"
                )
            else:
                self.logger.warning(
                    f"Recording did NOT stabilize within {MAX_WAIT_S}s: "
                    f"{rec_file} size={final_sz} bytes (need >100KB & 2s stable). "
                    f"Polled {waited_total}s; returning best effort (transcription may fail)."
                )
        else:
            sz = rec_path.stat().st_size
            self.logger.info(
                f"Recording ready: {rec_file} size={sz} bytes, total duration~{duration}s"
            )

        return {
            "call_id": call_id,
            "provider": provider.name,
            "recording_path": IVRClient._find_recording_anywhere(str(rec_file)),
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
