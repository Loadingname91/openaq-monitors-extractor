#!/usr/bin/env python3
"""Build one coverage report page per country from country_coverage.py's JSON.

Reads output/coverage/country/<country>.json and injects it into
scripts/country_report_template.html at the /*__REPORT_DATA__*/ placeholder, writing
output/coverage/report/<country>_coverage_report.html. Each page is self-contained
(it loads only D3 and Google Fonts from their CDNs).

Cheap to run (JSON only) -- fine on a login node.
"""

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = Path(__file__).resolve().parent / "country_report_template.html"
PLACEHOLDER = "/*__REPORT_DATA__*/null"
TITLE = "/*__TITLE__*/"
COUNTRIES = ["india", "pakistan", "nepal", "bangladesh", "bhutan"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--country", nargs="+", default=COUNTRIES, choices=COUNTRIES)
    parser.add_argument("--in-dir", default=str(REPO_ROOT / "output" / "coverage" / "country"))
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "output" / "coverage" / "report"))
    args = parser.parse_args()

    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise SystemExit(f"{TEMPLATE} must contain {PLACEHOLDER} exactly once")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for c in args.country:
        src = Path(args.in_dir) / f"{c}.json"
        if not src.exists():
            print(f"skip {c}: no {src}")
            continue
        # "</" inside a <script> block would end it early; JSON allows the escaped form
        payload = src.read_text(encoding="utf-8").replace("</", "<\\/")
        html = template.replace(PLACEHOLDER, payload).replace(TITLE, f"{c.capitalize()} Sensor Coverage")
        out = out_dir / f"{c}_coverage_report.html"
        out.write_text(html, encoding="utf-8")
        print(f"wrote {out} ({len(html) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
