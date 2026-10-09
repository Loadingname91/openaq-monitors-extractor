#!/bin/bash
#SBATCH --job-name=country-coverage
#SBATCH --clusters=kingspeak
#SBATCH --partition=soc-kp
#SBATCH --qos=soc-kp
#SBATCH --account=soc-kp
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=output/coverage/country_coverage_%j.log

# Usage (from the repo root): sbatch scripts/country_coverage.slurm   (check with: squeue -M kingspeak --me)
# Writes output/coverage/country/<country>.json, then rebuilds the five country report pages.
cd "$SLURM_SUBMIT_DIR"
python3 scripts/country_coverage.py "$@"
python3 scripts/build_country_reports.py
