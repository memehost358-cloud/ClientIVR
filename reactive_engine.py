#!/usr/bin/env python3
"""
Reactive IVR Engine — AMI-driven DTMF loop that listens to the growing
MixMonitor WAV, transcribes IVR prompts, and sends DTMF via AMI SendDTMF.

This replaces the old EAGI-based reactivity (which requires app_eagi.so —
not installed on this server) with a pure-Python loop running in the
ivr_reactive.py process.

Constraints:
- No dialplan changes. The reactive branch is Wait(180) — this loop runs
  alongside it and drives DTMF via AMI.
- No EAGI, no /var/lib/asterisk/agi-bin/.
- Uses existing venv deps: panoramisk, requests, python-dotenv.
- wave module for in-memory WAV conversion (stdlib only).
- Reads DEEPGRAM_API_KEY / ELEVENLABS_API_KEY from os.environ.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Set

# Attempt to import optional backends — they may not be installed
try:
    import requests
except ImportError:
    requests = None  # type: ignore

try:
    import json
except ImportError:
    json = None  # type: ignore

from panoramisk import Manager


# ─── Constants ───
SAMPLE_RATE = 8000
BYTES_PER_SAMPLE = 2
CHUNK_SECONDS = 1.2
OVERLAP_SECONDS = 0.6
CHUNK_BYTES = int(SAMPLE_RATE * BYTES_PER_SAMPLE * CHUNK_SECONDS)       # 19200
OVERLAP_BYTES = int(SAMPLE_RATE * BYTES_PER_SAMPLE * OVERLAP_SECONDS)   # 9600
MAX_CALL_SECONDS = 180
WAV_HEADER_BYTES = 44  # MixMonitor writes a 44-byte header before PCM data

# ─── Trigger phrases derived from actual TD IVR transcript ───
# Matching is case-insensitive, substring.
CARD_NUMBER_TRIGGERS = [
    "enter your card number",
    "please enter or say your card number",
    "enter or say your card number",
]

SECURITY_CODE_TRIGGERS = [
    "security code",
    "cvv",
    "3-digit",
    "3 digit",
]

INVALID_SELECTION_TRIGGERS = [
    "invalid selection",
    "didn't get that",
    "try again",
    "I'm sorry. I did not receive your response",
]

ENGLISH_TRIGGERS = [
    "press one for english",
    "for english press 1",
    "press 1 for english",
    "press 1",
]

FRENCH_TRIGGERS = [
    "press two for french",
    "for french press 2",
    "press 2 for french",
    "press 2",
]

TERMINAL_TRIGGERS = [
    "transferring",
    "goodbye",
    "thank you for calling",
    "please hang up",
]


# ─── State dataclass ───
@dataclass
class ReactiveState:
    """Holds the running transcript, DTMF history, and terminal flag."""
    transcript: str = ""
    dtmf_sent: List[str] = field(default_factory=list)
    last_dtmf_sent: Optional[str] = None
    triggered: Set[str] = field(default_factory=set)
    terminal: bool = False
    call_sid: Optional[str] = None  # AMI channel uniqueid for logging


# ─── Transcription backend protocol ───
class TranscriptionBackend(Protocol):
    """Protocol for STT backends."""
    async def transcribe(self, pcm_bytes: bytes) -> str:
        ...


class DeepgramBackend:
    """Deepgram Nova-2 streaming transcription (REST, not websocket)."""
    def __init__(self, api_key: str, logger: logging.Logger):
        if requests is None:
            raise RuntimeError("requests not installed — cannot use DeepgramBackend")
        self.api_key = api_key
        self.logger = logger
        self.url = "https://api.deepgram.com/v1/listen?model=nova-2&language=en&smart_format=true"

    async def transcribe(self, pcm_bytes: bytes) -> str:
        if not self.api_key:
            return ""
        wav_bytes = _build_wav_in_memory(pcm_bytes)
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    self.url,
                    headers={"Authorization": f"Token {self.api_key}"},
                    data=wav_bytes,
                    timeout=15,
                ),
            )
            if resp.status_code != 200:
                self.logger.warning(f"Deepgram HTTP {resp.status_code}: {resp.text[:200]}")
                return ""
            data = resp.json()
            transcript = data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0].get("transcript", "")
            return transcript.strip()
        except Exception as e:
            self.logger.warning(f"Deepgram error: {e}")
            return ""


class ElevenLabsBackend:
    """ElevenLabs Scribe v1 transcription (REST)."""
    def __init__(self, api_key: str, logger: logging.Logger):
        if requests is None:
            raise RuntimeError("requests not installed — cannot use ElevenLabsBackend")
        self.api_key = api_key
        self.logger = logger
        self.url = "https://api.elevenlabs.io/v1/speech-to-text"

    async def transcribe(self, pcm_bytes: bytes) -> str:
        if not self.api_key:
            return ""
        wav_bytes = _build_wav_in_memory(pcm_bytes)
        try:
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: requests.post(
                    self.url,
                    headers={"xi-api-key": self.api_key},
                    files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                    data={"model_id": "scribe_v1"},
                    timeout=20,
                ),
            )
            if resp.status_code != 200:
                self.logger.warning(f"ElevenLabs HTTP {resp.status_code}: {resp.text[:200]}")
                return ""
            data = resp.json()
            transcript = data.get("text", "")
            return transcript.strip()
        except Exception as e:
            self.logger.warning(f"ElevenLabs error: {e}")
            return ""


def _build_wav_in_memory(pcm_bytes: bytes) -> bytes:
    """Wrap raw 8kHz 16-bit mono PCM into a WAV container in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def build_backends(logger: logging.Logger) -> List[TranscriptionBackend]:
    """Create available backends from environment keys. Returns list (try in order)."""
    backends: List[TranscriptionBackend] = []
    dg_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    if dg_key:
        backends.append(DeepgramBackend(dg_key, logger))
    el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if el_key:
        backends.append(ElevenLabsBackend(el_key, logger))
    return backends


