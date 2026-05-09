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
  account_id  = data.aws_caller_identity.current.account_id
  region      = data.aws_region.current.name
  bucket_name = "${var.name_prefix}web-${var.env}-${local.account_id}"
  oac_name    = "${var.name_prefix}web-${var.env}"
  origin_id   = "s3-${local.bucket_name}"
  cert_sans   = var.additional_domain_names
}

# ---- S3 origin bucket (private; CloudFront-only via OAC) ----

resource "aws_s3_bucket" "this" {
  bucket = local.bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---- CloudFront Origin Access Control ----

resource "aws_cloudfront_origin_access_control" "this" {
  name                              = local.oac_name
  description                       = "OAC for the public web demo distribution; signs all S3 origin requests with sigv4."
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# ---- ACM cert (must be in us-east-1 for CloudFront) ----

resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_name
  subject_alternative_names = local.cert_sans
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = local.region == "us-east-1"
      error_message = "web-demo module must be applied in us-east-1 because CloudFront only accepts ACM certs from that region. Add a provider alias if your env layer runs elsewhere."
    }
  }

  tags = var.tags
}

# Operator adds the validation records emitted by aws_acm_certificate at the
# IONOS DNS console. This resource polls ACM until validation succeeds.
resource "aws_acm_certificate_validation" "this" {
  certificate_arn = aws_acm_certificate.this.arn

  timeouts {
    create = var.acm_validation_timeout
  }
}

# ---- S3 bucket policy granting OAC GetObject ----

data "aws_iam_policy_document" "bucket_policy" {
  statement {
    sid    = "AllowCloudFrontOACReadOnly"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this.arn}/*"]

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.bucket_policy.json

  depends_on = [aws_s3_bucket_public_access_block.this]
}
