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
  account_id       = data.aws_caller_identity.current.account_id
  partition        = data.aws_partition.current.partition
  region           = data.aws_region.current.name
  athena_wg_arn    = "arn:${local.partition}:athena:${local.region}:${local.account_id}:workgroup/${var.athena_workgroup_name}"
  glue_db_arn      = "arn:${local.partition}:glue:${local.region}:${local.account_id}:database/${var.glue_database_name}"
  glue_table_arn   = "arn:${local.partition}:glue:${local.region}:${local.account_id}:table/${var.glue_database_name}/*"
  glue_catalog_arn = "arn:${local.partition}:glue:${local.region}:${local.account_id}:catalog"
}

data "aws_iam_policy_document" "persona_trust_dispatcher" {
  statement {
    sid     = "AllowAssumeWithRoleTagDispatcher"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_assumer_arns
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/role"
      values   = ["dispatcher"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["role"]
    }
  }
}

data "aws_iam_policy_document" "persona_trust_technician_lead" {
  statement {
    sid     = "AllowAssumeWithRoleTagTechnicianLead"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_assumer_arns
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/role"
      values   = ["technician_lead"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["role", "service_region"]
    }

    condition {
      test     = "StringLike"
      variable = "aws:RequestTag/service_region"
      values   = ["*"]
    }
  }
}

data "aws_iam_policy_document" "persona_trust_owner" {
  statement {
    sid     = "AllowAssumeWithRoleTagOwner"
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]

    principals {
      type        = "AWS"
      identifiers = var.trusted_assumer_arns
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/role"
      values   = ["owner"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["role"]
    }
  }
}

data "aws_iam_policy_document" "data_access" {
  statement {
    sid    = "AthenaQuery"
    effect = "Allow"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetQueryResultsStream",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
      "athena:ListQueryExecutions",
    ]
    resources = [local.athena_wg_arn]
  }

  statement {
    sid    = "GlueCatalogRead"
    effect = "Allow"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = [
      local.glue_catalog_arn,
      local.glue_db_arn,
      local.glue_table_arn,
    ]
  }

  statement {
    sid       = "LakeFormationGetDataAccess"
    effect    = "Allow"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }

  statement {
    sid    = "S3DataRead"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = concat(
      var.data_bucket_arns,
      [for arn in var.data_bucket_arns : "${arn}/*"],
    )
  }

  statement {
    sid    = "S3AthenaResultsWrite"
    effect = "Allow"
    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [for arn in var.data_bucket_arns : "${arn}/*"]
  }
}

resource "aws_iam_role" "dispatcher" {
  name               = "${var.role_name_prefix}dispatcher-${var.env}"
  description        = "Dispatcher persona - column-masked PII via Lake Formation LF-Tag policy. Front-desk view (ADR-008)."
  assume_role_policy = data.aws_iam_policy_document.persona_trust_dispatcher.json
  tags               = var.tags
}

resource "aws_iam_role" "technician_lead" {
  name               = "${var.role_name_prefix}technician-lead-${var.env}"
  description        = "TechnicianLead persona - full PII for the assigned service_region only (ADR-008)."
  assume_role_policy = data.aws_iam_policy_document.persona_trust_technician_lead.json
  tags               = var.tags
}

resource "aws_iam_role" "owner" {
  name               = "${var.role_name_prefix}owner-${var.env}"
  description        = "Owner persona - unrestricted access including sensitivity=high columns (ADR-008)."
  assume_role_policy = data.aws_iam_policy_document.persona_trust_owner.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "dispatcher_data_access" {
  name   = "data-access"
  role   = aws_iam_role.dispatcher.id
  policy = data.aws_iam_policy_document.data_access.json
}

resource "aws_iam_role_policy" "technician_lead_data_access" {
  name   = "data-access"
  role   = aws_iam_role.technician_lead.id
  policy = data.aws_iam_policy_document.data_access.json
}

resource "aws_iam_role_policy" "owner_data_access" {
  name   = "data-access"
  role   = aws_iam_role.owner.id
  policy = data.aws_iam_policy_document.data_access.json
}
