#!/bin/bash
#SBATCH --partition=notchpeak-dtn
#SBATCH --account=dtn
#SBATCH --time=3-00:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#
# Usage: sbatch --job-name=aq-raw-<country>-6pol --output=logs/%x-%j.out \
#          slurm/run_country.sbatch <country>
#
# Runs the OpenAQ extraction "measurements" + "output" phases for one
# country spec at whatever aggregation that spec file specifies (currently
# "raw" for all 5 - see docs/sensor-reporting-levels.md for why). Discover
# and sensors phases are skipped deliberately: locations.csv/sensors.csv are
# already committed to this branch from the reporting-level survey, so
# re-running them would just waste ~40 min of redundant API calls.

set -euo pipefail

REPO="/scratch/general/vast/u1522135/openaq-extractor-run"
ENV_FILE="/uufs/chpc.utah.edu/common/home/holmes-group3/Hitesh/.env"
cd "$REPO"

module load python/3.10.3 >/dev/null 2>&1

export PYTHONPATH="$REPO/src"
export OPENAQ_API_KEY
OPENAQ_API_KEY="$(grep '^x-api-key=' "$ENV_FILE" | cut -d'=' -f2-)"

COUNTRY="${1:?Usage: sbatch ... run_country.sbatch <country>}"
SPEC="specs/${COUNTRY}.yaml"

if [[ ! -f "$SPEC" ]]; then
    echo "Spec not found: $SPEC" >&2
    exit 1
fi

POLLUTANTS="$(awk '/^pollutants:/{f=1;next} /^[a-z]/{f=0} f' "$SPEC" | tr -d ' -' | paste -sd, -)"
AGGREGATION="$(awk -F': ' '/^aggregation:/{print $2}' "$SPEC" | awk '{print $1}')"

echo "=== $(date -u +%FT%TZ) : starting country=${COUNTRY} aggregation=${AGGREGATION} pollutants=${POLLUTANTS} ==="

"$REPO/.venv/bin/python3" -m openaq_extractor.cli run --spec "$SPEC" --phase measurements -v
"$REPO/.venv/bin/python3" -m openaq_extractor.cli run --spec "$SPEC" --phase output -v

echo "=== $(date -u +%FT%TZ) : finished country=${COUNTRY} ==="
