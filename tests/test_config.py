"""Unit tests for config module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from openaq_extractor.config import (
    Spec,
    get_api_key_if_set,
    get_effective_api_key,
    load_spec,
    resolve_api_key,
)


def test_load_spec_valid(sample_spec_path: Path) -> None:
    """load_spec loads valid YAML and returns Spec."""
    spec = load_spec(sample_spec_path)
    assert isinstance(spec, Spec)
    assert spec.name == "India AQ Monitors"
    assert spec.country.iso == "IN"
    assert "pm25" in spec.pollutants
    assert spec.aggregation == "daily"


def test_load_spec_missing_file() -> None:
    """load_spec raises SystemExit on missing file."""
    with pytest.raises(SystemExit):
        load_spec("/nonexistent/path/spec.yaml")


def test_load_spec_invalid_yaml(tmp_path: Path) -> None:
    """load_spec raises SystemExit on invalid YAML."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("invalid: yaml: : :")
    with pytest.raises(SystemExit) as exc_info:
        load_spec(bad_yaml)
    assert "Invalid YAML" in str(exc_info.value)


def test_load_spec_invalid_schema(tmp_path: Path) -> None:
    """load_spec raises SystemExit on invalid schema (missing required fields)."""
    bad_spec = tmp_path / "bad.yaml"
    bad_spec.write_text("name: test\n")
    with pytest.raises(SystemExit) as exc_info:
        load_spec(bad_spec)
    assert "Invalid spec" in str(exc_info.value)


def test_load_spec_empty(tmp_path: Path) -> None:
    """load_spec raises SystemExit on empty file."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(SystemExit) as exc_info:
        load_spec(empty)
    assert "empty" in str(exc_info.value).lower()


def test_get_api_key_if_set_from_cli(sample_spec_path: Path) -> None:
    """get_api_key_if_set returns key from CLI when provided."""
    spec = load_spec(sample_spec_path)
    with patch.dict("os.environ", {}, clear=True):
        key = get_api_key_if_set(spec, cli_api_key="cli-key-123")
    assert key == "cli-key-123"


def test_get_api_key_if_set_from_spec(sample_spec_path: Path) -> None:
    """get_api_key_if_set returns key from spec when spec has api_key."""
    spec = load_spec(sample_spec_path)
    spec.api_key = "spec-key-456"
    with patch.dict("os.environ", {}, clear=True):
        key = get_api_key_if_set(spec, cli_api_key=None)
    assert key == "spec-key-456"


def test_get_api_key_if_set_from_env(sample_spec_path: Path) -> None:
    """get_api_key_if_set returns key from env when set."""
    spec = load_spec(sample_spec_path)
    spec.api_key = None
    with patch.dict("os.environ", {"OPENAQ_API_KEY": "env-key-789"}, clear=False):
        key = get_api_key_if_set(spec, cli_api_key=None)
    assert key == "env-key-789"


def test_get_api_key_if_set_none(sample_spec_path: Path) -> None:
    """get_api_key_if_set returns None when no key anywhere."""
    spec = load_spec(sample_spec_path)
    spec.api_key = None
    with patch.dict("os.environ", {}, clear=True):
        with patch("openaq_extractor.config.load_global_config", return_value=None):
            key = get_api_key_if_set(spec, cli_api_key=None)
    assert key is None


def test_get_effective_api_key_raises_without_key(sample_spec_path: Path) -> None:
    """get_effective_api_key raises SystemExit when no key found."""
    spec = load_spec(sample_spec_path)
    spec.api_key = None
    with patch.dict("os.environ", {}, clear=True):
        with patch("openaq_extractor.config.load_global_config", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                get_effective_api_key(spec, cli_api_key=None)
    msg = str(exc_info.value.args[0]) if exc_info.value.args else str(exc_info.value)
    assert "API key" in msg or "api key" in msg.lower()


def test_get_effective_api_key_returns_cli_key(sample_spec_path: Path) -> None:
    """get_effective_api_key returns CLI key when provided."""
    spec = load_spec(sample_spec_path)
    key = get_effective_api_key(spec, cli_api_key="test-key")
    assert key == "test-key"
