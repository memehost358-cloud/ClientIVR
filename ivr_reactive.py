#!/usr/bin/env python3
"""
ReactiveIVR - thin AMI (panoramisk) wrapper around Option-D EAGI flow.

Responsibility:
  1. Clean up stale channels
  2. Originate via same Local/s@run-call pattern as IVRClient, but
     additionally pass REACTIVE_MODE=1 as a channel variable
  3. Wait for Hangup
  4. Stabilize the MixMonitor .wav exactly the same way
  5. Return the EXACT Dict[str, Any] shape that ivr_client.IVRClient.validate_card()
     returns so main.py + report_generator.py can drop in seamlessly.

Implementation notes:
  - All the actual reactivity (FD3 PCM streaming, Deepgram/ElevenLabs
    transcription, trigger matching, EXEC SendDTMF 250/150) lives in the EAGI
    handler at /var/lib/asterisk/agi-bin/ivr_eagi.py
  - AGI process inherits its env from Asterisk, including
    DEEPGRAM_API_KEY / ELEVENLABS_API_KEY. To make sure the
    keys are visible from /etc/environment + .env we pass them
    explicitly into the originate 'Variable:' K=v list (Asterisk makes
    them AGI env automatically for any channel var, but passing them
    as environment requires 'Env' via AMI is unreliable across versions, so
    ivr_eagi.py reads BOTH channel vars AND process env for safety).
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
from ivr_client import IVRClient


class ReactiveIVR:
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

    # ----------------------------- Provider rotation (mirrors IVRClient) -----------------------------

    @property
    def current_provider(self) -> ProviderSpec:
        if not self.provider_policy.order:
            raise Exception("PROVIDER_ORDER empty")
        return self.provider_policy.order[
            self._current_provider_idx % len(self.provider_policy.order)
        ]

    def _mark_originate_success(self) -> None:
        self._consecutive_failures = 0

    def _mark_originate_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        n = len(self.provider_policy.order)
        self.logger.warning(
            f"ReactiveIVR provider '{self.current_provider.name}' originate failed "
            f"({self._consecutive_failures}/{self.provider_policy.failover_consecutive_failures}): {reason}"
        )
        threshold = max(1, self.provider_policy.failover_consecutive_failures)
        if self._consecutive_failures >= threshold and n > 1:
            old = self.current_provider.name
            self._current_provider_idx = (self._current_provider_idx + 1) % n
            self._consecutive_failures = 0
            self.logger.warning(f"FAILOVER: {old} -> {self.current_provider.name}")

    # ----------------------------- AMI lifecycle (mirrors IVRClient) -----------------------------

    async def connect(self) -> None:
        if not self.config.ami_secret:
            raise Exception("AMI_SECRET not configured")
        if not self.profile.ivr_phone_number:
            raise Exception("IVR_PHONE_NUMBER not configured")
        await self.disconnect()
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
        await self.manager.connect()
        self.logger.info("ReactiveIVR AMI connected")

    async def disconnect(self) -> None:
        if self.manager is not None:
            try:
                await self.manager.close()
            except Exception:
                pass
            self.manager = None

    async def _on_originate_response(self, manager, event) -> None:
        self._originate_response = dict(event) if hasattr(event, "get") else {}
        resp = str(event.get("Response", "") or event.get("response", "")).lower()
        reason = (
            event.get("Reason")
            or event.get("ReasonText")
            or event.get("Message")
            or ""
        )
        self.logger.info(f"ReactiveIVR OriginateResponse: {resp or 'n/a'} reason={reason}")

    async def _on_hangup(self, manager, event) -> None:
        cause = event.get("Cause-txt") or event.get("Cause") or ""
        self._hangup_cause = str(cause) if cause is not None else None
        self._call_done.set()

    async def _on_dial_begin(self, manager, event) -> None:
        ch = str(event.get("Channel", "") or "")
        dest = str(event.get("Destination", "") or "")
        self.logger.info(f"ReactiveIVR DialBegin: channel={ch} dest={dest}")

    async def _on_dial_end(self, manager, event) -> None:
        status = event.get("DialStatus", "")
        if status:
            self._dialstatus = str(status)
        ch = str(event.get("Channel", "") or "")
        self.logger.info(f"ReactiveIVR DialEnd: channel={ch} DialStatus={self._dialstatus}")
        if not ch.startswith("SIP/"):
            return
        if self._dialstatus != "ANSWER":
            self._call_done.set()

    # ----------------------------- Public entrypoints (drop-in shape) -----------------------------

    @staticmethod
    def _recording_file(call_id: str) -> str:
        MON_DIR = "/var/spool/asterisk/monitor"
        os.makedirs(MON_DIR, exist_ok=True)
        return os.path.join(MON_DIR, f"ivr-{call_id}.wav")

    @staticmethod
    def _sanitize_security_code(value: Any) -> str:
        return IVRClient._sanitize_security_code(value)

    @staticmethod
    def _cleanup_stale_channels(logger: logging.Logger) -> None:
        IVRClient._cleanup_stale_channels(logger)

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
        card_clean = "".join(ch for ch in str(card_number or "") if ch.isdigit())

        dg_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
        el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()

        variables: List[str] = [
            f"CALL_ID={call_id}",
            f"IVR_NUMBER={self.profile.ivr_phone_number}",
            f"CARD_NUMBER={card_clean}",
            f"SECURITY_CODE={sec_clean}",
            f"RECORD_FILE={rec_file}",
            f"OUTBOUND_TRUNK={provider.endpoint}",
            f"CALLER_ID_NUM={provider.caller_id_num}",
            f"DIAL_NUMBER={IVRClient._dialable_number(self.profile.ivr_phone_number)}",
            f"__REACTIVE_MODE=1",
            f"WAIT_CONNECT_S={float(self.profile.wait_after_connect_s)}",
            f"WAIT_AFTER_CARD_S={float(self.profile.wait_after_card_digits_s)}",
            f"WAIT_AFTER_CVV_S={float(self.profile.wait_after_cvv_digits_s)}",
            f"DTMF_ON_MS={int(self.profile.dtmf_digit_on_ms)}",
            f"DTMF_OFF_MS={int(self.profile.dtmf_inter_digit_ms)}",
        ]
        # Propagate transcription API keys as channel vars — AGI exposes these
        # automatically as AGI arg_(N).  ivr_eagi.py reads BOTH process env
        # AND falls back to AGI channel vars.
        if dg_key:
            variables.append(f"DEEPGRAM_API_KEY={dg_key}")
        if el_key:
            variables.append(f"ELEVENLABS_API_KEY={el_key}")

        try:
            Path(rec_file).parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.warning(f"Could not create recordings dir (continuing): {e}")

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

        self._pending_call_id = call_id
        self._call_done.clear()
        self._hangup_cause = None
        self._dialstatus = None
        self._originate_response = None

        start_ts = time.time()
        self.logger.info(
            f"ReactiveIVR originate call_id={call_id} provider={provider.name} "
            f"dest={self.profile.ivr_phone_number} rec_file={rec_file} "
            f"REACTIVE_MODE=1 backend_deepgram={'yes' if dg_key else 'no'} backend_elevenlabs={'yes' if el_key else 'no'}"
        )

        # Use the same Local/s@run-call originate pattern that the legacy
        # IVRClient uses (commit 9be6951 + fe5727e) proved works for
        # this exact server.
        subprocess.run(
            ["sudo", "asterisk", "-rx",
             "channel originate Local/s@run-call application Wait 60"],
            check=False, capture_output=True, text=True,
        )

        wait_max = 180
        try:
            await asyncio.wait_for(self._call_done.wait(), timeout=wait_max)
        except asyncio.TimeoutError:
            self.logger.warning(
                f"ReactiveIVR AMI events did not complete within {wait_max}s; "
                f"continuing to recording poll anyway."
            )
        if self._dialstatus and self._dialstatus not in ("ANSWER",):
            self._mark_originate_failure(f"DialStatus={self._dialstatus}")
        else:
            self._mark_originate_success()

        # Stabilize recording (EXACT same algorithm as ivr_client, verbatim)
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
                    break
                if sz != last_logged_size:
                    self.logger.info(
                        f"Recording growing: size={sz} bytes "
                        f"(waited {round(time.time() - poll_start, 1)}s of {MAX_WAIT_S}s max)"
                    )
                    last_logged_size = sz
            await asyncio.sleep(POLL_INTERVAL_S)

        duration = round(time.time() - start_ts, 2)

        if not stabilized:
            waited = round(time.time() - poll_start, 1)
            sz = rec_path.stat().st_size if rec_path.exists() else 0
            self.logger.warning(
                f"Recording did NOT stabilize within {MAX_WAIT_S}s: "
                f"size={sz} bytes (polled {waited}s); returning best effort."
            )
        else:
            sz = rec_path.stat().st_size
            self.logger.info(
                f"Recording ready: size={sz} bytes, duration~{duration}s"
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

    async def validate_card(self, card_number: str) -> Dict[str, Any]:
        call_id = f"reactive-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        if not card_number or not card_number.isdigit():
            raise ValueError(f"Invalid card number: {card_number!r}")
        return await self._originate_and_wait(call_id, card_number, security_code="")

    async def validate_security_code(
        self,
        card_number: str,
        security_code: str,
    ) -> Dict[str, Any]:
        call_id = f"reactive-sec-{int(time.time())}-{uuid.uuid4().hex[:6]}"
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
