// Static metadata for the data-preview view. Mirrors the action group's
// OpenAPI schema (terraform/modules/tools/main.tf) and the column tuples in
// lambdas/governed_query/handler.py. Updates here travel by hand; that's
// fine for an MVP — the dataset shape is stable.

export type TableId =
  | "customers"
  | "jobs"
  | "signals"
  | "equipment_telemetry"
  | "technician_utilization"
  | "truck_rolls";

export interface TableMeta {
  id: TableId;
  label: string;
  glueTable: string;
  apiPath: string;
  description: string;
  columns: string[];
  /** Subset of `columns` that Lake Formation redacts/masks per persona. */
  governedColumns: string[];
  /** Persona-specific governance summary, used by the DataView legend. */
  governanceNote: string;
}

export const TABLES: TableMeta[] = [
  {
    id: "customers",
    label: "Customers",
    glueTable: "customer",
    apiPath: "/customers",
    description:
      "SCD2 customer dimension. PII columns (email, phone, address, billing notes) are masked for Dispatcher; full for TechnicianLead and Owner.",
    columns: [
      "customer_id",
      "customer_type",
      "service_tier",
      "service_region",
      "first_name",
      "last_name",
      "email",
      "phone",
      "street_address",
      "city",
      "postal_code",
      "billing_notes",
    ],
    governedColumns: [
      "first_name",
      "last_name",
      "email",
      "phone",
      "street_address",
      "billing_notes",
    ],
    governanceNote:
      "PII columns (first_name, last_name, email, phone, street_address, billing_notes) are LF-Tagged pii=true → REDACTED for dispatcher.",
  },
  {
    id: "jobs",
    label: "Service jobs",
    glueTable: "service_job",
    apiPath: "/jobs",
    description:
      "Soft-delete fact. Defaults to deleted_at IS NULL. include_deleted=true is Owner-only and is enforced by the Lambda before the query reaches Athena.",
    columns: [
      "job_id",
      "customer_id",
      "technician_id",
      "equipment_id",
      "job_type",
      "scheduled_date",
      "completed_date",
      "status",
      "total_billed_usd",
      "billing_notes",
      "deleted_at",
      "deleted_by",
    ],
    governedColumns: ["billing_notes", "total_billed_usd"],
    governanceNote:
      "billing_notes is pii=true (REDACTED for dispatcher); total_billed_usd is sensitivity=high (Owner-only column).",
  },
  {
    id: "signals",
    label: "Customer signals",
    glueTable: "customer_signal_daily",
    apiPath: "/signals",
    description:
      "Daily customer signal rollup: engagement, churn risk, next-best-action. Joinable to customers by customer_id.",
    columns: [
      "signal_date",
      "customer_id",
      "engagement_score",
      "churn_risk",
      "next_best_action",
      "service_area_health",
    ],
    governedColumns: [],
    governanceNote:
      "No PII or sensitivity-tagged columns — all personas see identical rows on this table.",
  },
  {
    id: "equipment_telemetry",
    label: "Equipment telemetry",
    glueTable: "equipment_telemetry_daily",
    apiPath: "/equipment_telemetry",
    description:
      "Daily equipment telemetry with cycle_count, fault_code_count, efficiency_index, and a synthetic predicted_failure_30d score.",
    columns: [
      "equipment_id",
      "telemetry_date",
      "runtime_hours",
      "cycle_count",
      "fault_code_count",
      "efficiency_index",
      "predicted_failure_30d",
      "last_service_age_days",
    ],
    governedColumns: [],
    governanceNote:
      "No PII or sensitivity-tagged columns — all personas see identical rows on this table.",
  },
  {
    id: "technician_utilization",
    label: "Technician utilization",
    glueTable: "technician_utilization_daily",
    apiPath: "/technician_utilization",
    description:
      "Daily technician utilization. revenue_generated_usd and parts_consumed_cost_usd are sensitivity=high columns — Owner-only, masked for the others.",
    columns: [
      "technician_id",
      "utilization_date",
      "jobs_completed",
      "billable_hours",
      "revenue_generated_usd",
      "customer_satisfaction_avg",
      "parts_consumed_cost_usd",
      "idle_hours",
    ],
    governedColumns: ["revenue_generated_usd", "parts_consumed_cost_usd"],
    governanceNote:
      "revenue_generated_usd and parts_consumed_cost_usd are sensitivity=high — masked for dispatcher and technician_lead.",
  },
  {
    id: "truck_rolls",
    label: "Truck rolls",
    glueTable: "truck_roll",
    apiPath: "/truck_rolls",
    description:
      "Soft-delete fact joining service_job × technician × parts_inventory × equipment. Defaults to deleted_at IS NULL.",
    columns: [
      "truck_roll_id",
      "job_id",
      "technician_id",
      "equipment_id",
      "dispatch_ts",
      "return_ts",
      "miles_driven",
      "parts_pulled",
      "parts_returned",
      "outcome",
      "deleted_at",
      "deleted_by",
    ],
    governedColumns: [],
    governanceNote:
      "No PII or sensitivity-tagged columns — all personas see identical rows on this table.",
  },
];

export function findTable(id: TableId): TableMeta {
  const t = TABLES.find((x) => x.id === id);
  if (!t) throw new Error(`unknown table id: ${id}`);
  return t;
}
