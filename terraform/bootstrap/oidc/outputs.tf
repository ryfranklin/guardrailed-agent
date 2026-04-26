output "ci_role_arn" {
  description = "ARN of the CI role. Use as the role-to-assume in GitHub Actions workflows."
  value       = aws_iam_role.ci.arn
}

output "github_oidc_provider_arn" {
  description = "ARN of the GitHub OIDC IdP."
  value       = aws_iam_openid_connect_provider.github.arn
}
