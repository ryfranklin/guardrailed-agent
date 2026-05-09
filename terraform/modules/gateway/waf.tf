locals {
  waf_name = "${var.name_prefix}gateway-${var.env}"
}

resource "aws_wafv2_web_acl" "this" {
  name        = local.waf_name
  description = "Web ACL for the public-demo gateway HTTP API. Common + KnownBadInputs managed rules + per-IP rate limit (ADR-010 §5)."
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

resource "aws_wafv2_web_acl_association" "stage" {
  resource_arn = aws_apigatewayv2_stage.default.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}
