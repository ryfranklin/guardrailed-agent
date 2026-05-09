terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

data "aws_region" "current" {}

locals {
  pool_name          = "${var.name_prefix}${var.env}"
  client_name        = "${var.name_prefix}${var.env}-web"
  hosted_domain      = coalesce(var.hosted_ui_domain_prefix, local.pool_name)
  google_provider    = "Google"
  github_provider    = "GitHub"
  slack_provider     = "Slack"
  cognito_provider   = "COGNITO"
  identity_providers = [local.cognito_provider, local.google_provider, local.slack_provider]
}

resource "aws_cognito_user_pool" "this" {
  name = local.pool_name

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  mfa_configuration = "OPTIONAL"

  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  schema {
    name                     = "persona"
    attribute_data_type      = "String"
    developer_only_attribute = false
    mutable                  = true
    required                 = false

    string_attribute_constraints {
      min_length = 1
      max_length = 64
    }
  }

  admin_create_user_config {
    allow_admin_create_user_only = false
  }

  tags = var.tags
}

resource "aws_cognito_user_pool_domain" "this" {
  domain       = local.hosted_domain
  user_pool_id = aws_cognito_user_pool.this.id
}

resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.this.id
  provider_name = local.google_provider
  provider_type = "Google"

  provider_details = {
    client_id        = var.google_client_id
    client_secret    = var.google_client_secret
    authorize_scopes = "openid email profile"
  }

  attribute_mapping = {
    email    = "email"
    username = "sub"
    name     = "name"
  }
}

# GitHub-as-OIDC deferred to Phase 3.5. Cognito's generic-OIDC provider rejects
# the username attribute mapping with "id cannot be mapped to username" because
# GitHub does not expose a `sub` claim — the only field Cognito will accept as
# the username source. The brief (§7.4) explicitly allows simplifying to
# Google + email/password for v1 launch when GitHub-as-OIDC is brittle. The
# github_client_id / github_client_secret variables remain declared so adding
# the resource back in Phase 3.5 is a code-only change.
#
# resource "aws_cognito_identity_provider" "github" { ... }

resource "aws_cognito_identity_provider" "slack" {
  user_pool_id  = aws_cognito_user_pool.this.id
  provider_name = local.slack_provider
  provider_type = "OIDC"

  provider_details = {
    client_id                     = var.slack_client_id
    client_secret                 = var.slack_client_secret
    authorize_scopes              = "openid email profile"
    attributes_request_method     = "GET"
    oidc_issuer                   = "https://slack.com"
    authorize_url                 = "https://slack.com/openid/connect/authorize"
    token_url                     = "https://slack.com/api/openid.connect.token"
    attributes_url                = "https://slack.com/api/openid.connect.userInfo"
    jwks_uri                      = "https://slack.com/openid/connect/keys"
    attributes_url_add_attributes = "false"
  }

  attribute_mapping = {
    email    = "email"
    username = "sub"
    name     = "name"
  }
}

resource "aws_cognito_user_pool_client" "web" {
  name         = local.client_name
  user_pool_id = aws_cognito_user_pool.this.id

  generate_secret = false

  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["email", "openid", "profile"]
  allowed_oauth_flows_user_pool_client = true

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  supported_identity_providers = local.identity_providers

  prevent_user_existence_errors = "ENABLED"

  explicit_auth_flows = [
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  id_token_validity      = var.id_token_validity_minutes
  access_token_validity  = var.access_token_validity_minutes
  refresh_token_validity = var.refresh_token_validity_days

  token_validity_units {
    id_token      = "minutes"
    access_token  = "minutes"
    refresh_token = "days"
  }

  enable_token_revocation = true

  depends_on = [
    aws_cognito_identity_provider.google,
    aws_cognito_identity_provider.slack,
  ]
}
