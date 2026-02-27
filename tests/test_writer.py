"""Unit tests for writer module."""

import json
from pathlib import Path

from openaq_extractor.config import load_spec
from openaq_extractor.writer import write_run_summary


def test_write_run_summary_creates_file(
    sample_spec_path: Path, temp_output_dir: Path
) -> None:
    """write_run_summary creates run_summary.json with correct keys."""
    spec = load_spec(sample_spec_path)
    write_run_summary(
        temp_output_dir,
        spec,
        total_locations=10,
        total_sensors=25,
        total_measurements=1000,
        runtime_seconds=12.5,
    )
    out_path = temp_output_dir / "run_summary.json"
    assert out_path.exists()
    with open(out_path) as f:
        data = json.load(f)
    assert "spec" in data
    assert data["spec"]["name"] == spec.name
    assert data["spec"]["country_iso"] == spec.country.iso
    assert data["total_locations"] == 10
    assert data["total_sensors"] == 25
    assert data["total_measurements"] == 1000
    assert data["runtime_seconds"] == 12.5
    assert "output_directory" in data
