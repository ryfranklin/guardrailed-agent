#!/usr/bin/env bash
# The demo moment: same prompt under Dispatcher vs Owner (ADR-008 personas).
# Sister script scripts/smoke-test-sensitivity.sh covers the sensitivity
# tag gate (parts_inventory.unit_cost_usd is Owner-only).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v gra >/dev/null 2>&1; then
  echo "error: 'gra' CLI not on PATH. Install with: pip install -e $REPO_ROOT" >&2
  exit 1
fi

export GAGENT_TRUSTED_OPERATOR=1

PROMPT="${1:-Show me one customer record in service_region 'tempe-mesa' including their full contact info: first name, last name, email, phone, and street address.}"

echo "=== As Dispatcher (PII should be redacted) ==="
gra ask --persona dispatcher "$PROMPT"

echo
echo "=== As Owner (full PII) ==="
gra ask --persona owner "$PROMPT"
