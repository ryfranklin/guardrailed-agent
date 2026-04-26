terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  guardrail_name = "${var.name_prefix}${var.env}"

  pii_entities = ["EMAIL", "PHONE", "US_SOCIAL_SECURITY_NUMBER", "ADDRESS", "NAME"]

  content_filters = [
    { type = "PROMPT_ATTACK", input_strength = "HIGH", output_strength = "NONE" },
    { type = "SEXUAL", input_strength = "HIGH", output_strength = "HIGH" },
    { type = "HATE", input_strength = "HIGH", output_strength = "HIGH" },
    { type = "VIOLENCE", input_strength = "HIGH", output_strength = "HIGH" },
    { type = "INSULTS", input_strength = "HIGH", output_strength = "HIGH" },
    { type = "MISCONDUCT", input_strength = "HIGH", output_strength = "HIGH" },
  ]
}

resource "aws_bedrock_guardrail" "this" {
  name                      = local.guardrail_name
  description               = "Guardrail for the ${var.env} guardrailed-agent: PII anonymization, prompt-injection defense, denied off-scope topics, contextual grounding."
  blocked_input_messaging   = "I can't help with that. Please ask a question about ambassador performance, team health, orders, or signals."
  blocked_outputs_messaging = "I'm not able to share that response. Please rephrase or ask a different question about the ambassador dataset."

  content_policy_config {
    dynamic "filters_config" {
      for_each = local.content_filters
      content {
        type            = filters_config.value.type
        input_strength  = filters_config.value.input_strength
        output_strength = filters_config.value.output_strength
      }
    }
  }

  dynamic "sensitive_information_policy_config" {
    for_each = var.pii_action == "NONE" ? [] : [1]
    content {
      dynamic "pii_entities_config" {
        for_each = local.pii_entities
        content {
          action = var.pii_action
          type   = pii_entities_config.value
        }
      }
    }
  }

  topic_policy_config {
    dynamic "topics_config" {
      for_each = var.denied_topics
      content {
        name       = topics_config.value.name
        definition = topics_config.value.definition
        examples   = topics_config.value.examples
        type       = "DENY"
      }
    }
  }

  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = var.grounding_threshold
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = var.relevance_threshold
    }
  }

  tags = var.tags
}

resource "aws_bedrock_guardrail_version" "this" {
  guardrail_arn = aws_bedrock_guardrail.this.guardrail_arn
  description   = "Version for ${var.env} guardrail."

  depends_on = [aws_bedrock_guardrail.this]

  lifecycle {
    replace_triggered_by = [aws_bedrock_guardrail.this]
  }
}
