# OpenAQ Monitor Data Extractor

CLI to extract air quality monitor data from OpenAQ for South Asia (India, Nepal, Pakistan, etc.) using YAML spec files.

## Setup

1. Install: `pip install -e .` (or `pip install .`)
2. Get API key: [explore.openaq.org/register](https://explore.openaq.org/register)
3. Set key: `export OPENAQ_API_KEY=your-key` (or add to `.env`)
4. Run: `openaq-extract run --spec specs/india.yaml`

## Usage

```bash
# Run full pipeline with a spec file
python -m openaq_extractor.cli run --spec specs/india.yaml

# Dry run (no API calls)
python -m openaq_extractor.cli run --spec specs/india.yaml --dry-run

# Validate a spec file
python -m openaq_extractor.cli validate --spec specs/india.yaml

# List available countries and parameters
python -m openaq_extractor.cli list-countries
python -m openaq_extractor.cli list-parameters


# Run specifc phase
python -m openaq_extractor.cli run --spec specs/india.yaml --phase discover
python -m openaq_extractor.cli run --spec specs/india.yaml --phase sensors
python -m openaq_extractor.cli run --spec specs/india.yaml --phase measurements
python -m openaq_extractor.cli run --spec specs/india.yaml --phase output
```

## Spec File

Create a YAML spec (e.g. `specs/india.yaml`) with:

- `country.iso`: 2-letter ISO code (IN, NP, PK, etc.)
- `pollutants`: list (pm25, pm10, no2, so2, o3, co)
- `aggregation`: raw | hourly | daily | monthly | yearly
- `date_range.from` / `date_range.to`
- `output.directory`
- `filters.monitor` / `filters.mobile`
