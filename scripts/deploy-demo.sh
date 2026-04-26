#!/usr/bin/env bash
# Deploy the Demo environment. Run from anywhere — script resolves its own path.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$REPO_ROOT/terraform/envs/demo"

if [[ ! -f "$ENV_DIR/terraform.tfvars" ]]; then
  echo "error: $ENV_DIR/terraform.tfvars not found." >&2
  echo "Copy terraform.tfvars.example and fill in langfuse_public_key, langfuse_secret_key." >&2
  exit 1
fi

cd "$ENV_DIR"
terraform init -input=false
terraform apply -input=false "$@"

echo
echo "Demo deployed. Next steps:"
echo "  $REPO_ROOT/scripts/seed-data.sh"
echo "  $REPO_ROOT/scripts/smoke-test.sh"
