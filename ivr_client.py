#!/usr/bin/env python3

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Dict, Any

from panoramisk.manager import Manager

from config import Config


class IVRClient:
    """
    Asterisk AMI client.

    Telephony path:

        Python
          -> Asterisk AMI
          -> card-validation dialplan
          -> SIP/SignalWire
          -> IVR
          -> MixMonitor
          -> recording file
    """

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.manager = None
        self.call_state: Dict[str, Any] = {}

    async def connect(self) -> None:
        """Connect to the local Asterisk AMI."""
        self.manager = Manager(
            host=self.config.ami_host,
            port=self.config.ami_port,
            username=self.config.ami_username,
            secret=self.config.ami_secret,
        )

        await self.manager.connect()

        self.logger.info(
            "Connected to Asterisk AMI %s:%s",
            self.config.ami_host,
            self.config.ami_port,
        )

    async def disconnect(self) -> None:
        """Close the AMI connection."""
        if self.manager is not None:
            self.manager.close()
            self.manager = None
            self.logger.info("Disconnected from Asterisk AMI")

    async def validate_card(self, card_number: str) -> Dict[str, Any]:
        """
        Originate a real outbound call through the Asterisk dialplan.

        The dialplan is responsible for:
          - calling SignalWire
          - entering the IVR
          - sending the card number
          - recording the call
        """

        if self.manager is None:
            raise RuntimeError("AMI connection is not established")

        call_id = f"card-validation-{uuid.uuid4().hex}"
        recording_path = (
            Path(self.config.recordings_dir) / f"{call_id}.wav"
        )

        masked_card = self._mask_card_number(card_number)

        self.logger.info(
            "Starting Asterisk IVR call %s for card %s",
            call_id,
            masked_card,
        )

        originate = {
            "Action": "Originate",
            "ActionID": call_id,

            # Local channel enters our card-validation dialplan.
            "Channel": "Local/s@card-validation",

            "Context": "card-validation",
            "Exten": "s",
            "Priority": "1",

            "Timeout": "120000",

            "CallerID": self.config.signalwire_phone_number,

            "Async": "true",

            "Variable": [
                f"CALL_ID={call_id}",
                f"IVR_NUMBER={self.config.ivr_phone_number}",
                f"SIGNALWIRE_PHONE_NUMBER={self.config.signalwire_phone_number}",
                f"RECORD_FILE={recording_path}",
                f"CARD_NUMBER={card_number}",
                "SECURITY_CODE=",
            ],
        }

        self.call_state[call_id] = {
            "status": "originating",
            "recording_path": str(recording_path),
            "started_at": time.time(),
        }

        try:
            response = await self.manager.send_action(originate)

            self.logger.info(
                "Asterisk Originate response: %s",
                response,
            )

            if response is None:
                raise RuntimeError(
                    "Asterisk returned no Originate response"
                )

            response_text = str(response)

            if "Error" in response_text or "Failed" in response_text:
                raise RuntimeError(
                    f"Asterisk Originate failed: {response_text}"
                )

            self.call_state[call_id]["status"] = "originated"

            return await self._wait_for_recording(
                call_id=call_id,
                recording_path=recording_path,
                timeout_seconds=150,
            )

        except Exception:
            self.call_state[call_id]["status"] = "failed"
            raise

    async def validate_security_code(
        self,
        card_number: str,
        security_code: str,
    ) -> Dict[str, Any]:
        """
        Security-code transport boundary.

        The validator supplies a candidate here and expects a real
        telephony result from an approved transport implementation.
        The candidate-generation controller remains in card_validator.py.
        """
        if self.manager is None:
            raise RuntimeError("AMI connection is not established")

        if not security_code or len(security_code) != 3 or not security_code.isdigit():
            raise ValueError("Security code candidate must be exactly 3 digits")

        raise NotImplementedError(
            "Security-code telephony transport is not connected yet. "
            "The candidate-generation controller remains intact."
        )

    async def _wait_for_recording(
        self,
        call_id: str,
        recording_path: Path,
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        """
        Wait for the actual MixMonitor file.

        No artificial recording result is returned. The call succeeds here
        only when Asterisk has actually created the recording.
        """

        started = time.monotonic()

        while time.monotonic() - started < timeout_seconds:

            if recording_path.exists():
                size = recording_path.stat().st_size

                if size > 0:
                    duration = time.time() - self.call_state[
                        call_id
                    ]["started_at"]

                    self.call_state[call_id]["status"] = "recorded"

                    self.logger.info(
                        "Actual recording detected: %s (%d bytes)",
                        recording_path,
                        size,
                    )

                    return {
                        "call_id": call_id,
                        "recording_path": str(recording_path),
                        "duration": duration,
                        "status": "completed",
                    }

            await asyncio.sleep(1)

        self.call_state[call_id]["status"] = "timeout"

        raise TimeoutError(
            f"Asterisk did not create the expected recording within "
            f"{timeout_seconds} seconds: {recording_path}"
        )

    @staticmethod
    def _mask_card_number(card_number: str) -> str:
        if len(card_number) <= 4:
            return "*" * len(card_number)

        return "*" * (len(card_number) - 4) + card_number[-4:]
