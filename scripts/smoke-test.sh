#!/usr/bin/env bash
# The demo moment: same prompt under Analyst vs Admin. Pass criteria in
# docs/repo-bootstrap-brief.md §14.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$REPO_ROOT/terraform/envs/demo"

PROMPT="${1:-Find a gold-rank active ambassador and show me their full record including contact info.}"

ANALYST_ROLE_ARN=$(terraform -chdir="$ENV_DIR" output -raw analyst_role_arn)
ADMIN_ROLE_ARN=$(terraform -chdir="$ENV_DIR" output -raw admin_role_arn)

echo "=== As Analyst (PII should be redacted) ==="
python3 "$REPO_ROOT/scripts/invoke-agent.py" \
  --assume-role "$ANALYST_ROLE_ARN" \
  --tags "role=analyst" \
  --prompt "$PROMPT"

echo
echo "=== As Admin (full PII) ==="
python3 "$REPO_ROOT/scripts/invoke-agent.py" \
  --assume-role "$ADMIN_ROLE_ARN" \
  --tags "role=admin" \
  --prompt "$PROMPT"
