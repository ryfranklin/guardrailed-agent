locals {
  waf_name = "${var.name_prefix}gateway-${var.env}"
}

resource "aws_wafv2_web_acl" "this" {
  name        = local.waf_name
  description = "Web ACL for the public-demo gateway HTTP API. Common + KnownBadInputs managed rules + per-IP rate limit per ADR-010."
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "AWS-AWSManagedRulesCommonRuleSet"
    priority = 10

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.waf_name}-common"
    }
  }

  rule {
    name     = "AWS-AWSManagedRulesKnownBadInputsRuleSet"
    priority = 20

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.waf_name}-bad-inputs"
    }
  }

  rule {
    name     = "RateLimitPerIp"
    priority = 30

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      sampled_requests_enabled   = true
      cloudwatch_metrics_enabled = true
      metric_name                = "${local.waf_name}-rate"
    }
  }

  visibility_config {
    sampled_requests_enabled   = true
    cloudwatch_metrics_enabled = true
    metric_name                = local.waf_name
  }

  tags = var.tags
}

# aws_wafv2_web_acl_association deferred to Phase 3.5.
#
# AWS WAFv2 supports REST API (v1) stages, ALB, AppSync, CloudFront, Cognito,
# AppRunner, and Amplify — but NOT API Gateway HTTP API (v2) stages. The
# AssociateWebACL call returns WAFInvalidParameterException for HTTP API
# stage ARNs. The brief's §10 spec assumed support; it doesn't exist.
#
# The WAF ACL above is created with the three rules per ADR-010 §5 and is
# observable via CloudWatch metrics, but it is NOT enforcing on the HTTP API
# until one of these unblocks the association in 3.5:
#   1. AWS adds HTTP API v2 to the WAFv2 supported-resource set.
#   2. Migrate the gateway from HTTP API → REST API.
#   3. Front the HTTP API with CloudFront and attach WAF to that.
#
# resource "aws_wafv2_web_acl_association" "stage" {
#   resource_arn = aws_apigatewayv2_stage.default.arn
#   web_acl_arn  = aws_wafv2_web_acl.this.arn
# }
