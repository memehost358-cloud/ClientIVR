#!/usr/bin/env python3
"""
Transcription Service - Handles audio transcription using ElevenLabs Scribe

Provides word-level timestamps for response analysis.
"""

import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional

from config import Config


class TranscriptionService:
    """Transcription service using ElevenLabs Scribe."""

    def __init__(self, config: Config, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.api_url = "https://api.elevenlabs.io/v1/speech-to-text"

    async def transcribe(self, audio_path: str) -> Dict[str, Any]:
        """Transcribe audio file and return result with timestamps."""
        audio_path = Path(audio_path)

        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        self.logger.info(f"Transcribing audio: {audio_path.name}")

        try:
            with audio_path.open("rb") as f:
                files = {"file": (audio_path.name, f, "audio/wav")}
                data = {"model_id": "scribe_v1", "timestamps_granularity": "word"}

                response = requests.post(
                    self.api_url,
                    headers={"xi-api-key": self.config.elevenlabs_api_key},
                    files=files,
                    data=data,
                    timeout=120
                )

            if response.status_code != 200:
                raise Exception(f"ElevenLabs API error: {response.status_code} - {response.text}")

            payload = response.json()
            return self._normalize_response(payload)

        except Exception as e:
            self.logger.error(f"Transcription failed: {e}")
            raise

    def _normalize_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize ElevenLabs response to standard format."""
        transcript = payload.get("text", "")
        language = payload.get("language_code")

        raw_words = payload.get("words", [])
        words = []
        for w in raw_words:
            if w.get("type") == "word":
                text = w.get("text") or w.get("word", "")
                start = w.get("start")
                end = w.get("end")
                if start is not None and end is not None:
                    words.append({
                        "word": text,
                        "start": float(start),
                        "end": float(end)
                    })

        duration = words[-1]["end"] if words else 0.0

        return {
            "transcript": transcript,
            "language": language,
            "words": words,
            "duration_seconds": duration,
        }
