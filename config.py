#!/usr/bin/env python3
"""
Configuration Management

Everything comes from environment variables. No hardcoded numbers,
phrases, timings, or provider names.

Exports:
  Config            - system-wide settings
  IVRProfile        - per-target-IVR: phone number + timings + phrases
  ProviderSpec      - per-SIP-trunk: Asterisk endpoint + callerId
  CardSpec          - one card: number + optional cvv_start + phase-2 extras
  load_card_inventory() -> list[CardSpec]
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()


# ----------------------------- Phrase helpers -----------------------------


def _split_csv(value: Optional[str]) -> List[str]:
    """Split comma-separated phrases, trimming, dropping empties, lowering."""
    if not value:
        return []
    out: List[str] = []
    # Use csv.reader so commas inside quoted phrases could be supported in future.
    for row in csv.reader(io.StringIO(value)):
        for cell in row:
            cell = cell.strip().lower()
            if cell:
                out.append(cell)
    return out


def _normalize_e164(raw: str, default_country_code: str = "1") -> str:
    """Best-effort E.164 normalization. Assumes NANP (US/CA/ toll-free).
    - Strips all non-digit / non-'+' prefix chars
    - If starts with '+' -> returns as-is
    - 10 digits (incl toll-free area codes) -> prepend '+' + default_country_code
    - 11 digits starting with '1' -> prepend '+'
    - Otherwise -> digits only, prepend '+'+default_country_code only if len==10
    """
    if not raw:
        return ""
    stripped = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if not stripped:
        return raw
    if stripped.startswith("+"):
        return stripped
    digits = stripped
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    if len(digits) == 10:
        return "+" + default_country_code + digits
    # Unknown length: return as-is (digits only), caller/provider will handle
    return digits


# ------------------------------- IVRProfile -------------------------------


@dataclass
class IVRProfile:
    """Per-target-IVR configuration. All values come from env + IVR_PROFILE_ID."""

    profile_id: str
    ivr_phone_number: str

    # DTMF / call flow timings (seconds unless *_MS)
    wait_after_connect_s: float = 2.0
    wait_after_card_digits_s: float = 6.0
    wait_after_cvv_digits_s: float = 5.0
    dtmf_digit_on_ms: int = 100
    dtmf_inter_digit_ms: int = 200
    max_call_wait_s: int = 45

    # Classification phrases (substring match, case-insensitive)
    phrases_card_prompt: List[str] = field(default_factory=list)
    phrases_cvv_prompt: List[str] = field(default_factory=list)
    phrases_invalid_card: List[str] = field(default_factory=list)
    phrases_verification_required: List[str] = field(default_factory=list)
    phrases_valid_code: List[str] = field(default_factory=list)
    phrases_invalid_code: List[str] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "IVRProfile":
        pid = os.getenv("IVR_PROFILE_ID", "default")
        raw_ivr = os.getenv("IVR_PHONE_NUMBER", "").strip()
        norm_ivr = _normalize_e164(raw_ivr)
        if norm_ivr != raw_ivr:
            print(f"INFO: IVR phone number normalized '{raw_ivr}' -> '{norm_ivr}'")
        return cls(
            profile_id=pid,
            ivr_phone_number=norm_ivr,
            wait_after_connect_s=float(os.getenv("WAIT_AFTER_CONNECT_S", "2")),
            wait_after_card_digits_s=float(os.getenv("WAIT_AFTER_CARD_DIGITS_S", "6")),
            wait_after_cvv_digits_s=float(os.getenv("WAIT_AFTER_CVV_DIGITS_S", "5")),
            dtmf_digit_on_ms=int(os.getenv("DTMF_DIGIT_ON_MS", "100")),
            dtmf_inter_digit_ms=int(os.getenv("DTMF_INTER_DIGIT_MS", "200")),
            max_call_wait_s=int(os.getenv("MAX_CALL_WAIT_S", "45")),
            phrases_card_prompt=_split_csv(os.getenv("PHRASES_CARD_PROMPT")) or [
                "please enter your 16 digit card number",
                "enter your card number",
                "digit card number",
            ],
            phrases_cvv_prompt=_split_csv(os.getenv("PHRASES_CVV_PROMPT")) or [
                "enter the 3 digit security code",
                "three digit security code",
                "security code",
                "3 digit security code",
                "cvv",
            ],
            phrases_invalid_card=_split_csv(os.getenv("PHRASES_INVALID_CARD")) or [
                "card number does not exist",
                "invalid card number",
                "card is invalid",
                "not a valid card number",
            ],
            phrases_verification_required=_split_csv(
                os.getenv("PHRASES_VERIFICATION_REQUIRED")
            ) or [
                "one time verification code",
                "confirm your identity",
                "text you a code",
                "verification code",
                "send you a text",
            ],
            phrases_valid_code=_split_csv(os.getenv("PHRASES_VALID_CODE")) or [
                "activated",
                "successful",
                "complete",
                "thank you",
                "your card has been activated",
                "activation complete",
                "card is now active",
            ],
            phrases_invalid_code=_split_csv(os.getenv("PHRASES_INVALID_CODE")) or [
                "invalid security code",
                "incorrect security code",
                "wrong code",
                "cvv is incorrect",
                "security code does not match",
                "please try again",
                "code does not match",
            ],
        )


# ------------------------------ ProviderSpec ------------------------------


@dataclass
class ProviderSpec:
    """One SIP trunk provider used via local Asterisk SIP endpoint."""

    name: str
    endpoint: str  # SIP endpoint name in chan_sip / chan_pjsip
    caller_id_num: str  # outbound CLI number

    # Credentials (not needed for Asterisk trunk since Asterisk holds them),
    # but kept for status reporting + provider rotation heuristics if needed.
    account_sid: str = ""
    auth_token: str = ""
    phone_number: str = ""


@dataclass
class ProviderPolicy:
    """Ordered list of providers + failover threshold."""

    order: List[ProviderSpec]
    failover_consecutive_failures: int = 3

    def validate(self) -> List[str]:
        errors: List[str] = []
        warnings: List[str] = []
        if not self.order:
            errors.append("PROVIDER_ORDER list is empty")
            return errors

        fully_valid_providers = 0
        for p in self.order:
            ok_endpoint = bool(p.endpoint and not p.endpoint.lower().startswith("your_"))
            # Caller ID (PROV_CALLERID_*) is OPTIONAL. Asterisk will fall back to
            # whatever default CLI the SIP trunk peer is configured to send.
            ok_callerid = bool(p.caller_id_num and not p.caller_id_num.lower().startswith("your_"))
            if ok_endpoint:
                fully_valid_providers += 1
                if not ok_callerid:
                    warnings.append(
                        f"PROVIDER '{p.name}': PROV_CALLERID_{p.name.upper()} not set. "
                        f"Asterisk trunk default CLI will be used (this is usually fine)."
                    )
            else:
                warnings.append(
                    f"SKIPPING provider '{p.name}' (PROV_ENDPOINT_{p.name.upper()} missing or placeholder)"
                )

        if fully_valid_providers == 0:
            for w in warnings:
                errors.append(w.replace("SKIPPING provider", "PROVIDER MISCONFIGURED"))
            errors.append(
                "NO VALID PROVIDER IN PROVIDER_ORDER. "
                "Set PROV_ENDPOINT_<name> for at least one provider in PROVIDER_ORDER "
                "(PROV_CALLERID_<name> is optional)."
            )
        else:
            for w in warnings:
                print(f"WARN: {w}")

        return errors


def _build_provider_policy_from_env() -> ProviderPolicy:
    order_raw = os.getenv("PROVIDER_ORDER", "signalwire")
    names = [n.strip().lower() for n in order_raw.split(",") if n.strip()]

    lookup_credentials = {
        "signalwire": (
            os.getenv("SIGNALWIRE_ACCOUNT_SID", ""),
            os.getenv("SIGNALWIRE_AUTH_TOKEN", ""),
            os.getenv("SIGNALWIRE_PHONE_NUMBER", ""),
        ),
        "twilio": (
            os.getenv("TWILIO_ACCOUNT_SID", ""),
            os.getenv("TWILIO_AUTH_TOKEN", ""),
            os.getenv("TWILIO_PHONE_NUMBER", ""),
        ),
    }

    providers: List[ProviderSpec] = []
    for name in names:
        up = name.upper()
        sid, tok, pn = lookup_credentials.get(name, ("", "", ""))
        prov_endpoint = os.getenv(f"PROV_ENDPOINT_{up}", name).strip()
        prov_callerid = os.getenv(f"PROV_CALLERID_{up}", "").strip()
        # Backward compat: if PROV_CALLERID_<name> is empty but the old-style
        # <NAME>_PHONE_NUMBER env var (e.g. SIGNALWIRE_PHONE_NUMBER) is set,
        # fall back to that so the user's existing .env keeps working.
        if not prov_callerid and pn:
            prov_callerid = _normalize_e164(pn)
            if prov_callerid:
                print(
                    f"INFO: Provider '{name}': PROV_CALLERID_{up} not set; "
                    f"falling back to {name.upper()}_PHONE_NUMBER={prov_callerid}"
                )
        providers.append(
            ProviderSpec(
                name=name,
                endpoint=prov_endpoint,
                caller_id_num=prov_callerid,
                account_sid=sid,
                auth_token=tok,
                phone_number=pn,
            )
        )

    failover = int(os.getenv("PROVIDER_FAILOVER_FAILED_CALLS", "3"))
    return ProviderPolicy(order=providers, failover_consecutive_failures=failover)


# ------------------------------- CardSpec / inventory ------------------------------


@dataclass
class CardSpec:
    number: str
    cvv_start: int = 0
    expiry_mmyy: Optional[str] = None
    dob_mmyy: Optional[str] = None
    phone: Optional[str] = None

    def masked(self) -> str:
        if len(self.number) <= 4:
            return "*" * len(self.number)
        return "*" * (len(self.number) - 4) + self.number[-4:]

    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "CardSpec":
        num = (str(obj.get("number") or obj.get("card_number") or "")).strip()
        cvv_start_raw = obj.get("cvv_start", 0)
        try:
            cvv_start = int(cvv_start_raw)
        except (TypeError, ValueError):
            cvv_start = 0
        return cls(
            number=num,
            cvv_start=max(0, min(999, cvv_start)),
            expiry_mmyy=(str(obj["expiry_mmyy"]) if obj.get("expiry_mmyy") else None),
            dob_mmyy=(str(obj["dob_mmyy"]) if obj.get("dob_mmyy") else None),
            phone=(str(obj["phone"]) if obj.get("phone") else None),
        )


def load_card_inventory_from_env() -> List[CardSpec]:
    """Load cards from env. Priority: CARDS_JSON > CARD_NUMBERS_CSV > CARD_NUMBER.

    Falls back to legacy env var CARD_NUMBER + default CARD_CVV_START.
    """
    json_path = (os.getenv("CARDS_JSON") or "").strip()
    if json_path:
        p = Path(json_path)
        if not p.exists():
            raise FileNotFoundError(f"CARDS_JSON referenced but file missing: {json_path}")
        data = json.loads(p.read_text())
        if not isinstance(data, list):
            raise ValueError("CARDS_JSON must contain a JSON array of card objects")
        cards = [CardSpec.from_dict(o) for o in data]
        if not cards:
            raise ValueError("CARDS_JSON array is empty")
        return cards

    csv_raw = (os.getenv("CARD_NUMBERS_CSV") or "").strip()
    if csv_raw:
        default_start = int(os.getenv("CARD_CVV_START", "0") or 0)
        numbers = [n.strip() for n in csv_raw.split(",") if n.strip()]
        cards = [CardSpec(number=n, cvv_start=default_start) for n in numbers]
        if not cards:
            raise ValueError("CARD_NUMBERS_CSV is set but empty")
        return cards

    # Legacy / backwards compatible: single CARD_NUMBER
    num = (os.getenv("CARD_NUMBER") or "").strip()
    if not num:
        return []
    default_start = int(os.getenv("CARD_CVV_START", "0") or 0)
    return [CardSpec(number=num, cvv_start=default_start)]


# --------------------------------- Config ---------------------------------


@dataclass
class Config:
    """Top-level configuration composed from env and the sub-objects above."""

    # Company / system
    company_name: str
    system_name: str
    operated_by: str
    contact_email: str

    # Public base URL (for status links only, no longer used to steer calls)
    public_base_url: str

    # AMI (calls routed through local Asterisk)
    ami_host: str
    ami_port: int
    ami_username: str
    ami_secret: str

    # Transcription
    elevenlabs_api_key: str

    # Rates / limits
    max_daily_calls: int
    rate_limit_calls_per_hour: int
    call_cooldown_seconds: float
    max_security_attempts_per_call: int

    # Storage
    results_dir: str
    recordings_dir: str
    state_file: str
    log_dir: str

    # Security
    mask_card_numbers: bool
    emergency_stop_on_detection: bool

    # Composed sub-configs
    ivr_profile: IVRProfile
    provider_policy: ProviderPolicy

    def validate(self) -> bool:
        errors: List[str] = []

        def _err(name: str, value: str):
            if not value or value.lower().startswith("your_"):
                errors.append(name)

        _err("IVR_PHONE_NUMBER (under IVRProfile)", self.ivr_profile.ivr_phone_number)
        _err("ELEVENLABS_API_KEY", self.elevenlabs_api_key)
        _err("AMI_SECRET", self.ami_secret)

        errors.extend(self.provider_policy.validate())

        # SIP trunk credentials (SignalWire/Twilio) are usually stored directly in
        # Asterisk sip.conf / pjsip.conf, not in env. We emit a WARN if none are
        # set in env, but don't treat it as fatal — the actual SIP registration
        # is handled by Asterisk (you can confirm with `sip show registry`).
        any_creds_env = any(
            (p.account_sid and not p.account_sid.lower().startswith("your_"))
            and (p.auth_token and not p.auth_token.lower().startswith("your_"))
            for p in self.provider_policy.order
        )
        if not any_creds_env:
            print(
                "WARN: No SIGNALWIRE_* or TWILIO_* credentials in env. "
                "This is OK if Asterisk stores them in sip.conf/pjsip.conf "
                "(confirm with `asterisk -rx 'sip show registry'`)."
            )

        try:
            cards = load_card_inventory_from_env()
            if not cards:
                errors.append(
                    "CARD INVENTORY: set at least one of CARD_NUMBER, "
                    "CARD_NUMBERS_CSV, or CARDS_JSON"
                )
        except Exception as e:
            errors.append(f"CARD INVENTORY ERROR: {e}")

        if errors:
            for e in errors:
                print(f"ERROR: {e}")
            return False
        return True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            company_name=os.getenv("COMPANY_NAME", "UnknownCardCompany"),
            system_name=os.getenv("SYSTEM_NAME", "CardActivationSystem"),
            operated_by=os.getenv("OPERATED_BY", "CardOperationsTeam"),
            contact_email=os.getenv("CONTACT_EMAIL", ""),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:5000").rstrip("/"),
            ami_host=os.getenv("AMI_HOST", "127.0.0.1"),
            ami_port=int(os.getenv("AMI_PORT", "5038")),
            ami_username=os.getenv("AMI_USERNAME", "cardvalidator"),
            ami_secret=os.getenv("AMI_SECRET", ""),
            elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
            max_daily_calls=int(os.getenv("MAX_DAILY_CALLS", "100")),
            rate_limit_calls_per_hour=int(os.getenv("RATE_LIMIT_CALLS_PER_HOUR", "10")),
            call_cooldown_seconds=float(os.getenv("CALL_COOLDOWN_SECONDS", "30")),
            max_security_attempts_per_call=int(
                os.getenv("MAX_SECURITY_ATTEMPTS_PER_CALL", "3")
            ),
            results_dir=os.getenv(
                "RESULTS_DIR", "/var/lib/card-validation-system/results"
            ),
            recordings_dir=os.getenv(
                "RECORDINGS_DIR", "/home/ubuntu/ClientIVR/recordings"
            ),
            state_file=os.getenv(
                "STATE_FILE", "/var/lib/card-validation-system/state.json"
            ),
            log_dir=os.getenv("LOG_DIR", "/var/log/card-validation-system"),
            mask_card_numbers=os.getenv("MASK_CARD_NUMBERS", "true").lower() == "true",
            emergency_stop_on_detection=os.getenv(
                "EMERGENCY_STOP_ON_DETECTION", "true"
            ).lower()
            == "true",
            ivr_profile=IVRProfile.from_env(),
            provider_policy=_build_provider_policy_from_env(),
        )
