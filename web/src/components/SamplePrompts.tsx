import type { PersonaRole } from "../api/types";

interface SamplePrompt {
  text: string;
  hint: string;
}

const PROMPTS_BY_ROLE: Record<PersonaRole, SamplePrompt[]> = {
  dispatcher: [
    {
      text: "Show me a list of customers",
      hint: "Lake Formation strips PII columns (first_name, email, phone, etc.); operational fields survive",
    },
    {
      text: "Show me open jobs scheduled for today",
      hint: "All job fields except billing_notes (PII) — Lambda retries with SELECT * so LF column-filters transparently",
    },
    {
      text: "What customers have the highest churn risk?",
      hint: "customer_signal_daily — engagement_score, churn_risk, next_best_action are all non-PII",
    },
  ],
  technician_lead: [
    {
      text: "Show me customers in my service region",
      hint: "Full PII, but rows are filtered by your ABAC service_region tag",
    },
    {
      text: "What jobs are scheduled in my region this week?",
      hint: "Same ABAC row filter — no service_region WHERE clause needed",
    },
    {
      text: "Which equipment is predicted to fail in the next 30 days?",
      hint: "Reads predicted_failure_30d from equipment_telemetry_daily",
    },
  ],
  owner: [
    {
      text: "Show me revenue by service region",
      hint: "revenue_generated_usd is tagged sensitivity=high — owner-only",
    },
    {
      text: "Which technicians have the highest utilization?",
      hint: "Joins technician_utilization_daily; revenue columns visible to owner only",
    },
    {
      text: "List jobs including deleted ones",
      hint: "include_deleted is enforced as an owner-only parameter in the Lambda",
    },
  ],
};

type SamplePromptsLayout = "sidebar" | "centered";

interface SamplePromptsProps {
  role: PersonaRole;
  onSubmit: (prompt: string) => void;
  disabled?: boolean;
  layout?: SamplePromptsLayout;
}

export function SamplePrompts({
  role,
  onSubmit,
  disabled = false,
  layout = "sidebar",
}: SamplePromptsProps) {
  const prompts = PROMPTS_BY_ROLE[role];
  const isSidebar = layout === "sidebar";
  const wrapperClass = isSidebar
    ? "flex h-full flex-col gap-3 overflow-y-auto px-4 py-4 text-slate-600"
    : "flex h-full flex-col items-center justify-center gap-4 px-6 py-8 text-slate-600";
  const listClass = isSidebar
    ? "flex flex-col gap-2"
    : "flex w-full max-w-xl flex-col gap-2";
  return (
    <div className={wrapperClass} data-testid="sample-prompts">
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
        Try a query
      </p>
      <ul className={listClass}>
        {prompts.map((p) => (
          <li key={p.text}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onSubmit(p.text)}
              data-testid="sample-prompt"
              className="group w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-left transition hover:border-sky-300 hover:bg-sky-50 focus:outline-none focus:ring-2 focus:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <div className="text-sm font-medium leading-snug text-slate-900 group-hover:text-sky-900">
                {p.text}
              </div>
              <div className="mt-1 text-xs leading-snug text-slate-500">
                {p.hint}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
