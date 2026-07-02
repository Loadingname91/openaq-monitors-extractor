"""CLI entry point for OpenAQ extractor."""

from pathlib import Path
from typing import Literal

import click
from openaq import OpenAQ

from .config import get_api_key_if_set, get_effective_api_key, load_spec
from .pipeline import run_pipeline

PhaseChoice = Literal["discover", "sensors", "measurements", "output"]


@click.group()
@click.version_option(version="0.1.0", prog_name="openaq-extract")
def main() -> None:
    """OpenAQ Monitor Data Extractor - Extract air quality data from South Asia monitors."""
    pass


@main.command("run")
@click.option(
    "--spec",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to YAML spec file",
)
@click.option(
    "--api-key",
    envvar="OPENAQ_API_KEY",
    help="OpenAQ API key (overrides OPENAQ_API_KEY env)",
)
@click.option(
    "--phase",
    "-p",
    type=click.Choice(["discover", "sensors", "measurements", "output"]),
    default=None,
    help="Run only this phase",
)
@click.option("--dry-run", is_flag=True, help="Validate spec and show what would run, no API calls")
@click.option("-v", "verbose", is_flag=True, help="Verbose output")
@click.option("--debug", is_flag=True, help="Enable debug logging with tracebacks")
@click.option(
    "--log-file",
    type=click.Path(path_type=Path),
    default=None,
    help="Log file path (default: {output_dir}/openaq-extract.log)",
)
def run(
    spec: Path,
    api_key: str | None,
    phase: PhaseChoice | None,
    dry_run: bool,
    verbose: bool,
    debug: bool,
    log_file: Path | None,
) -> None:
    """Run the extraction pipeline with the given spec file."""
    loaded = load_spec(spec)
    if dry_run:
        click.echo(f"Dry run: spec '{loaded.name}' (country={loaded.country.iso})")
        click.echo(f"  Pollutants: {loaded.pollutants}")
        click.echo(f"  Aggregation: {loaded.aggregation}")
        click.echo(f"  Date range: {loaded.date_range.from_} .. {loaded.date_range.to}")
        click.echo(f"  Output: {loaded.output.directory}")
        key = get_api_key_if_set(loaded, api_key)
        click.echo(f"  API key: {'OK' if key else 'NOT SET (required for actual run)'}")
        if phase:
            click.echo(f"  Phase: {phase}")
        else:
            click.echo("  Phases: all (discover -> sensors -> measurements -> output)")
        return
    run_pipeline(
        loaded,
        api_key=api_key,
        phase=phase,
        dry_run=False,
        verbose=verbose,
        debug=debug,
        log_file=log_file,
    )


@main.command("validate")
@click.option(
    "--spec",
    "-s",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Path to YAML spec file",
)
def validate(spec: Path) -> None:
    """Validate a spec file without running the pipeline."""
    loaded = load_spec(spec)
    click.echo(f"Valid: {loaded.name}")
    click.echo(f"  Country: {loaded.country.iso}")
    click.echo(f"  Pollutants: {loaded.pollutants}")
    click.echo(f"  Aggregation: {loaded.aggregation}")


@main.command("list-countries")
@click.option(
    "--api-key",
    envvar="OPENAQ_API_KEY",
    help="OpenAQ API key (or set OPENAQ_API_KEY)",
)
@click.option("--limit", default=100, help="Max results")
def list_countries(api_key: str | None, limit: int) -> None:
    """List countries available in OpenAQ."""
    from .config import resolve_api_key
    key = resolve_api_key(cli_api_key=api_key)
    client = OpenAQ(api_key=key)
    try:
        resp = client.countries.list(limit=limit)
        for c in resp.results:
            click.echo(f"  {c.code}  {c.name}")
        click.echo(f"  ({len(resp.results)} shown)")
    finally:
        client.close()


@main.command("list-parameters")
@click.option(
    "--api-key",
    envvar="OPENAQ_API_KEY",
    help="OpenAQ API key (or set OPENAQ_API_KEY)",
)
@click.option("--limit", default=100, help="Max results")
def list_parameters(api_key: str | None, limit: int) -> None:
    """List parameters (pollutants) available in OpenAQ."""
    from .config import resolve_api_key
    key = resolve_api_key(cli_api_key=api_key)
    client = OpenAQ(api_key=key)
    try:
        resp = client.parameters.list(limit=limit)
        for p in resp.results:
            click.echo(f"  {p.name}  (id={p.id})  {p.units}")
        click.echo(f"  ({len(resp.results)} shown)")
    finally:
        client.close()


if __name__ == "__main__":
    main()
