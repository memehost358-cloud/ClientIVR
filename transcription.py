#!/usr/bin/env python3
"""
Transcription Service - Handles audio transcription using ElevenLabs Scribe.

Calls ElevenLabs HTTP API synchronously. Async callers MUST run this via
asyncio.to_thread(...) (not direct await) so we don't block the reactor.

Provides word-level timestamps for response analysis.
"""

from __future__ import annotations

import logging
import requests
from pathlib import Path
from typing import Any, Dict

from config import Config


class TranscriptionService:
    """Transcription service using ElevenLabs Scribe (sync HTTP API)."""

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.api_url = "https://api.elevenlabs.io/v1/speech-to-text"

    def transcribe(self, audio_path: str) -> Dict[str, Any]:
        """Synchronous transcription. Async callers: use asyncio.to_thread."""
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        if not self.config.elevenlabs_api_key:
            raise Exception("ELEVENLABS_API_KEY not set in env")

        self.logger.info(f"Transcribing audio: {audio_path.name} ({audio_path.stat().st_size} bytes)")

        f = None
        try:
            f = audio_path.open("rb")
            files = {"file": (audio_path.name, f, "audio/wav")}
            data = {"model_id": "scribe_v1", "timestamps_granularity": "word"}

            response = requests.post(
                self.api_url,
                headers={"xi-api-key": self.config.elevenlabs_api_key},
                files=files,
                data=data,
                timeout=(10, 180),
            )

            if response.status_code != 200:
                # Try to read the body even on error; keep it short.
                body_preview = (response.text or "")[:800]
                raise Exception(
                    f"ElevenLabs API HTTP {response.status_code}: {body_preview}"
                )

            payload = response.json()
            norm = self._normalize_response(payload)
            self.logger.info(
                f"Transcription OK: {len(norm['words'])} words, "
                f"transcript ({len(norm['transcript'])} chars): "
                f"{(norm['transcript'] or '')[:200]!r}"
            )
            return norm
        except Exception as e:
            self.logger.error(f"Transcription failed: {e}")
            raise
        finally:
            if f is not None:
                try:
                    f.close()
                except Exception:
                    pass

    def _normalize_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize ElevenLabs response to standard format."""
        transcript = ""
        if isinstance(payload.get("text"), str):
            transcript = payload["text"].strip()
        language = payload.get("language_code")

        raw_words = payload.get("words", [])
        words = []
        if isinstance(raw_words, list):
            for w in raw_words:
                if not isinstance(w, dict):
                    continue
                if w.get("type") == "word":
                    text = w.get("text") or w.get("word", "")
                    start = w.get("start")
                    end = w.get("end")
                    try:
                        start_f = float(start) if start is not None else None
                        end_f = float(end) if end is not None else None
                    except (TypeError, ValueError):
                        start_f, end_f = None, None
                    if (
                        start_f is not None
                        and end_f is not None
                        and start_f >= 0.0
                        and end_f >= start_f
                    ):
                        words.append(
                            {
                                "word": str(text),
                                "start": start_f,
                                "end": end_f,
                            }
                        )

        duration = words[-1]["end"] if words else 0.0
        return {
            "transcript": transcript,
            "language": language,
            "words": words,
            "duration_seconds": duration,
        }
