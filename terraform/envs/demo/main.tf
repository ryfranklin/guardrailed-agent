terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = local.common_tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  env                   = "demo"
  s3_bucket_prefix      = "gagent-"
  glue_database_name    = "guardrailed_agent_${local.env}"
  account_id            = data.aws_caller_identity.current.account_id
  partition             = data.aws_partition.current.partition
  data_bucket_name      = "${local.s3_bucket_prefix}data-${local.env}-${local.account_id}"
  athena_bucket_name    = "${local.s3_bucket_prefix}athena-${local.env}-${local.account_id}"
  data_bucket_arn       = "arn:${local.partition}:s3:::${local.data_bucket_name}"
  athena_bucket_arn     = "arn:${local.partition}:s3:::${local.athena_bucket_name}"
  athena_workgroup_name = "${local.s3_bucket_prefix}${local.env}"

  lambda_function_name = "${local.s3_bucket_prefix}governed-query-${local.env}"
  lambda_role_arn      = "arn:${local.partition}:iam::${local.account_id}:role/${local.lambda_function_name}-exec"

  common_tags = merge(var.tags, {
    Project   = "guardrailed-agent"
    Env       = local.env
    ManagedBy = "terraform"
  })

  lf_admin_principal_arns = concat(
    var.lf_admin_principal_arns,
    [data.aws_caller_identity.current.arn],
  )

  trusted_assumer_arns = concat(
    var.trusted_assumer_arns,
    [
      data.aws_caller_identity.current.arn,
      local.lambda_role_arn,
    ],
  )
}

module "observability" {
  source = "../../modules/observability"

  env                = local.env
  log_group_name     = var.invocation_log_group
  log_retention_days = var.invocation_log_retention_days
  tags               = local.common_tags
}

module "identity" {
  source = "../../modules/identity"

  env                   = local.env
  trusted_assumer_arns  = local.trusted_assumer_arns
  data_bucket_arns      = [local.data_bucket_arn, local.athena_bucket_arn]
  glue_database_name    = local.glue_database_name
  athena_workgroup_name = local.athena_workgroup_name
  tags                  = local.common_tags
}

module "data_plane" {
  source = "../../modules/data-plane"

  env                      = local.env
  glue_database_name       = local.glue_database_name
  s3_bucket_prefix         = local.s3_bucket_prefix
  lf_admin_principal_arns  = local.lf_admin_principal_arns
  dispatcher_role_arn      = module.identity.dispatcher_role_arn
  technician_lead_role_arn = module.identity.technician_lead_role_arn
  owner_role_arn           = module.identity.owner_role_arn
  tags                     = local.common_tags
}

module "guardrails" {
  source = "../../modules/guardrails"

  env  = local.env
  tags = local.common_tags
}

module "tools" {
  source = "../../modules/tools"

  env                       = local.env
  lambda_source_dir         = "${path.module}/../../../lambdas/governed_query"
  athena_workgroup_name     = module.data_plane.athena_workgroup_name
  athena_results_bucket_arn = module.data_plane.athena_results_bucket_arn
  glue_database_name        = module.data_plane.glue_database_name
  persona_role_arns         = module.identity.all_persona_role_arns
  invocation_log_group      = module.observability.invocation_log_group
  invocation_log_group_arn  = module.observability.invocation_log_group_arn
  tags                      = local.common_tags
}

module "agent" {
  source = "../../modules/agent"

  env                         = local.env
  foundation_model_id         = var.foundation_model_id
  guardrail_id                = module.guardrails.guardrail_id
  guardrail_version           = module.guardrails.guardrail_version
  action_group_lambda_arn     = module.tools.lambda_arn
  action_group_openapi_schema = module.tools.openapi_schema_inline
  tags                        = local.common_tags
}

