#!/usr/bin/env python3
"""
Card Validation System - Main Application

Professional card validation system for card activation IVR systems.
This system validates card activation status and security code functionality.
"""

import os
import sys
import asyncio
import logging
import time
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from card_validator import CardValidator
from config import Config
from response_analyzer import ResponseAnalyzer
from report_generator import ReportGenerator
from compliance_checker import ComplianceChecker

# Configure logging
log_level = os.getenv('LOG_LEVEL', 'INFO')
logging.basicConfig(
    level=getattr(logging, log_level),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.getenv('LOG_DIR', '/var/log/card-validation-system') + '/card_validation.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def main():
    """Main entry point for the card validation system."""
    logger.info("Starting Card Validation System")
    logger.info(f"Company: {os.getenv('COMPANY_NAME')}")
    logger.info(f"System: {os.getenv('SYSTEM_NAME')}")

    try:
        # Load configuration
        config = Config.from_env()

        # Initialize response analyzer
        analyzer = ResponseAnalyzer(config)

        # Initialize card validator
        validator = CardValidator(config, analyzer, logger)

        # Initialize report generator
        report_generator = ReportGenerator(config, logger)

        # Initialize compliance checker
        compliance_checker = ComplianceChecker(config, logger)

        # Run validation
        logger.info("Starting card validation process...")
        results = asyncio.run(validator.validate_card())

        # Generate reports
        logger.info("Generating validation report...")
        validation_report_path = report_generator.generate_validation_report(results)

        logger.info("Generating compliance report...")
        compliance_report = compliance_checker.check_compliance(results)
        compliance_report_path = Path(config.results_dir) / f"compliance_report_{int(time.time())}.json"
        compliance_report_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        compliance_report_path.write_text(json.dumps(compliance_report, indent=2))

        # Process results
        logger.info("Card validation completed")
        logger.info(f"Validation report: {validation_report_path}")
        logger.info(f"Compliance report: {compliance_report_path}")
        logger.info(f"Results: {results}")

        return 0

    except KeyboardInterrupt:
        logger.warning("System interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"System error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
