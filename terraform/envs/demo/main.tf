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

  lambda_function_name = "${local.s3_bucket_prefix}query-ambassadors-${local.env}"
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

  env                 = local.env
  langfuse_host       = var.langfuse_host
  langfuse_public_key = var.langfuse_public_key
  langfuse_secret_key = var.langfuse_secret_key
  tags                = local.common_tags
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

  env                       = local.env
  glue_database_name        = local.glue_database_name
  s3_bucket_prefix          = local.s3_bucket_prefix
  lf_admin_principal_arns   = local.lf_admin_principal_arns
  analyst_role_arn          = module.identity.analyst_role_arn
  regional_manager_role_arn = module.identity.regional_manager_role_arn
  admin_role_arn            = module.identity.admin_role_arn
  tags                      = local.common_tags
}

module "guardrails" {
  source = "../../modules/guardrails"

  env  = local.env
  tags = local.common_tags
}

module "tools" {
  source = "../../modules/tools"

  env                       = local.env
  lambda_source_dir         = "${path.module}/../../../lambdas/query_ambassadors"
  athena_workgroup_name     = module.data_plane.athena_workgroup_name
  athena_results_bucket_arn = module.data_plane.athena_results_bucket_arn
  data_bucket_arn           = module.data_plane.data_bucket_arn
  glue_database_name        = module.data_plane.glue_database_name
  persona_role_arns         = module.identity.all_persona_role_arns
  langfuse_secret_arn       = module.observability.langfuse_secret_arn
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
}

resource "aws_iam_role_policy" "analyst_invoke_agent" {
  name   = "invoke-agent"
  role   = module.identity.analyst_role_name
  policy = data.aws_iam_policy_document.persona_invoke_agent.json
}

resource "aws_iam_role_policy" "regional_manager_invoke_agent" {
  name   = "invoke-agent"
  role   = module.identity.regional_manager_role_name
  policy = data.aws_iam_policy_document.persona_invoke_agent.json
}

resource "aws_iam_role_policy" "admin_invoke_agent" {
  name   = "invoke-agent"
  role   = module.identity.admin_role_name
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
  }

  permissions                   = ["SELECT", "DESCRIBE"]
  permissions_with_grant_option = []
}