data "aws_iam_policy_document" "persona_invoke_agent" {
  statement {
    sid    = "InvokeThisAgent"
    effect = "Allow"
    actions = [
      "bedrock:InvokeAgent",
    ]
    resources = [
      module.agent.agent_alias_arn,
    ]
  }

  statement {
    sid    = "EmitInvocationTrace"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      module.observability.invocation_log_group_arn,
      "${module.observability.invocation_log_group_arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "dispatcher_invoke_agent" {
  name   = "invoke-agent"
  role   = module.identity.dispatcher_role_name
  policy = data.aws_iam_policy_document.persona_invoke_agent.json
}

resource "aws_iam_role_policy" "technician_lead_invoke_agent" {
  name   = "invoke-agent"
  role   = module.identity.technician_lead_role_name
  policy = data.aws_iam_policy_document.persona_invoke_agent.json
}

resource "aws_iam_role_policy" "owner_invoke_agent" {
  name   = "invoke-agent"
  role   = module.identity.owner_role_name
  policy = data.aws_iam_policy_document.persona_invoke_agent.json
}

resource "aws_lakeformation_permissions" "smus_reader_database_describe" {
  for_each = toset(var.smus_reader_role_arns)

  principal = each.value

  database {
    name = module.data_plane.glue_database_name
  }

  permissions                   = ["DESCRIBE"]
  permissions_with_grant_option = []
}

resource "aws_lakeformation_permissions" "smus_reader_lf_tag_all" {
  for_each = toset(var.smus_reader_role_arns)

  principal = each.value

  lf_tag_policy {
    resource_type = "TABLE"

    expression {
      key    = module.data_plane.lf_tag_pii_key
      values = module.data_plane.lf_tag_pii_values
    }

    expression {
      key    = module.data_plane.lf_tag_sensitivity_key
      values = module.data_plane.lf_tag_sensitivity_values
    }
  }

  permissions                   = ["SELECT", "DESCRIBE"]
  permissions_with_grant_option = []
}

# ---- MCP governance-tool reader policy (ADR-009 Phase 2.b) ----
#
# Read-only permissions the MCP server needs for explain_governance,
# eval_query, and audit_trace. Surface area is strictly read against the
# Glue catalog, Lake Formation policy, CloudTrail, and the data bucket.
#
# In Shape A (single-operator) this is informational — the operator already
# has admin-equivalent permissions. The policy exists so that:
#   * client deployments where the operator is *not* admin can attach it
#     directly to a least-privilege MCP-runner principal,
#   * the Phase 2.d Shape B server-side IAM role (Prompt 2.7) can attach it.
#
# The policy is created but not bound to any principal here. Output the ARN
# so callers can attach it explicitly.

data "aws_iam_policy_document" "mcp_governance_reader" {
  statement {
    sid    = "GlueReadCatalog"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
      "glue:GetColumnStatisticsForTable",
    ]
    resources = [
      "arn:${local.partition}:glue:*:${local.account_id}:catalog",
      "arn:${local.partition}:glue:*:${local.account_id}:database/${module.data_plane.glue_database_name}",
      "arn:${local.partition}:glue:*:${local.account_id}:table/${module.data_plane.glue_database_name}/*",
    ]
  }

  statement {
    sid    = "LakeFormationReadPolicy"
    effect = "Allow"
    actions = [
      "lakeformation:GetResourceLFTags",
      "lakeformation:ListPermissions",
      "lakeformation:ListLFTags",
      "lakeformation:GetLFTag",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "CloudTrailLookupForAuditTrace"
    effect    = "Allow"
    actions   = ["cloudtrail:LookupEvents"]
    resources = ["*"]
  }

  statement {
    sid    = "S3ReadDataBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [local.data_bucket_arn]
  }

  statement {
    sid    = "ReadInvocationLogs"
    effect = "Allow"
    actions = [
      "logs:StartQuery",
      "logs:StopQuery",
      "logs:GetQueryResults",
      "logs:DescribeQueries",
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      module.observability.invocation_log_group_arn,
      "${module.observability.invocation_log_group_arn}:*",
    ]
  }
}

resource "aws_iam_policy" "mcp_governance_reader" {
  name        = "${local.s3_bucket_prefix}mcp-governance-reader-${local.env}"
  description = "Read-only governance probe permissions for the MCP server's tools 4-6 (ADR-009 Phase 2.b). Attach to MCP-runner principals; not auto-bound."
  policy      = data.aws_iam_policy_document.mcp_governance_reader.json
}
