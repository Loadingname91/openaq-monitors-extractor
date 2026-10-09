#!/bin/bash
#SBATCH --job-name=pm25-coverage
#SBATCH --clusters=kingspeak
#SBATCH --partition=soc-kp
#SBATCH --qos=soc-kp
#SBATCH --account=soc-kp
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=output/coverage/pm25_coverage_%j.log

# Usage (from the repo root): sbatch scripts/pm25_coverage.slurm   (check with: squeue -M kingspeak --me)
# Uses the same python3 the login shell provides (pandas, matplotlib). Also rebuilds the HTML report.
cd "$SLURM_SUBMIT_DIR"
python3 scripts/pm25_coverage.py "$@"
python3 scripts/build_report.py
