#!/usr/bin/env python3
"""
Report Generator - Generates validation and compliance reports

Creates professional reports for card validation results.
"""

import json
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List


class ReportGenerator:
    """Generate validation and compliance reports."""

    def __init__(self, config, logger: logging.Logger):
        self.config = config
        self.logger = logger

    def generate_validation_report(self, results: Dict[str, Any]) -> str:
        """Generate card validation report."""
        timestamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())

        report = {
            "report_type": "Card Validation Report",
            "generated_at": timestamp,
            "company": self.config.company_name,
            "system": self.config.system_name,
            "results": results,
            "summary": {
                "card_valid": results.get("success", False),
                "security_code_required": (results.get("card_result") or {}).get("state") == "security_code_required",
                "valid_code": results.get("valid_code"),
                "total_attempts": len((results.get("security_result") or {}).get("attempts", [])),
            }
        }

        # Save report
        report_path = Path(self.config.results_dir) / f"validation_report_{timestamp}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))

        self.logger.info(f"Validation report saved: {report_path}")
        return str(report_path)

    def generate_compliance_report(self, results: Dict[str, Any]) -> str:
        """Generate compliance report."""
        timestamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())

        report = {
            "report_type": "Compliance Report",
            "generated_at": timestamp,
            "company": self.config.company_name,
            "system": self.config.system_name,
            "compliance_frameworks": ["PCI_DSS", "SOC_2"],
            "findings": [],
            "compliance_status": "COMPLIANT"
        }

        # Add findings based on results
        if results.get("card_result", {}).get("state") == "card_invalid":
            report["findings"].append({
                "severity": "HIGH",
                "description": "Card number validation failed",
                "recommendation": "Verify card number with card management system"
            })
            report["compliance_status"] = "REQUIRES_ATTENTION"

        # Save report
        report_path = Path(self.config.results_dir) / f"compliance_report_{timestamp}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))

        self.logger.info(f"Compliance report saved: {report_path}")
        return str(report_path)
