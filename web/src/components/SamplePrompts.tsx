import type { PersonaRole } from "../api/types";

interface SamplePrompt {
  text: string;
  hint: string;
}

const PROMPTS_BY_ROLE: Record<PersonaRole, SamplePrompt[]> = {
  dispatcher: [
    {
      text: "Show me a list of customers",
      hint: "Dispatcher is blocked — Lake Formation denies the PII columns",
    },
    {
      text: "What customers have the highest churn risk?",
      hint: "Uses the daily signals table",
    },
    {
      text: "List service jobs scheduled this week",
      hint: "Operational view; no revenue visibility",
    },
  ],
  technician_lead: [
    {
      text: "Show me customers in my service region",
      hint: "Full PII for your region only",
    },
    {
      text: "What jobs are scheduled in my region this week?",
      hint: "Row-filtered by service_region tag",
    },
    {
      text: "Which equipment is predicted to fail in the next 30 days?",
      hint: "Pulls from equipment telemetry",
    },
  ],
  owner: [
    {
      text: "Show me revenue by service region",
      hint: "Revenue column is owner-only",
    },
    {
      text: "Which technicians have the highest utilization?",
      hint: "Includes revenue_generated_usd",
    },
    {
      text: "List jobs including deleted ones",
      hint: "Soft-delete access (owner only)",
    },
  ],
};

interface SamplePromptsProps {
  role: PersonaRole;
  onSubmit: (prompt: string) => void;
  disabled?: boolean;
}

export function SamplePrompts({
  role,
  onSubmit,
  disabled = false,
}: SamplePromptsProps) {
  const prompts = PROMPTS_BY_ROLE[role];
  return (
    <div
      className="flex h-full flex-col items-center justify-center gap-4 px-6 py-8 text-slate-600"
      data-testid="sample-prompts"
    >
      <p className="text-sm text-slate-500">Try one of these to get started:</p>
      <ul className="flex w-full max-w-xl flex-col gap-2">
        {prompts.map((p) => (
          <li key={p.text}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onSubmit(p.text)}
              data-testid="sample-prompt"
              className="group w-full rounded-lg border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-sky-300 hover:bg-sky-50 focus:outline-none focus:ring-2 focus:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <div className="text-sm font-medium text-slate-900 group-hover:text-sky-900">
                {p.text}
              </div>
              <div className="mt-0.5 text-xs text-slate-500">{p.hint}</div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
