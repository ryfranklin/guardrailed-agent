output "dispatcher_role_arn" {
  description = "ARN of the Dispatcher persona role (front-desk view, ADR-008)."
  value       = aws_iam_role.dispatcher.arn
}

output "dispatcher_role_name" {
  description = "Name of the Dispatcher persona role."
  value       = aws_iam_role.dispatcher.name
}

output "technician_lead_role_arn" {
  description = "ARN of the TechnicianLead persona role (ADR-008)."
  value       = aws_iam_role.technician_lead.arn
}

output "technician_lead_role_name" {
  description = "Name of the TechnicianLead persona role."
  value       = aws_iam_role.technician_lead.name
}

output "owner_role_arn" {
  description = "ARN of the Owner persona role (unrestricted, ADR-008)."
  value       = aws_iam_role.owner.arn
}

output "owner_role_name" {
  description = "Name of the Owner persona role."
  value       = aws_iam_role.owner.name
}

output "all_persona_role_arns" {
  description = "All three persona role ARNs in a list — convenient for Lake Formation grants and Lambda IAM."
  value = [
    aws_iam_role.dispatcher.arn,
    aws_iam_role.technician_lead.arn,
    aws_iam_role.owner.arn,
  ]
}
