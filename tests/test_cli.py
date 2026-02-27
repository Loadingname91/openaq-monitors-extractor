"""Integration tests for CLI."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from openaq_extractor.cli import main

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SPEC_PATH = PROJECT_ROOT / "specs" / "india.yaml"


def test_validate_exits_zero_and_prints() -> None:
    """validate --spec specs/india.yaml exits 0 and prints expected text."""
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--spec", str(SPEC_PATH)])
    assert result.exit_code == 0
    assert "Valid" in result.output
    assert "IN" in result.output
    assert "pm25" in result.output or "pm10" in result.output


def test_run_dry_run_exits_zero() -> None:
    """run --spec specs/india.yaml --dry-run exits 0 and prints dry-run summary."""
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--spec", str(SPEC_PATH), "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run" in result.output
    assert "India AQ Monitors" in result.output
    assert "Date range" in result.output


def test_run_phase_discover_without_api_key_exits_one() -> None:
    """run --spec specs/india.yaml --phase discover without API key exits 1 with message."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        # Create a minimal spec in isolated fs
        spec_content = """
name: "Test"
country:
  iso: "IN"
pollutants: [pm25]
aggregation: daily
date_range:
  from: "2024-01-01"
  to: "2024-12-31"
output:
  directory: "./output/test"
filters:
  monitor: true
  mobile: false
"""
        with open("spec.yaml", "w") as f:
            f.write(spec_content)
        result = runner.invoke(
            main,
            ["run", "--spec", "spec.yaml", "--phase", "discover"],
            env={"OPENAQ_API_KEY": ""},
        )
    assert result.exit_code == 1
    assert "API key" in result.output or "api key" in result.output.lower()