def _match_trigger(text: str, triggers: List[str]) -> bool:
    """Case-insensitive substring match."""
    text_lower = text.lower()
    return any(t.lower() in text_lower for t in triggers)


def state_decide(state: ReactiveState, new_text: str, card_number: str, security_code: str) -> Optional[str]:
    """
    Decide what DTMF to send based on the latest transcript snippet.
    Returns the digit string to send, or None if nothing to press.
    """
    if state.terminal:
        return None

    # Append new text
    state.transcript += " " + new_text

    # 1. Terminal triggers
    if _match_trigger(state.transcript, TERMINAL_TRIGGERS):
        state.terminal = True
        return None

    # 2. English prompt — press 1
    if "english_1" not in state.triggered and _match_trigger(state.transcript, ENGLISH_TRIGGERS):
        state.triggered.add("english_1")
        state.dtmf_sent.append("1")
        state.last_dtmf_sent = "1"
        return "1"

    # 3. French prompt — record and fall through (do NOT block the card check)
    if "french_2" not in state.triggered and _match_trigger(state.transcript, FRENCH_TRIGGERS):
        state.triggered.add("french_2")

    # 4. Card number prompt — send the 16-digit card + "#"
    if "card_number" not in state.triggered and _match_trigger(state.transcript, CARD_NUMBER_TRIGGERS):
        state.triggered.add("card_number")
        digits_to_send = card_number + "#"
        state.dtmf_sent.append(digits_to_send)
        state.last_dtmf_sent = digits_to_send
        return digits_to_send

    # 5. Security code / CVV prompt — send the 3-digit CVV
    if "security_code" not in state.triggered and _match_trigger(state.transcript, SECURITY_CODE_TRIGGERS):
        state.triggered.add("security_code")
        state.dtmf_sent.append(security_code)
        state.last_dtmf_sent = security_code
        return security_code

    # 6. Invalid selection / retry — resend last DTMF (or card if nothing sent yet)
    if _match_trigger(state.transcript, INVALID_SELECTION_TRIGGERS):
        to_resend = state.last_dtmf_sent or card_number
        state.dtmf_sent.append(to_resend)
        state.last_dtmf_sent = to_resend
        return to_resend

    return None


