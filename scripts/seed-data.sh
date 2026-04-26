#!/usr/bin/env bash
# Generate synthetic ambassador data and register the four Iceberg tables in Glue.
# Reads target bucket, database, workgroup from terraform outputs.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$REPO_ROOT/terraform/envs/demo"
SYNTH_DIR="$REPO_ROOT/data/synthesizer"

BUCKET=$(terraform -chdir="$ENV_DIR" output -raw data_bucket_name)
DATABASE=$(terraform -chdir="$ENV_DIR" output -raw glue_database_name)
WORKGROUP=$(terraform -chdir="$ENV_DIR" output -raw athena_workgroup_name)
REGION=$(terraform -chdir="$ENV_DIR" output -json | python3 -c "import json,sys; print(json.load(sys.stdin).get('region',{}).get('value','us-east-1'))" 2>/dev/null || echo "us-east-1")

echo "Seeding $DATABASE in $BUCKET (workgroup=$WORKGROUP, region=$REGION)"

cd "$SYNTH_DIR"

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

if [[ ! -d ".venv" ]]; then
  "$PYTHON_BIN" -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r requirements.txt
fi

./.venv/bin/python generate.py \
  --bucket "$BUCKET" \
  --database "$DATABASE" \
  --workgroup "$WORKGROUP" \
  --region "$REGION" \
  --all

echo "Seed complete. Tables registered: ambassador, ambassador_team, order_fact, signal_fact."
echo "PII columns tagged with pii=true via Lake Formation."
