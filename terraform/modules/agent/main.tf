terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

locals {
  agent_name = "${var.name_prefix}${var.env}"
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name
  partition  = data.aws_partition.current.partition

  foundation_model_arn = "arn:${local.partition}:bedrock:${local.region}::foundation-model/${var.foundation_model_id}"
  guardrail_arn        = "arn:${local.partition}:bedrock:${local.region}:${local.account_id}:guardrail/${var.guardrail_id}"
}

data "aws_iam_policy_document" "agent_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["bedrock.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:${local.partition}:bedrock:${local.region}:${local.account_id}:agent/*"]
    }
  }
}

data "aws_iam_policy_document" "agent_policy" {
  statement {
    sid    = "InvokeFoundationModel"
    effect = "Allow"
    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
    ]
    resources = [local.foundation_model_arn]
  }

  statement {
    sid       = "InvokeActionGroupLambda"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [var.action_group_lambda_arn]
  }

  statement {
    sid       = "ApplyGuardrail"
    effect    = "Allow"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [local.guardrail_arn]
  }
}

resource "aws_iam_role" "agent" {
  name               = "AmazonBedrockExecutionRoleForAgents_${local.agent_name}"
  description        = "Execution role for the ${local.agent_name} Bedrock Agent."
  assume_role_policy = data.aws_iam_policy_document.agent_trust.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "agent" {
  name   = "agent-policy"
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.agent_policy.json
}

resource "aws_bedrockagent_agent" "this" {
  agent_name                  = local.agent_name
  description                 = "Guardrailed analyst assistant for the ${var.env} ambassador dataset."
  agent_resource_role_arn     = aws_iam_role.agent.arn
  foundation_model            = var.foundation_model_id
  instruction                 = var.agent_instructions
  idle_session_ttl_in_seconds = var.idle_session_ttl

  guardrail_configuration {
    guardrail_identifier = var.guardrail_id
    guardrail_version    = var.guardrail_version
  }

  prepare_agent = true

  tags = var.tags
}

resource "aws_bedrockagent_agent_action_group" "query_ambassadors" {
  agent_id                   = aws_bedrockagent_agent.this.agent_id
  agent_version              = "DRAFT"
  action_group_name          = "query_ambassadors"
  description                = "Query the governed ambassador dataset. Lake Formation enforces per-persona row and column visibility."
  action_group_state         = "ENABLED"
  skip_resource_in_use_check = true

  action_group_executor {
    lambda = var.action_group_lambda_arn
  }

  api_schema {
    payload = var.action_group_openapi_schema
  }
}

resource "aws_bedrockagent_agent_alias" "live" {
  agent_id         = aws_bedrockagent_agent.this.agent_id
  agent_alias_name = var.agent_alias_name
  description      = "Published alias for ${local.agent_name}."

  depends_on = [aws_bedrockagent_agent_action_group.query_ambassadors]

  tags = var.tags
}
