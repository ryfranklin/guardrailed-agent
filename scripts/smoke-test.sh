#!/usr/bin/env bash
# The demo moment: same prompt under Dispatcher vs Owner (ADR-008 personas).
# Sister script scripts/smoke-test-sensitivity.sh covers the sensitivity
# tag gate (parts_inventory.unit_cost_usd is Owner-only).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$REPO_ROOT/terraform/envs/demo"
EVAL_DIR="$REPO_ROOT/eval"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  for candidate in python3.12 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      ver=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")
      major=${ver%.*}; minor=${ver#*.}
      if [[ "$major" -ge 3 && "$minor" -ge 12 ]]; then
        PYTHON_BIN=$(command -v "$candidate"); break
      fi
    fi
  done
fi
if [[ -z "$PYTHON_BIN" ]]; then
  echo "error: need python3.12 or newer. Set PYTHON_BIN to override." >&2
  exit 1
fi

if [[ ! -d "$EVAL_DIR/.venv" ]]; then
  "$PYTHON_BIN" -m venv "$EVAL_DIR/.venv"
  "$EVAL_DIR/.venv/bin/pip" install -q --upgrade pip
  "$EVAL_DIR/.venv/bin/pip" install -q -r "$EVAL_DIR/requirements.txt"
fi
VENV_PY="$EVAL_DIR/.venv/bin/python"

PROMPT="${1:-Show me one customer record in service_region 'tempe-mesa' including their full contact info: first name, last name, email, phone, and street address.}"

DISPATCHER_ROLE_ARN=$(terraform -chdir="$ENV_DIR" output -raw dispatcher_role_arn)
OWNER_ROLE_ARN=$(terraform -chdir="$ENV_DIR" output -raw owner_role_arn)

echo "=== As Dispatcher (PII should be redacted) ==="
"$VENV_PY" "$REPO_ROOT/scripts/invoke-agent.py" \
  --assume-role "$DISPATCHER_ROLE_ARN" \
  --tags "role=dispatcher" \
  --prompt "$PROMPT"

echo
echo "=== As Owner (full PII) ==="
"$VENV_PY" "$REPO_ROOT/scripts/invoke-agent.py" \
  --assume-role "$OWNER_ROLE_ARN" \
  --tags "role=owner" \
  --prompt "$PROMPT"
