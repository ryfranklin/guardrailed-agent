import { PERSONA_LABELS, type PersonaRole } from "../api/types";

interface PersonaIndicatorProps {
  role: PersonaRole;
  serviceRegion: string | null;
  onChange: () => void;
}

export function PersonaIndicator({
  role,
  serviceRegion,
  onChange,
}: PersonaIndicatorProps) {
  return (
    <div className="flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-1 text-sm text-slate-700">
      <span className="text-slate-500">Acting as</span>
      <span className="font-medium text-slate-900" data-testid="persona-indicator-role">
        {PERSONA_LABELS[role]}
      </span>
      {serviceRegion && (
        <span
          className="text-slate-500"
          data-testid="persona-indicator-region"
        >
          ({serviceRegion})
        </span>
      )}
      <button
        type="button"
        onClick={onChange}
        className="text-sky-600 underline hover:text-sky-700"
        data-testid="persona-indicator-change"
      >
        change
      </button>
    </div>
  );
}
