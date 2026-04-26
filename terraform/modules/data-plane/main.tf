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

locals {
  bucket_suffix = "${var.env}-${data.aws_caller_identity.current.account_id}"
  data_bucket   = "${var.s3_bucket_prefix}data-${local.bucket_suffix}"
  athena_bucket = "${var.s3_bucket_prefix}athena-${local.bucket_suffix}"
  workgroup     = "${var.s3_bucket_prefix}${var.env}"
}

resource "aws_s3_bucket" "data" {
  bucket = local.data_bucket
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket" "athena_results" {
  bucket = local.athena_bucket
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "athena_results" {
  bucket                  = aws_s3_bucket.athena_results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id
  rule {
    id     = "expire-old-results"
    status = "Enabled"
    filter {}
    expiration {
      days = 30
    }
  }
}

resource "aws_glue_catalog_database" "this" {
  name         = var.glue_database_name
  description  = "Iceberg-backed governed dataset for the Guardrailed Agent (${var.env})."
  location_uri = "s3://${aws_s3_bucket.data.bucket}/${var.glue_database_name}/"
}

resource "aws_athena_workgroup" "this" {
  name          = local.workgroup
  state         = "ENABLED"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    requester_pays_enabled             = false

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.bucket}/results/"
      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }

    engine_version {
      selected_engine_version = "AUTO"
    }
  }

  tags = var.tags
}

resource "aws_lakeformation_resource" "data" {
  arn                     = aws_s3_bucket.data.arn
  use_service_linked_role = true
}

resource "aws_lakeformation_data_lake_settings" "this" {
  admins                  = var.lf_admin_principal_arns
  trusted_resource_owners = [data.aws_caller_identity.current.account_id]

  create_database_default_permissions {
    permissions = []
    principal   = "IAM_ALLOWED_PRINCIPALS"
  }

  create_table_default_permissions {
    permissions = []
    principal   = "IAM_ALLOWED_PRINCIPALS"
  }
}

resource "aws_lakeformation_lf_tag" "pii" {
  key        = "pii"
  values     = ["true", "false"]
  depends_on = [aws_lakeformation_data_lake_settings.this]
}

resource "aws_lakeformation_resource_lf_tag" "database" {
  database {
    name = aws_glue_catalog_database.this.name
  }
  lf_tag {
    key   = aws_lakeformation_lf_tag.pii.key
    value = "false"
  }
}

resource "aws_lakeformation_permissions" "admin_database" {
  principal = var.admin_role_arn

  database {
    name = aws_glue_catalog_database.this.name
  }

  permissions                   = ["ALL"]
  permissions_with_grant_option = []

  depends_on = [aws_lakeformation_data_lake_settings.this]
}

resource "aws_lakeformation_permissions" "admin_lf_tag_pii" {
  principal = var.admin_role_arn

  lf_tag_policy {
    resource_type = "TABLE"

    expression {
      key    = aws_lakeformation_lf_tag.pii.key
      values = ["true", "false"]
    }
  }

  permissions                   = ["SELECT", "DESCRIBE"]
  permissions_with_grant_option = []

  depends_on = [aws_lakeformation_lf_tag.pii]
}

resource "aws_lakeformation_permissions" "analyst_lf_tag_non_pii" {
  principal = var.analyst_role_arn

  lf_tag_policy {
    resource_type = "TABLE"

    expression {
      key    = aws_lakeformation_lf_tag.pii.key
      values = ["false"]
    }
  }

  permissions                   = ["SELECT", "DESCRIBE"]
  permissions_with_grant_option = []

  depends_on = [aws_lakeformation_lf_tag.pii]
}

resource "aws_lakeformation_permissions" "regional_manager_lf_tag_all" {
  principal = var.regional_manager_role_arn

  lf_tag_policy {
    resource_type = "TABLE"

    expression {
      key    = aws_lakeformation_lf_tag.pii.key
      values = ["true", "false"]
    }
  }

  permissions                   = ["SELECT", "DESCRIBE"]
  permissions_with_grant_option = []

  depends_on = [aws_lakeformation_lf_tag.pii]
}

resource "aws_lakeformation_permissions" "analyst_database_describe" {
  principal = var.analyst_role_arn

  database {
    name = aws_glue_catalog_database.this.name
  }

  permissions                   = ["DESCRIBE"]
  permissions_with_grant_option = []

  depends_on = [aws_lakeformation_data_lake_settings.this]
}

resource "aws_lakeformation_permissions" "regional_manager_database_describe" {
  principal = var.regional_manager_role_arn

  database {
    name = aws_glue_catalog_database.this.name
  }

  permissions                   = ["DESCRIBE"]
  permissions_with_grant_option = []

  depends_on = [aws_lakeformation_data_lake_settings.this]
}
