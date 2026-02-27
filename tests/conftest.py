"""Shared fixtures for tests."""

from pathlib import Path

import pytest

# Project root (parent of tests/)
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent


@pytest.fixture
def sample_spec_path() -> Path:
    """Path to the India spec file."""
    return PROJECT_ROOT / "specs" / "india.yaml"


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    """Temporary directory for output files."""
    out = tmp_path / "output" / "india"
    out.mkdir(parents=True, exist_ok=True)
    return out
