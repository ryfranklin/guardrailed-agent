# Client Environment Template

Reference for instantiating a new client deployment. Copy this directory to `terraform/envs/client-<name>/`, point it at the client's AWS account (Topology B or C), and fill in `terraform.tfvars`.

The deployable Terraform module is account-agnostic — every client-specific value flows in via variables. No surgery inside `terraform/modules/`. See `docs/repo-bootstrap-brief.md` §3 and §15.

Stub. Phase 1 will populate `main.tf`, `variables.tf`, and `terraform.tfvars.example`.
