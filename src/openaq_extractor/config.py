"""YAML spec loader, validation, and API key resolution."""

from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

# Load .env before accessing os.environ
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import os

# Config file search paths
CONFIG_PATHS = [
    Path.cwd() / ".openaq-extractor.yaml",
    Path.cwd() / "config.yaml",
    Path.home() / ".config" / "openaq-extractor" / "config.yaml",
]

AGGREGATION_CHOICES = Literal["raw", "hourly", "daily", "monthly", "yearly"]


class CountrySpec(BaseModel):
    """Country filter for the spec."""

    iso: str = Field(..., min_length=2, max_length=2, description="2-letter ISO 3166-alpha-2 country code")


class OutputSpec(BaseModel):
    """Output configuration."""

    directory: str = "./output"


class FiltersSpec(BaseModel):
    """Location filters."""

    monitor: Optional[bool] = None  # true=reference grade, false=air sensors, null=both
    mobile: Optional[bool] = None  # true=mobile, false=stationary, null=both


class DateRangeSpec(BaseModel):
    """Date range for measurements."""

    from_: str = Field(..., alias="from", description="Start date (YYYY-MM-DD)")
    to: str = Field(..., description="End date (YYYY-MM-DD)")

    model_config = {"populate_by_name": True}


class Spec(BaseModel):
    """Full YAML spec file schema."""

    name: str = "OpenAQ Extract"
    country: CountrySpec
    pollutants: list[str] = Field(..., min_length=1)
    aggregation: AGGREGATION_CHOICES = "daily"
    date_range: DateRangeSpec
    output: OutputSpec = Field(default_factory=OutputSpec)
    filters: FiltersSpec = Field(default_factory=FiltersSpec)
    api_key: Optional[str] = Field(default=None, description="Optional per-spec API key override")

    model_config = {"extra": "ignore"}


class OpenAQConfig(BaseModel):
    """Global OpenAQ client config (from config file)."""

    rate_limit: int = 60
    auto_wait: bool = True
    timeout: float = 8.0


class GlobalConfig(BaseModel):
    """Global config file schema."""

    api_key: Optional[str] = None
    openaq: OpenAQConfig = Field(default_factory=OpenAQConfig)
    output: Optional[dict] = None  # default_directory, etc.


def load_global_config() -> Optional[GlobalConfig]:
    """Load optional global config from search paths."""
    for p in CONFIG_PATHS:
        if p.exists():
            try:
                data = yaml.safe_load(p.read_text())
                if data:
                    return GlobalConfig(**data)
            except Exception:
                pass
    return None


def _resolve_api_key_safe(
    *,
    cli_api_key: Optional[str] = None,
    env_key: str = "OPENAQ_API_KEY",
) -> Optional[str]:
    """Return API key if found, else None. Does not raise."""
    if cli_api_key and cli_api_key.strip():
        return cli_api_key.strip()
    env_val = os.environ.get(env_key)
    if env_val and env_val.strip():
        return env_val.strip()
    global_cfg = load_global_config()
    if global_cfg and global_cfg.api_key and global_cfg.api_key.strip():
        return global_cfg.api_key.strip()
    return None


def resolve_api_key(
    *,
    cli_api_key: Optional[str] = None,
    env_key: str = "OPENAQ_API_KEY",
) -> str:
    """
    Resolve API key with precedence: CLI flag > env var > global config file.
    Raises SystemExit with helpful message if not found.
    """
    key = _resolve_api_key_safe(cli_api_key=cli_api_key, env_key=env_key)
    if key:
        return key

    msg = (
        "OpenAQ API key not found. Set it via:\n"
        "  1. CLI: --api-key YOUR_KEY\n"
        "  2. Environment: export OPENAQ_API_KEY=your-key\n"
        "  3. .env file: OPENAQ_API_KEY=your-key\n"
        "  4. Config file: ~/.config/openaq-extractor/config.yaml (api_key: ...)\n\n"
        "Get an API key at: https://explore.openaq.org/register"
    )
    raise SystemExit(msg)


def load_spec(path: str | Path) -> Spec:
    """Load and validate a YAML spec file."""
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Spec file not found: {p}")

    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise SystemExit(f"Invalid YAML in spec file: {e}")

    if not data:
        raise SystemExit("Spec file is empty")

    try:
        return Spec(**data)
    except Exception as e:
        raise SystemExit(f"Invalid spec: {e}")


def get_api_key_if_set(spec: Spec, cli_api_key: Optional[str] = None) -> Optional[str]:
    """Return API key if set, else None. Does not raise."""
    if cli_api_key and cli_api_key.strip():
        return cli_api_key.strip()
    if spec.api_key and spec.api_key.strip():
        return spec.api_key.strip()
    return _resolve_api_key_safe(cli_api_key=None)


def get_effective_api_key(spec: Spec, cli_api_key: Optional[str] = None) -> str:
    """
    Get API key with precedence: CLI flag > spec api_key > env > config file.
    """
    if cli_api_key and cli_api_key.strip():
        return cli_api_key.strip()
    if spec.api_key and spec.api_key.strip():
        return spec.api_key.strip()
    return resolve_api_key(cli_api_key=None)
