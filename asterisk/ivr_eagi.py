#!/usr/bin/env python3
"""
ivr_eagi.py - Enhanced AGI (EAGI) handler for reactive IVR card validation.

Runs on the ANSWERED outbound SIP leg (not the Local wrapper) because
Dial()'s U(card-val-gosub) always executes on the B-leg.
MixMonitor still writes master WAV to disk simultaneously (no conflict).

Invocation from extensions.conf:
  EAGI(/var/lib/asterisk/agi-bin/ivr_eagi.py,
       <card>,<cvv>,<rec_path>,<dtmf_on_ms>,<dtmf_off_ms_reactive>,<pad_s>)

Arguments (comma-separated, 1-indexed per AGI convention):
  ARG1 = CARD_NUMBER         (all digits, already sanitized by dialplan)
  ARG2 = SECURITY_CODE_CVV   ("" = Phase 1, "000".."999" = Phase 2)
  ARG3 = REC_PATH            (absolute wav path, only used for local logging)
  ARG4 = DTMF_ON_MS          (per-digit active tone, e.g. 250)
  ARG5 = DTMF_OFF_MS_REACTIVE(inter-digit gap, MANDATORY pinned 150 for 400ms/digit total)
  ARG6 = EXTRA_HANGUP_PAD_S  (seconds to hold line after final state)

Protocol:
  stdin/out = AGI (initial 20-key env dump on startup, then command pairs)
  fd 3       = 8000 Hz, 16-bit signed LE, mono PCM stream of REMOTE audio
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import struct
import subprocess
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_PATH = "/var/log/asterisk/ivr_eagi.log"
MONO_8K_BPS = 2
FRAME_BYTES_20MS = int(8000 * 0.020 * MONO_8K_BPS)

CHUNK_SECONDS = 1.2
OVERLAP_SECONDS = 0.6
CHUNK_BYTES = int(8000 * CHUNK_SECONDS * MONO_8K_BPS)
OVERLAP_BYTES = int(8000 * OVERLAP_SECONDS * MONO_8K_BPS)

MAX_STREAK_FAIL = 3
MAX_CALL_SECONDS = 180
SAMPLE_RATE = 8000

TRIGGERS = {
    "card": [
        "please enter",
        "enter or say your card number",
        "card number is required",
        "16 digit",
        "16-digit",
        "enter your card",
        "numéro de carte",
    ],
    "cvv": [
        "security code",
        "3-digit security",
        "three digit",
        "3 digit",
        "cvv",
        "cvc",
        "code de sécurité",
        "trois chiffres",
    ],
    "expiry": [
        "expir",
        "expiration",
        "valid thru",
        "mm yy",
        "mm/yy",
        "expiry date",
    ],
    "ok": [
        "activated",
        "activation complete",
        "thank you",
        "success",
        "votre carte est activée",
    ],
    "bad": [
        "invalid card",
        "invalid selection",
        "not recognized",
        "incorrect",
        "try again",
        "does not match",
        "erreur",
    ],
}

STATE_ORDER = ["greeting", "waiting_card", "sent_card", "waiting_cvv", "sent_cvv", "waiting_expiry", "sent_expiry", "waiting_result", "done", "failed"]


# ---------------------------------------------------------------------------
# Logging setup (root-writable agi-bin script → Asterisk runs as user asterisk)
# ---------------------------------------------------------------------------

def _make_logger() -> logging.Logger:
    log_dir = Path(LOG_PATH).parent
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    logger = logging.getLogger("ivr_eagi")
    logger.setLevel(logging.INFO)
    # Avoid double handlers if Asterisk's AGI loader re-invokes
    if not logger.handlers:
        try:
            h = logging.FileHandler(LOG_PATH)
            h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(h)
        except Exception:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(h)
    return logger


log = _make_logger()

# ---------------------------------------------------------------------------
# Transcription backends
# ---------------------------------------------------------------------------

class BackendUnavailable(Exception):
    pass


class TranscriptionBackend:
    name: str = "base"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def transcribe_bytes(self, pcm_bytes: bytes) -> Optional[str]:
        raise NotImplementedError


def _build_wav_in_memory(pcm_bytes: bytes) -> bytes:
    n_frames = len(pcm_bytes) // MONO_8K_BPS
    data_size = n_frames * MONO_8K_BPS
    riff_size = 36 + data_size
    buf = io.BytesIO()
    buf.write(b"RIFF")
    buf.write(struct.pack("<I", riff_size))
    buf.write(b"WAVE")
    buf.write(b"fmt ")
    buf.write(struct.pack("<I", 16))          # PCM fmt chunk size
    buf.write(struct.pack("<H", 1))           # PCM format
    buf.write(struct.pack("<H", 1))           # channels
    buf.write(struct.pack("<I", SAMPLE_RATE)) # sample rate
    buf.write(struct.pack("<I", SAMPLE_RATE * MONO_8K_BPS)) # byte rate
    buf.write(struct.pack("<H", MONO_8K_BPS)) # block align
    buf.write(struct.pack("<H", 16))          # bits per sample
    buf.write(b"data")
    buf.write(struct.pack("<I", data_size))
    buf.write(pcm_bytes[:data_size])
    return buf.getvalue()


class DeepgramBackend(TranscriptionBackend):
    name = "deepgram"

    def transcribe_bytes(self, pcm_bytes: bytes) -> Optional[str]:
        if requests is None:
            raise BackendUnavailable("requests missing")
        if not self.api_key:
            raise BackendUnavailable("DEEPGRAM_API_KEY not set")
        wav = _build_wav_in_memory(pcm_bytes)
        url = (
            "https://api.deepgram.com/v1/listen"
            "?model=nova-2&smart_format=false&punctuate=true&language=en"
        )
        headers = {
            "Authorization": f"Token {self.api_key}",
        }
        files = {"audio": ("chunk.wav", wav, "audio/wav")}
        r = requests.post(url, headers=headers, files=files, timeout=(5, 25))
        if r.status_code != 200:
            raise BackendUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            j = r.json()
            return j["results"]["channels"][0]["alternatives"][0]["transcript"].strip() or None
        except Exception as e:
            raise BackendUnavailable(f"parse fail: {e}")


class ElevenLabsBackend(TranscriptionBackend):
    name = "elevenlabs"

    def transcribe_bytes(self, pcm_bytes: bytes) -> Optional[str]:
        if requests is None:
            raise BackendUnavailable("requests missing")
        if not self.api_key:
            raise BackendUnavailable("ELEVENLABS_API_KEY not set")
        wav = _build_wav_in_memory(pcm_bytes)
        url = "https://api.elevenlabs.io/v1/speech-to-text"
        headers = {"xi-api-key": self.api_key}
        files = {"file": ("chunk.wav", wav, "audio/wav")}
        data = {"model_id": "scribe_v1"}
        r = requests.post(url, headers=headers, files=files, data=data, timeout=(10, 40))
        if r.status_code != 200:
            raise BackendUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            t = r.json().get("text") or r.json().get("transcript") or ""
            return t.strip() or None
        except Exception as e:
            raise BackendUnavailable(f"parse fail: {e}")


# ---------------------------------------------------------------------------
# AGI command helpers
# ---------------------------------------------------------------------------

class AGI:
    def __init__(self):
        self.env: Dict[str, str] = {}
        self._read_env()

    def _read_env(self) -> None:
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                break
            if not line:
                break
            line = line.rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                k, v = line.split(":", 1)
                self.env[k.strip()] = v.strip()
            else:
                self.env[line] = ""

    def arg(self, n: int, default: str = "") -> str:
        """AGI convention: arg_1..arg_N are the comma-separated script args."""
        return self.env.get(f"arg_{n}", default)

    def cmd(self, text: str) -> Tuple[int, str, str]:
        """Send one AGI command. Return (code, result, data)."""
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        line = sys.stdin.readline()
        if not line:
            return 500, "", "EOF"
        line = line.rstrip("\r\n")
        code = 0
        result = ""
        data = ""
        try:
            if line.startswith("200 result="):
                rest = line[len("200 result="):]
                code = 200
                if " " in rest:
                    result, data = rest.split(" ", 1)
                else:
                    result = rest
            else:
                parts = line.split(" ", 2)
                code = int(parts[0]) if parts and parts[0].isdigit() else 500
                result = parts[1] if len(parts) > 1 else ""
                data = parts[2] if len(parts) > 2 else ""
        except Exception:
            pass
        return code, result, data.strip()

    def exec_app(self, app: str, args: str = "") -> Tuple[int, str, str]:
        return self.cmd(f"EXEC {app} {args}")

    def send_dtmf(self, digits: str, on_ms: int, off_ms: int) -> bool:
        args = f"{digits},{on_ms},,{off_ms}"
        code, res, _ = self.exec_app("SendDTMF", args)
        ok = (code == 200)
        log.info(f"AGI EXEC SendDTMF -> code={code} res={res} digits=***{digits[-4:] if digits else ''} on={on_ms} off={off_ms} ok={ok}")
        return ok

    def hangup(self):
        code, res, data = self.cmd("HANGUP")
        log.info(f"AGI HANGUP -> code={code} res={res} data={data}")


# ---------------------------------------------------------------------------
# Audio reader thread (FD3 PCM -> bounded ring buffer of bytes)
# ---------------------------------------------------------------------------

class AudioRing:
    def __init__(self):
        self._buffer = bytearray()
        self._lock = threading.Lock()
        self._stop = False
        self._new_chunk_event = threading.Event()
        self._next_read_at = 0.0

    def stop(self):
        self._stop = True
        self._new_chunk_event.set()

    def append(self, data: bytes):
        if not data:
            return
        with self._lock:
            self._buffer.extend(data)
        self._new_chunk_event.set()

    def available_bytes(self) -> int:
        with self._lock:
            return len(self._buffer)

    def try_read_chunk(self) -> Optional[bytes]:
        """Non-blocking: return a CHUNK_BYTES chunk if available, else None."""
        with self._lock:
            if len(self._buffer) >= CHUNK_BYTES:
                data = bytes(self._buffer[:CHUNK_BYTES])
                del self._buffer[:CHUNK_BYTES - OVERLAP_BYTES]
                self._new_chunk_event.clear()
                return data
            return None

    def wait_for_chunk(self, timeout_s: float) -> None:
        self._new_chunk_event.wait(timeout=timeout_s)


def fd3_reader_loop(ring: AudioRing) -> None:
    fd3 = os.fdopen(3, "rb", closefd=False)
    try:
        while not ring._stop:
            try:
                b = fd3.read(FRAME_BYTES_20MS * 4)
            except Exception as e:
                log.warning(f"FD3 read error: {e}")
                break
            if not b:
                log.info("FD3 stream EOF (call ended)")
                break
            ring.append(b)
    finally:
        try:
            fd3.close()
        except Exception:
            pass
        ring.stop()


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

@dataclass
class ReactiveState:
    card: str = ""
    cvv: str = ""
    rec_path: str = ""
    dtmf_on_ms: int = 250
    dtmf_off_ms: int = 150
    pad_s: int = 40
    sent_card: bool = False
    sent_cvv: bool = False
    sent_expiry: bool = False
    bad_count: int = 0
    state: str = "greeting"
    running_transcript: List[str] = field(default_factory=list)
    full_text: str = ""
    last_action_at: float = 0.0
    started_at: float = field(default_factory=time.time)
    final_sent_at: Optional[float] = None
    backend_name: str = "unknown"
    card_sent_at: Optional[float] = None
    cvv_sent_at: Optional[float] = None
    expiry_sent_at: Optional[float] = None


def _matches_any(text: str, keys: List[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keys)


def dedupe_tail(running: List[str], new_text: str) -> str:
    """Strip overlap from new_text if its head matches the tail of running[] concatenated."""
    if not running:
        return new_text
    tail_src = (" ".join(running[-8:])).lower()
    t = new_text.lower()
    for i in range(min(len(t), 200), max(0, len(t) - 200), -1):
        head = t[:i]
        if tail_src.endswith(head) and i >= 6:
            return new_text[i:]
    return new_text


def state_decide(rs: ReactiveState, text: str) -> Optional[str]:
    """Return digits string to send, or None. Mutates rs.state/sent flags."""
    if not text:
        return None
    # Terminals first
    if _matches_any(text, TRIGGERS["ok"]):
        rs.state = "done"
        return None
    bad = _matches_any(text, TRIGGERS["bad"])
    if bad and rs.state in ("sent_card", "waiting_cvv", "sent_cvv"):
        rs.bad_count += 1
        # Reset the relevant sent flag so we re-send after next prompt on retry window
        if rs.state == "sent_card" or (rs.state == "waiting_cvv" and not rs.sent_cvv):
            rs.sent_card = False
            rs.state = "waiting_card"
        elif rs.state in ("sent_cvv",):
            rs.sent_cvv = False
            rs.state = "waiting_cvv"
        if rs.bad_count >= 3:
            rs.state = "failed"
        return None

    # Transitions
    if rs.state == "greeting":
        if _matches_any(text, TRIGGERS["card"]) and not rs.sent_card:
            rs.state = "waiting_card"
    if rs.state in ("greeting", "waiting_card") and _matches_any(text, TRIGGERS["card"]) and not rs.sent_card:
        rs.sent_card = True
        rs.state = "sent_card"
        rs.card_sent_at = time.time()
        return rs.card
    if rs.state == "sent_card" and _matches_any(text, TRIGGERS["cvv"]) and not rs.sent_cvv and rs.cvv:
        rs.sent_cvv = True
        rs.state = "sent_cvv"
        rs.cvv_sent_at = time.time()
        return rs.cvv
    if rs.state == "sent_cvv" and _matches_any(text, TRIGGERS["expiry"]) and not rs.sent_expiry:
        # Expiry currently not routed through dialplan; skip sending, mark transition only
        rs.sent_expiry = True
        rs.state = "waiting_result"
        rs.expiry_sent_at = time.time()
        return None
    return None


# ---------------------------------------------------------------------------
# Main EAGI entry
# ---------------------------------------------------------------------------

def build_backends() -> Tuple[List[TranscriptionBackend], str]:
    dg_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
    el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    backends: List[TranscriptionBackend] = []
    order: List[str] = []
    if dg_key:
        backends.append(DeepgramBackend(dg_key))
        order.append("deepgram")
    if el_key:
        backends.append(ElevenLabsBackend(el_key))
        order.append("elevenlabs")
    if not backends:
        log.error("NO BACKENDS AVAILABLE — set DEEPGRAM_API_KEY and/or ELEVENLABS_API_KEY")
        raise RuntimeError("no transcription backend configured")
    return backends, "+".join(order)


def main() -> int:
    # 1. AGI handshake
    agi = AGI()
    card = "".join(c for c in str(agi.arg(1)) if c.isdigit())
    cvv = "".join(c for c in str(agi.arg(2)) if c.isdigit())
    rec_path = agi.arg(3, "")
    try:
        dtmf_on = int(agi.arg(4) or "250")
    except Exception:
        dtmf_on = 250
    try:
        dtmf_off = int(agi.arg(5) or "150")
    except Exception:
        dtmf_off = 150
    try:
        pad_s = int(agi.arg(6) or "40")
    except Exception:
        pad_s = 40

    log.info("=" * 72)
    log.info(f"EAGI start channel_env[agi_extension]={agi.env.get('agi_extension')} agi_channel={agi.env.get('agi_channel')}")
    log.info(f"  card_last4={card[-4:] if card else 'NONE'} cvv_len={len(cvv)} rec={rec_path}")
    log.info(f"  dtmf_on_ms={dtmf_on} dtmf_off_ms={dtmf_off} pad_s={pad_s}")

    # 2. Backends
    backends, backend_order = build_backends()
    active_backend_idx = 0
    streak = 0
    rs = ReactiveState(
        card=card,
        cvv=cvv,
        rec_path=rec_path,
        dtmf_on_ms=dtmf_on,
        dtmf_off_ms=dtmf_off,
        pad_s=pad_s,
    )
    log.info(f"  backends_available={backend_order} starting on: {backends[active_backend_idx].name}")
    rs.backend_name = backends[active_backend_idx].name

    # 3. FD3 audio ring
    ring = AudioRing()
    t = threading.Thread(target=fd3_reader_loop, args=(ring,), name="fd3-rd", daemon=True)
    t.start()

    try:
        # 4. Main loop
        last_log_transcript_len = 0
        while rs.state not in ("done", "failed"):
            if (time.time() - rs.started_at) > MAX_CALL_SECONDS:
                log.warning(f"MAX_CALL_SECONDS ({MAX_CALL_SECONDS}) reached -> terminate")
                rs.state = "failed"
                break
            chunk = ring.try_read_chunk()
            if chunk is None:
                ring.wait_for_chunk(0.25)
                continue

            # Try active backend first, fall back on failure streak
            text: Optional[str] = None
            for _ in range(len(backends)):
                be = backends[active_backend_idx]
                try:
                    text = be.transcribe_bytes(chunk)
                    rs.backend_name = be.name
                    streak = 0
                    break
                except BackendUnavailable as e:
                    log.warning(f"backend {be.name} unavailable: {e}")
                    streak += 1
                    if streak >= MAX_STREAK_FAIL:
                        # Rotate to next backend if available
                        streak = 0
                        active_backend_idx = (active_backend_idx + 1) % len(backends)
                        log.warning(f"STREAK_FAIL -> rotating to backend {backends[active_backend_idx].name}")
                        rs.backend_name = backends[active_backend_idx].name

            if text:
                new_part = dedupe_tail(rs.running_transcript, text)
                if new_part:
                    rs.running_transcript.append(new_part)
                    rs.full_text = " ".join(rs.running_transcript)
                # Log chunks of transcript growth
                tl = len(rs.full_text)
                if tl - last_log_transcript_len >= 120:
                    last_log_transcript_len = tl
                    log.info(f"TRANSCRIPT so far ({tl} chars): {rs.full_text[-300:]}")
                action = state_decide(rs, text)
                if action:
                    ok = agi.send_dtmf(action, rs.dtmf_on_ms, rs.dtmf_off_ms)
                    rs.last_action_at = time.time()
                    if not ok:
                        log.warning("SendDTMF returned non-200, marking as sent anyway")

        # 5. Hold the line for EXTRA_HANGUP_PAD_S so MixMonitor captures result audio
        rs.final_sent_at = time.time()
        hold_until = rs.final_sent_at + float(max(1, rs.pad_s))
        remaining = hold_until - time.time()
        if remaining > 0:
            log.info(f"state={rs.state} reached — holding line for {remaining:.1f}s to capture result audio")
            end = time.time() + remaining
            while time.time() < end:
                # Drain audio so the ring buffer doesn't grow unbounded
                chunk = ring.try_read_chunk()
                if chunk is None:
                    time.sleep(0.25)
                # Intentionally drop hold-period chunks (no transcription needed in pad)
    finally:
        ring.stop()
        try:
            agi.hangup()
        except Exception:
            pass
        try:
            t.join(timeout=1.5)
        except Exception:
            pass

    log.info(f"EAGI FINAL state={rs.state} backend={rs.backend_name} bad_count={rs.bad_count}")
    log.info(f"  card_sent_at={rs.card_sent_at} cvv_sent_at={rs.cvv_sent_at} expiry_sent_at={rs.expiry_sent_at}")
    # Write result JSON next to the recording so thin client can pick it up
    if rec_path:
        try:
            res_path = Path(str(rec_path) + ".eagi.json")
            res_path.write_text(json.dumps({
                "state": rs.state,
                "backend": rs.backend_name,
                "bad_count": rs.bad_count,
                "full_text": rs.full_text,
                "card_sent_at": rs.card_sent_at,
                "cvv_sent_at": rs.cvv_sent_at,
                "expiry_sent_at": rs.expiry_sent_at,
                "final_sent_at": rs.final_sent_at,
                "started_at": rs.started_at,
                "sent_card": rs.sent_card,
                "sent_cvv": rs.sent_cvv,
                "sent_expiry": rs.sent_expiry,
            }), encoding="utf-8")
            log.info(f"Wrote EAGI result sidecar: {res_path}")
        except Exception as e:
            log.warning(f"Could not write sidecar json: {e}")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
        sys.exit(rc)
    except Exception as exc:
        try:
            log.exception(f"EAGI FATAL: {exc}")
        except Exception:
            print(f"EAGI FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
