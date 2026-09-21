#!/usr/bin/env python3
"""
Card Validation System - Main entry point.

Loads configuration from .env (provider policy, IVR profile, card inventory,
rate limits, etc.), validates each card in inventory through Phase 1
(card check) + Phase 2 (CVV 000-999 brute force with resume and rate limits),
and writes per-card validation + compliance reports to RESULTS_DIR.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from card_validator import (
    CardValidator,
    RateLimiter,
    StateStore,
)
from config import (
    Config,
    CardSpec,
    load_card_inventory_from_env,
)
from response_analyzer import ResponseAnalyzer
from report_generator import ReportGenerator
from compliance_checker import ComplianceChecker


def _configure_logging(log_dir: str) -> logging.Logger:
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level_name, logging.INFO)
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    handlers: List[logging.Handler] = [
        logging.FileHandler(log_path / "card_validation.log"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    return logging.getLogger("main")


def _print_summary_table(results: List[Dict[str, Any]], logger: logging.Logger) -> None:
    sep = "+" + "-" * 14 + "+" + "-" * 10 + "+" + "-" * 14 + "+" + "-" * 12 + "+"
    logger.info("")
    logger.info("SUMMARY")
    logger.info(sep)
    logger.info(
        "| CARD LAST4     | P1 STATE | VALID CVV?   | RESUME CODE  |"
    )
    logger.info(sep)
    for r in results:
        last4 = (r.get("card") or "***????")[-4:]
        state = (
            (r.get("card_result") or {}).get("state")
            or "(missing)"
        )[:10]
        valid = r.get("valid_code") or "-"
        resume = str(r.get("resume_code") or 0)
        logger.info(
            f"| {last4:<12} | {state:<8} | {valid:<12} | {resume:<12} |"
        )
    logger.info(sep)


def main() -> int:
    config = Config.from_env()
    logger = _configure_logging(config.log_dir)

    logger.info("=" * 72)
    logger.info("Card Validation System starting")
    logger.info(f"Company : {config.company_name}")
    logger.info(f"System  : {config.system_name}")
    logger.info(f"IVR     : {config.ivr_profile.profile_id} -> {config.ivr_profile.ivr_phone_number}")
    logger.info(
        f"Providers (failover order): "
        f"{[(p.name, p.endpoint) for p in config.provider_policy.order]}"
    )
    logger.info("=" * 72)

    # Validate config early
    if not config.validate():
        logger.error("Config validation failed. Fix errors above and re-run.")
        return 2

    # Load card inventory
    try:
        cards: List[CardSpec] = load_card_inventory_from_env()
    except Exception as e:
        logger.error(f"Failed to load card inventory: {e}")
        return 3

    logger.info(f"Card inventory: {len(cards)} card(s) loaded")
    for idx, card in enumerate(cards, 1):
        logger.info(
            f"  #{idx}: last4={card.number[-4:] if card.number else '?'} "
            f"cvv_start={card.cvv_start:03d} "
            f"expiry={card.expiry_mmyy or 'n/a'} dob={card.dob_mmyy or 'n/a'}"
        )

    # Shared services
    state_store = StateStore(Path(config.state_file), logger)
    rate_limiter = RateLimiter(
        max_daily=config.max_daily_calls,
        max_hourly=config.rate_limit_calls_per_hour,
        logger=logger,
    )
    state_store.load_rate(rate_limiter)
    analyzer = ResponseAnalyzer(config.ivr_profile, logger)
    report_gen = ReportGenerator(config, logger)
    compliance = ComplianceChecker(config, logger)

    per_card_results: List[Dict[str, Any]] = []

    for i, card in enumerate(cards, 1):
        logger.info("")
        logger.info(f"===== Processing card {i}/{len(cards)}: {card.masked()} =====")

        validator = CardValidator(
            config=config,
            profile=config.ivr_profile,
            provider_policy=config.provider_policy,
            state_store=state_store,
            rate_limiter=rate_limiter,
            analyzer=analyzer,
            logger=logger,
        )

        import asyncio

        try:
            result = asyncio.run(validator.validate_card(card))
        except KeyboardInterrupt:
            logger.warning("Interrupted by user; saving state before exit")
            state_store.save()
            return 130
        except Exception as e:
            logger.exception(f"Card {card.masked()} threw top-level error: {e}")
            result = {
                "success": False,
                "card": card.masked(),
                "error": str(e),
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

        per_card_results.append(result)
        state_store.save()

        # Per-card validation report
        try:
            vrp = report_gen.generate_validation_report(result)
            logger.info(f"Per-card validation report: {vrp}")
        except Exception as e:
            logger.warning(f"Could not generate validation report: {e}")

        # Per-card compliance report
        try:
            compliance_result = compliance.check_compliance(result)
            ts = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
            compliance_path = (
                Path(config.results_dir)
                / f"compliance_report_{card.masked()[-4:]}_{ts}.json"
            )
            compliance_path.parent.mkdir(parents=True, exist_ok=True)
            compliance_path.write_text(json.dumps(compliance_result, indent=2))
            logger.info(
                f"Compliance status: {compliance_result.get('overall_status')} -> {compliance_path}"
            )
        except Exception as e:
            logger.warning(f"Could not generate compliance report: {e}")

    # Overall summary written to results dir
    summary: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "company": config.company_name,
        "system": config.system_name,
        "ivr_profile_id": config.ivr_profile.profile_id,
        "ivr_phone_number": config.ivr_profile.ivr_phone_number,
        "provider_order": [p.name for p in config.provider_policy.order],
        "cards_total": len(per_card_results),
        "cards_succeeded": sum(1 for r in per_card_results if r.get("success")),
        "cards_with_valid_cvv": sum(1 for r in per_card_results if r.get("valid_code")),
        "per_card": per_card_results,
    }
    Path(config.results_dir).mkdir(parents=True, exist_ok=True)
    summary_path = (
        Path(config.results_dir)
        / f"run_summary_{time.strftime('%Y%m%d-%H%M%S')}.json"
    )
    summary_path.write_text(json.dumps(summary, indent=2))

    _print_summary_table(per_card_results, logger)
    logger.info(f"Full summary JSON written to: {summary_path}")
    logger.info("Done.")

    # Exit non-zero if no cards produced a result or any unexpected fatal error
    if not per_card_results:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