class ReactiveEngine:
    """
    Main reactive loop. Tails the growing MixMonitor WAV, transcribes chunks,
    and sends DTMF via AMI SendDTMF.
    """
    def __init__(
        self,
        manager: Manager,
        rec_file: str,
        card_number: str,
        security_code: str,
        logger: logging.Logger,
        max_seconds: int = MAX_CALL_SECONDS,
    ):
        self.manager = manager
        self.rec_file: str = rec_file
        self.rec_path: Path = Path(rec_file)
        self.card_number = card_number
        self.security_code = security_code
        self.logger = logger
        self.max_seconds = max_seconds
        self.state = ReactiveState()
        self.backends = build_backends(logger)
        self.sip_channel: Optional[str] = None
        self._file_cursor: int = WAV_HEADER_BYTES  # skip 44-byte WAV header
        self._buffer = bytearray()
        self._start_time = time.time()

        if not self.backends:
            self.logger.warning("No transcription backends available (no API keys)")

    async def run(self) -> Dict[str, Any]:
        """Run the reactive loop until terminal state or timeout."""
        self.logger.info(f"ReactiveEngine starting: rec_file={self.rec_file} max={self.max_seconds}s")
        self.chunks_sent = 0
        self._last_heartbeat = 0.0

        # Start a background task to wait for the SIP channel via AMI Newchannel
        channel_task = asyncio.create_task(self._wait_for_sip_channel())

        try:
            self.logger.info(f"ReactiveEngine: entering main loop, rec_file={self.rec_file}")
            while time.time() - self._start_time < self.max_seconds:
                now = time.time()
                if now - self._last_heartbeat >= 2.0:
                    try:
                        file_size = self.rec_path.stat().st_size if self.rec_path.exists() else 0
                    except Exception:
                        file_size = -1
                    self.logger.info(
                        f"ReactiveEngine: tick file_size={file_size} "
                        f"buffered={len(self._buffer)} "
                        f"chunks_sent={self.chunks_sent} "
                        f"elapsed={int(now - self._start_time)}s"
                    )
                    self._last_heartbeat = now

                # Check if terminal
                if self.state.terminal:
                    self.logger.info("ReactiveEngine: terminal state reached")
                    break

                # Read new PCM from the growing file
                pcm_chunk = self._read_new_pcm()
                if pcm_chunk:
                    self._buffer.extend(pcm_chunk)

                    # Process complete chunks with overlap
                    while len(self._buffer) >= CHUNK_BYTES:
                        chunk = bytes(self._buffer[:CHUNK_BYTES])
                        self.logger.info(f"ReactiveEngine: chunk emitted len={len(chunk)}")
                        self.chunks_sent += 1
                        # Keep overlap for next chunk
                        del self._buffer[:CHUNK_BYTES - OVERLAP_BYTES]

                        # Transcribe
                        transcript = await self._transcribe_chunk(chunk)
                        if transcript:
                            self.logger.info(f"Transcript: {transcript}")
                            dtmf = state_decide(
                                self.state,
                                transcript,
                                self.card_number,
                                self.security_code,
                            )
                            if dtmf:
                                await self._send_dtmf(dtmf)
                else:
                    # No new data yet, wait a bit
                    await asyncio.sleep(0.5)

            # Timeout or terminal — hang up the SIP channel
            if self.sip_channel:
                self.logger.info(f"ReactiveEngine: hanging up SIP channel {self.sip_channel}")
                await self._hangup(self.sip_channel)
            else:
                self.logger.warning("ReactiveEngine: no SIP channel found, cannot hangup")

        except Exception as e:
            self.logger.exception(f"ReactiveEngine error: {e}")
        finally:
            channel_task.cancel()
            try:
                await channel_task
            except asyncio.CancelledError:
                pass

        return {
            "dtmf_sent": self.state.dtmf_sent,
            "transcript": self.state.transcript,
            "terminal": self.state.terminal,
        }

    def _read_new_pcm(self) -> Optional[bytes]:
        """Read new PCM bytes from the MixMonitor file (skipping 44-byte header)."""
        try:
            if not self.rec_path.exists():
                return None
            size = self.rec_path.stat().st_size
            # If the underlying file shrank (MixMonitor truncated / recreated
            # the WAV between reads) the in-memory cursor would stay forever
            # ahead of size, producing None forever. Re-sync past the new header
            # and let the next read grab fresh PCM from the new file.
            if size < self._file_cursor:
                self.logger.warning(
                    f"ReactiveEngine: WAV shrank (size={size} < cursor={self._file_cursor}). "
                    f"Re-syncing cursor past {WAV_HEADER_BYTES}-byte header."
                )
                self._file_cursor = WAV_HEADER_BYTES
            if size <= self._file_cursor:
                return None
            with self.rec_path.open("rb") as f:
                f.seek(self._file_cursor)
                data = f.read(size - self._file_cursor)
            self._file_cursor = size
            return data
        except Exception as e:
            self.logger.warning(f"Read error: {e}")
            return None

    async def _transcribe_chunk(self, pcm_chunk: bytes) -> str:
        """Send chunk to backends in order, return first non-empty transcript."""
        for backend in self.backends:
            try:
                transcript = await backend.transcribe(pcm_chunk)
                if transcript:
                    return transcript
            except Exception as e:
                self.logger.warning(f"Backend {backend.__class__.__name__} failed: {e}")
        return ""

    async def _wait_for_sip_channel(self) -> None:
        """
        Wait for AMI Newchannel event with Channel starting with 'SIP/signalwire-'.
        This is the correct way to get the live SIP channel for SendDTMF/Hangup.
        The Local/s@run-call pair appears first; we ignore it.
        """
        future = asyncio.get_event_loop().create_future()

        def on_newchannel(manager, event):
            if future.done():
                return
            ch = str(event.get("Channel", "") or "")
            if ch.startswith("SIP/signalwire-"):
                self.logger.info(f"ReactiveEngine: captured SIP channel {ch}")
                future.set_result(ch)

        self.manager.register_event("Newchannel", on_newchannel)
        try:
            # Wait up to 30s for SIP channel to appear
            self.sip_channel = await asyncio.wait_for(future, timeout=30.0)
        except asyncio.TimeoutError:
            self.logger.error("ReactiveEngine: SIP channel not found within 30s — DTMF will not be sent")
        finally:
            try:
                self.manager.unregister_event("Newchannel", on_newchannel)
            except Exception as e:
                self.logger.debug(f"unregister skipped: {e}")

    async def _send_dtmf(self, digits: str) -> None:
        """Send DTMF via dialplan SendDTMF() application using SetVar + Redirect."""
        if not self.sip_channel:
            self.logger.warning(f"Cannot send DTMF {digits}: no SIP channel yet")
            return
        try:
            if digits.isdigit() and len(digits) > 4:
                masked = "****" + digits[-4:]
            else:
                masked = digits
            self.logger.info(f"ReactiveEngine: SEND DTMF via dialplan to {self.sip_channel}: digits={masked}")

            # Set channel variable with digits to send
            await self.manager.send_action({
                "Action": "SetVar",
                "Channel": self.sip_channel,
                "Variable": "SEND_DTMF_DIGITS",
                "Value": digits,
            })

            # Redirect to dialplan context that executes SendDTMF() then returns
            resp = await self.manager.send_action({
                "Action": "Redirect",
                "Channel": self.sip_channel,
                "Context": "send-dtmf",
                "Exten": "s",
                "Priority": "1",
            })
            self.logger.info(f"Dialplan DTMF redirect response: {resp}")
        except Exception as e:
            self.logger.warning(f"Dialplan SendDTMF failed: {e}")

    async def _hangup(self, channel: str) -> None:
        """Hang up the SIP channel via AMI Hangup."""
        try:
            await self.manager.send_action({
                "Action": "Hangup",
                "Channel": channel,
            })
        except Exception as e:
            self.logger.warning(f"Hangup failed: {e}")