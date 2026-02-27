"""Unit tests for pipeline with mocked OpenAQ."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from openaq_extractor.config import Spec, load_spec
from openaq_extractor.pipeline import run_pipeline


def _make_mock_location(loc_id: int) -> SimpleNamespace:
    coords = SimpleNamespace(latitude=28.5, longitude=77.1)
    dt_first = SimpleNamespace(utc="2024-01-01T00:00:00Z")
    dt_last = SimpleNamespace(utc="2024-12-31T23:59:59Z")
    return SimpleNamespace(
        id=loc_id,
        name=f"Location {loc_id}",
        locality="Delhi",
        coordinates=coords,
        is_monitor=True,
        is_mobile=False,
        datetime_first=dt_first,
        datetime_last=dt_last,
    )


def _make_mock_locations_response(count: int = 2) -> SimpleNamespace:
    results = [_make_mock_location(i) for i in range(1, count + 1)]
    meta = SimpleNamespace(found=count)
    resp = SimpleNamespace(results=results, meta=meta)
    return resp


def _make_mock_sensor(sensor_id: int, param_name: str, loc_id: int) -> SimpleNamespace:
    param = SimpleNamespace(id=1, name=param_name, units="µg/m³")
    latest_dt = SimpleNamespace(utc="2024-12-31T12:00:00Z")
    latest = SimpleNamespace(value=42.0, datetime=latest_dt)
    return SimpleNamespace(
        id=sensor_id,
        name=f"Sensor {sensor_id}",
        parameter=param,
        latest=latest,
    )


def _make_mock_sensors_response(loc_id: int) -> SimpleNamespace:
    results = [
        _make_mock_sensor(100 + loc_id, "pm25", loc_id),
        _make_mock_sensor(200 + loc_id, "no2", loc_id),
    ]
    return SimpleNamespace(results=results)


@patch("openaq_extractor.pipeline.OpenAQ")
def test_run_pipeline_phase_discover(
    mock_openaq_class: MagicMock,
    sample_spec_path: Path,
    temp_output_dir: Path,
) -> None:
    """run_pipeline with phase=discover calls locations.list and writes locations.csv."""
    spec = load_spec(sample_spec_path)
    spec.output.directory = str(temp_output_dir)

    mock_client = MagicMock()
    mock_client.locations.list.return_value = _make_mock_locations_response(count=2)
    mock_openaq_class.return_value = mock_client

    run_pipeline(
        spec,
        api_key="test-key",
        phase="discover",
        verbose=False,
    )

    mock_client.locations.list.assert_called()
    call_kw = mock_client.locations.list.call_args[1]
    assert call_kw.get("iso") == "IN"
    assert call_kw.get("monitor") is True
    assert call_kw.get("mobile") is False

    locations_csv = temp_output_dir / "locations.csv"
    assert locations_csv.exists()
    df = pd.read_csv(locations_csv)
    assert len(df) == 2
    assert "location_id" in df.columns
    assert "name" in df.columns


@patch("openaq_extractor.pipeline.OpenAQ")
def test_run_pipeline_phase_sensors_loads_locations(
    mock_openaq_class: MagicMock,
    sample_spec_path: Path,
    temp_output_dir: Path,
) -> None:
    """run_pipeline with phase=sensors loads locations.csv and calls locations.sensors per row."""
    # Write locations.csv first
    locations_df = pd.DataFrame({
        "location_id": [1, 2],
        "name": ["L1", "L2"],
        "locality": ["A", "B"],
        "latitude": [28.5, 28.6],
        "longitude": [77.1, 77.2],
        "is_monitor": [True, True],
        "is_mobile": [False, False],
        "datetime_first": ["2024-01-01", "2024-01-01"],
        "datetime_last": ["2024-12-31", "2024-12-31"],
    })
    locations_df.to_csv(temp_output_dir / "locations.csv", index=False)

    spec = load_spec(sample_spec_path)
    spec.output.directory = str(temp_output_dir)

    mock_client = MagicMock()
    mock_client.locations.sensors.side_effect = [
        _make_mock_sensors_response(1),
        _make_mock_sensors_response(2),
    ]
    mock_openaq_class.return_value = mock_client

    run_pipeline(
        spec,
        api_key="test-key",
        phase="sensors",
        verbose=False,
    )

    assert mock_client.locations.sensors.call_count == 2
    mock_client.locations.sensors.assert_any_call(1)
    mock_client.locations.sensors.assert_any_call(2)

    sensors_csv = temp_output_dir / "sensors.csv"
    assert sensors_csv.exists()
    df = pd.read_csv(sensors_csv)
    assert len(df) >= 1
    assert "sensor_id" in df.columns
    assert "parameter_name" in df.columns
