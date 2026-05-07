#!/usr/bin/env bash
# Generate the ADR-008 HVAC synthetic dataset, stage to S3, register the
# Iceberg tables in Glue, and apply Lake Formation column tags.
# Reads target bucket, database, workgroup, region from terraform outputs.
#
# Pipeline:
#   1. generate.py             -> 10 parquet files (core + supporting), upload to S3
#   2. telemetry.py            -> equipment_telemetry_daily.parquet
#   3. utilization.py          -> technician_utilization_daily.parquet
#   3b. aws s3 cp              -> upload the two rollups to staging/
#   4. register-iceberg.py     -> CTAS each of the twelve tables into Iceberg
#   5. apply-lf-tags.py        -> column-level pii + sensitivity tags
#
# Idempotent end-to-end: re-running drops/recreates Iceberg tables and
# re-applies tags.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$REPO_ROOT/terraform/envs/demo"
SYNTH_DIR="$REPO_ROOT/data/synthesizer"
OUTPUT_DIR="$SYNTH_DIR/output"

BUCKET=$(terraform -chdir="$ENV_DIR" output -raw data_bucket_name)
DATABASE=$(terraform -chdir="$ENV_DIR" output -raw glue_database_name)
WORKGROUP=$(terraform -chdir="$ENV_DIR" output -raw athena_workgroup_name)
REGION=$(terraform -chdir="$ENV_DIR" output -json \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('region',{}).get('value','us-east-1'))" \
  2>/dev/null || echo "us-east-1")

echo "Seeding $DATABASE in $BUCKET (workgroup=$WORKGROUP, region=$REGION)"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")
      major=${ver%.*}
      minor=${ver#*.}
      if [[ "$major" -ge 3 && "$minor" -ge 12 ]]; then
        PYTHON_BIN=$(command -v "$candidate")
        break
      fi
    fi
  done
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "error: need python3.12 or newer. Set PYTHON_BIN to override." >&2
  exit 1
fi
echo "Using $PYTHON_BIN ($("$PYTHON_BIN" --version))"

if [[ ! -d "$SYNTH_DIR/.venv" ]]; then
  "$PYTHON_BIN" -m venv "$SYNTH_DIR/.venv"
  "$SYNTH_DIR/.venv/bin/pip" install -q --upgrade pip
  "$SYNTH_DIR/.venv/bin/pip" install -q -r "$SYNTH_DIR/requirements.txt"
fi
VENV_PY="$SYNTH_DIR/.venv/bin/python"

cd "$REPO_ROOT"

echo "[1/5] generating core + supporting tables -> $OUTPUT_DIR (and uploading to S3)"
"$VENV_PY" -m data.synthesizer.generate \
  --output "$OUTPUT_DIR" \
  --region "$REGION" \
  --bucket "$BUCKET" \
  --upload

echo "[2/5] generating equipment_telemetry_daily"
"$VENV_PY" -m data.synthesizer.telemetry \
  --input "$OUTPUT_DIR" \
  --output "$OUTPUT_DIR" \
  --seed 42 \
  --days 365

echo "[3/5] generating technician_utilization_daily"
"$VENV_PY" -m data.synthesizer.utilization \
  --input "$OUTPUT_DIR" \
  --output "$OUTPUT_DIR" \
  --days 365

echo "[3b/5] uploading rollup parquet to s3://$BUCKET/staging/"
aws s3 cp "$OUTPUT_DIR/equipment_telemetry_daily.parquet" \
  "s3://$BUCKET/staging/equipment_telemetry_daily/data.parquet" \
  --region "$REGION" --no-progress
aws s3 cp "$OUTPUT_DIR/technician_utilization_daily.parquet" \
  "s3://$BUCKET/staging/technician_utilization_daily/data.parquet" \
  --region "$REGION" --no-progress

echo "[4/5] registering Iceberg tables via Athena CTAS"
"$VENV_PY" "$REPO_ROOT/scripts/register-iceberg.py" \
  --bucket "$BUCKET" \
  --database "$DATABASE" \
  --workgroup "$WORKGROUP" \
  --region "$REGION"

echo "[5/5] applying Lake Formation column tags"
"$VENV_PY" "$REPO_ROOT/scripts/apply-lf-tags.py" \
  --database "$DATABASE" \
  --region "$REGION"

echo
echo "Seed pipeline complete."
echo "To verify column-by-column tag state matches ADR-008 expectations:"
echo "  $VENV_PY $REPO_ROOT/scripts/apply-lf-tags.py --database $DATABASE --region $REGION --verify"
