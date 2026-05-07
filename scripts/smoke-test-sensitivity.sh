#!/usr/bin/env bash
# Sensitivity-tag smoke test (ADR-008).
#
# For each persona role, assume the role and run an Athena query against
# parts_inventory.unit_cost_usd. Expected outcome:
#   Dispatcher       -> Athena denies access (sensitivity=high gate)
#   TechnicianLead   -> Athena denies access (sensitivity=high gate)
#   Owner            -> rows returned
#
# This is the inverse of scripts/smoke-test.sh: that one shows PII redaction
# via the agent path; this one shows column-level sensitivity gating
# directly at Lake Formation, before any agent is involved.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="$REPO_ROOT/terraform/envs/demo"

DATABASE=$(terraform -chdir="$ENV_DIR" output -raw glue_database_name)
WORKGROUP=$(terraform -chdir="$ENV_DIR" output -raw athena_workgroup_name)
REGION=$(terraform -chdir="$ENV_DIR" output -json \
  | python3 -c "import json,sys; print(json.load(sys.stdin).get('region',{}).get('value','us-east-1'))" \
  2>/dev/null || echo "us-east-1")

DISPATCHER_ROLE_ARN=$(terraform -chdir="$ENV_DIR" output -raw dispatcher_role_arn)
TECHNICIAN_LEAD_ROLE_ARN=$(terraform -chdir="$ENV_DIR" output -raw technician_lead_role_arn)
OWNER_ROLE_ARN=$(terraform -chdir="$ENV_DIR" output -raw owner_role_arn)

QUERY="SELECT unit_cost_usd FROM parts_inventory LIMIT 5"
echo "Database: $DATABASE  Workgroup: $WORKGROUP  Region: $REGION"
echo "Query:    $QUERY"
echo

run_as() {
  local persona="$1" role_arn="$2" extra_tag_arg="${3:-}"
  echo "=== persona=$persona ==="

  local creds_json
  creds_json=$(aws sts assume-role \
    --role-arn "$role_arn" \
    --role-session-name "smoke-${persona}-$$" \
    --tags Key=role,Value="$persona" $extra_tag_arg \
    --transitive-tag-keys role $([[ -n "$extra_tag_arg" ]] && echo "service_region" || echo "") \
    --duration-seconds 900)

  AWS_ACCESS_KEY_ID=$(echo "$creds_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['AccessKeyId'])")
  AWS_SECRET_ACCESS_KEY=$(echo "$creds_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SecretAccessKey'])")
  AWS_SESSION_TOKEN=$(echo "$creds_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['Credentials']['SessionToken'])")
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN

  local exec_id
  exec_id=$(aws athena start-query-execution \
    --region "$REGION" \
    --work-group "$WORKGROUP" \
    --query-execution-context "Database=$DATABASE" \
    --query-string "$QUERY" \
    --output text \
    --query "QueryExecutionId")

  local state="" reason=""
  for _ in {1..60}; do
    local resp
    resp=$(aws athena get-query-execution --region "$REGION" --query-execution-id "$exec_id")
    state=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['QueryExecution']['Status']['State'])")
    if [[ "$state" == "SUCCEEDED" || "$state" == "FAILED" || "$state" == "CANCELLED" ]]; then
      reason=$(echo "$resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['QueryExecution']['Status'].get('StateChangeReason',''))")
      break
    fi
    sleep 1
  done

  echo "  state:  $state"
  if [[ "$state" == "SUCCEEDED" ]]; then
    aws athena get-query-results --region "$REGION" --query-execution-id "$exec_id" \
      --max-results 6 --query "ResultSet.Rows[].Data[].VarCharValue" --output text \
      | sed 's/^/  result: /'
  else
    echo "  reason: $reason"
  fi

  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
  echo
}

run_as dispatcher "$DISPATCHER_ROLE_ARN"
run_as technician_lead "$TECHNICIAN_LEAD_ROLE_ARN" "Key=service_region,Value=tempe-mesa"
run_as owner "$OWNER_ROLE_ARN"

echo
echo "Expected: dispatcher and technician_lead FAILED with access-denied;"
echo "owner SUCCEEDED with five rows of unit_cost_usd values."
