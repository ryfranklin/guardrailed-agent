# Bootstrap — GitHub OIDC

One-time setup. Provisions the GitHub OIDC identity provider and a CI role assumable by `repo:<owner>/<repo>:*`. Kept separate from `terraform/envs/demo/` because it has a different lifecycle: set once per AWS account, not per env.

## Apply

```bash
cd terraform/bootstrap/oidc
terraform init
terraform apply -var="github_owner=<your-gh-owner>"
```

Then copy the `ci_role_arn` output into `.github/workflows/*.yml` as the `role-to-assume` for `aws-actions/configure-aws-credentials`.

## Permissions

The CI role gets:

- Read-only AWS API access broad enough to run `terraform plan` against the `terraform/envs/demo/` composition.
- S3 + DynamoDB access scoped to the terraform state backend (or wildcard if `tfstate_bucket_arn` / `tfstate_lock_table_arn` are unset — only acceptable for early development).
- `bedrock:InvokeAgent` so the eval workflow can run against the deployed agent.
- `sts:AssumeRole` + `sts:TagSession` on the persona roles so the eval can run cases as Analyst / RegionalManager / Admin.
- `secretsmanager:GetSecretValue` on `gagent/*` secrets for Langfuse credential retrieval.

`terraform plan` from CI is read-only by design. CI does **not** get permission to `terraform apply` against the env — applies stay operator-driven. To allow CI applies later, add an `apply` role with broader permissions and gate it behind a workflow approval.
