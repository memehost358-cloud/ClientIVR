#!/usr/bin/env python3
from __future__ import annotations
import asyncio, logging, os, subprocess, time
from pathlib import Path
from typing import Optional
from config import Config
from transcription import TranscriptionService


class ReactiveIVR:
    def __init__(self, config, logger, ivr_number, card_number,
                 security_code="", channel_prefix="signalwire",
                 max_seconds=180, chunk_seconds=3):
        self.cfg = config
        self.log = logger
        self.ivr = ivr_number
        self.card = card_number
        self.cvv = security_code
        self.trunk = channel_prefix
        self.max_seconds = max_seconds
        self.chunk = chunk_seconds
        self.ts = TranscriptionService(config, logger)
        self.call_id = f"reactive-{int(time.time())}-{os.getpid()}"
        self.mon_path = f"/var/spool/asterisk/monitor/{self.call_id}.wav"
        self.state = "greeting"
        self.sent_card = False
        self.sent_cvv = False
        self.sent_lang = False
        self.last_texts = []

    def _run(self, cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr

    def _set_global(self, name, value):
        self._run(["sudo", "asterisk", "-rx",
                   f"dialplan set global {name} {value}"])

    def _originate(self):
        self._set_global("IVR_NUMBER", self.ivr)
        self._set_global("CALL_ID", self.call_id)
        self._set_global("CARD_NUMBER", self.card)
        self._set_global("SECURITY_CODE", self.cvv or "NONE")
        self._set_global("RECORD_FILE", self.mon_path)
        self._set_global("OUTBOUND_TRUNK", self.trunk)
        self._set_global("CALLER_ID_NUM",
                         os.environ.get("REACTIVE_CID", "+12082473014"))
        self.log.info(f"[reactive] originate call_id={self.call_id} ivr={self.ivr}")
        self._run(["sudo", "asterisk", "-rx",
                   "channel originate Local/s@run-call application Wait 300"])

    def _channels(self):
        out = self._run(["sudo", "asterisk", "-rx",
                         "core show channels concise"])
        chans = []
        for line in out.splitlines():
            parts = line.split("!")
            if parts and parts[0].startswith("SIP/"):
                chans.append(parts[0])
        return chans

    def _send_dtmf(self, digits):
        chans = self._channels()
        if not chans:
            self.log.warning("[reactive] no SIP channel for DTMF")
            return
        chan = chans[0]
        self.log.info(f"[reactive] SendDTMF({digits!r}) on {chan}")
        self._run(["sudo", "asterisk", "-rx",
                   f'channel send dtmf {chan} {digits}'])

    def _run(self, cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr

    def _set_global(self, name, value):
        self._run(["sudo", "asterisk", "-rx",
                   f"dialplan set global {name} {value}"])

    def _originate(self):
        self._set_global("IVR_NUMBER", self.ivr)
        self._set_global("CALL_ID", self.call_id)
        self._set_global("CARD_NUMBER", self.card)
        self._set_global("SECURITY_CODE", self.cvv or "NONE")
        self._set_global("RECORD_FILE", self.mon_path)
        self._set_global("OUTBOUND_TRUNK", self.trunk)
        self._set_global("CALLER_ID_NUM",
                         os.environ.get("REACTIVE_CID", "+12082473014"))
        self.log.info(f"[reactive] originate call_id={self.call_id} ivr={self.ivr}")
        self._run(["sudo", "asterisk", "-rx",
                   "channel originate Local/s@run-call application Wait 300"])

    def _channels(self):
        out = self._run(["sudo", "asterisk", "-rx",
                         "core show channels concise"])
        chans = []
        for line in out.splitlines():
            parts = line.split("!")
            if parts and parts[0].startswith("SIP/"):
                chans.append(parts[0])
        return chans

    def _send_dtmf(self, digits):
        chans = self._channels()
        if not chans:
            self.log.warning("[reactive] no SIP channel for DTMF")
            return
        chan = chans[0]
        self.log.info(f"[reactive] SendDTMF({digits!r}) on {chan}")
        self._run(["sudo", "asterisk", "-rx",
                   f'channel send dtmf {chan} {digits}'])

    def _record_chunk(self):
        chans = self._channels()
        if not chans:
            return None
        chan = chans[0]
        path = f"/tmp/ivr-chunk-{int(time.time()*1000)}.wav"
        self._run(["sudo", "asterisk", "-rx",
                   f"mixmonitor start {chan} {path}"])
        time.sleep(self.chunk)
        self._run(["sudo", "asterisk", "-rx",
                   f"mixmonitor stop {chan}"])
        p = Path(path)
        if p.exists() and p.stat().st_size > 1000:
            return path
        return None

    def _decide(self, text):
        t = (text or "").lower()
        if not self.sent_lang and (
            "press 1" in t or "appuyez sur le" in t or "pour le service" in t
            or "for english" in t or "anglais" in t
        ):
            self.sent_lang = True
            self.state = "menu"
            return "2"
        if not self.sent_card and (
            ("enter" in t and "card" in t) or "card number" in t
            or "16 digit" in t or "16-digit" in t
            or "numéro de carte" in t
        ):
            self.sent_card = True
            self.state = "card_prompt"
            return self.card + "#"
        if self.sent_card and not self.sent_cvv and (
            "security code" in t or "cvv" in t or "3 digit" in t
            or "trois chiffres" in t or "code de sécurité" in t
        ):
            self.sent_cvv = True
            self.state = "cvv_prompt"
            return self.cvv or "000"
        if any(k in t for k in ("activated", "activation complete",
                                 "thank you", "votre carte est", "activée")):
            self.state = "done"
            return None
        if any(k in t for k in ("invalid", "not recognized",
                                 "try again", "incorrect", "erreur")):
            self.state = "failed"
            return None
        return None

    async def run(self):
        self._originate()
        deadline = time.time() + 30
        while time.time() < deadline:
            if self._channels():
                break
            await asyncio.sleep(0.5)
        else:
            return {"status": "no_channel"}
        self.log.info("[reactive] channel answered, entering loop")
        start = time.time()
        while time.time() - start < self.max_seconds:
            if self.state in ("done", "failed"):
                break
            chunk = self._record_chunk()
            if not chunk:
                await asyncio.sleep(1)
                continue
            try:
                result = await asyncio.to_thread(self.ts.transcribe, chunk)
                text = result.get("transcript", "")
            except Exception as e:
                self.log.warning(f"[reactive] transcribe error: {e}")
                text = ""
            if text:
                self.last_texts.append(text)
                self.log.info(f"[reactive] heard: {text[:200]!r} state={self.state}")
                action = self._decide(text)
                if action:
                    self._send_dtmf(action)
                    await asyncio.sleep(1)
            else:
                await asyncio.sleep(0.5)
        for chan in self._channels():
            self._run(["sudo", "asterisk", "-rx",
                       f"channel request hangup {chan}"])
        return {"status": self.state, "call_id": self.call_id,
                "recording": self.mon_path, "sent_card": self.sent_card,
                "sent_cvv": self.sent_cvv, "sent_lang": self.sent_lang,
                "texts": self.last_texts}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    log = logging.getLogger("reactive")
    cfg = Config.from_env()
    ivr = os.environ.get("REACTIVE_IVR", "+18774916062")
    card = os.environ.get("REACTIVE_CARD", "4520883074934938")
    cvv = os.environ.get("REACTIVE_CVV", "")
    client = ReactiveIVR(cfg, log, ivr, card, cvv)
    result = asyncio.run(client.run())
    print()
    print("=== REACTIVE RESULT ===")
    for k, v in result.items():
        if k == "texts":
            print(f"  {k}:")
            for t in v:
                print(f"    - {t}")
        else:
            print(f"  {k}: {v}")
