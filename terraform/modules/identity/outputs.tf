output "analyst_role_arn" {
  description = "ARN of the Analyst persona role."
  value       = aws_iam_role.analyst.arn
}

output "analyst_role_name" {
  description = "Name of the Analyst persona role."
  value       = aws_iam_role.analyst.name
}

output "regional_manager_role_arn" {
  description = "ARN of the RegionalManager persona role."
  value       = aws_iam_role.regional_manager.arn
}

output "regional_manager_role_name" {
  description = "Name of the RegionalManager persona role."
  value       = aws_iam_role.regional_manager.name
}

output "admin_role_arn" {
  description = "ARN of the Admin persona role."
  value       = aws_iam_role.admin.arn
}

output "admin_role_name" {
  description = "Name of the Admin persona role."
  value       = aws_iam_role.admin.name
}

output "all_persona_role_arns" {
  description = "All three persona role ARNs in a list — convenient for Lake Formation grants and Lambda IAM."
  value = [
    aws_iam_role.analyst.arn,
    aws_iam_role.regional_manager.arn,
    aws_iam_role.admin.arn,
  ]
}
