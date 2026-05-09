import { useState } from "react";

import {
  PERSONA_DESCRIPTIONS,
  PERSONA_LABELS,
  PERSONA_ROLES,
  type PersonaRole,
} from "../api/types";

interface PersonaModalProps {
  defaultRole?: PersonaRole | null;
  defaultServiceRegion?: string | null;
  onConfirm: (role: PersonaRole, serviceRegion: string | null) => void;
  onCancel?: () => void;
  cancellable?: boolean;
}

export function PersonaModal({
  defaultRole = null,
  defaultServiceRegion = null,
  onConfirm,
  onCancel,
  cancellable = false,
}: PersonaModalProps) {
  const [role, setRole] = useState<PersonaRole | null>(defaultRole);
  const [serviceRegion, setServiceRegion] = useState<string>(
    defaultServiceRegion ?? "tempe-mesa",
  );
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = () => {
    if (!role) {
      setError("Pick a persona to start chatting.");
      return;
    }
    if (role === "technician_lead" && !serviceRegion.trim()) {
      setError("Technician Lead requires a service region.");
      return;
    }
    setError(null);
    onConfirm(
      role,
      role === "technician_lead" ? serviceRegion.trim() : null,
    );
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="persona-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/60 px-4"
    >
      <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <h2
          id="persona-modal-title"
          className="text-xl font-semibold text-slate-900"
        >
          Pick a persona for this session
        </h2>
        <p className="mt-2 text-sm text-slate-600">
          Lake Formation enforces row + column visibility on every tool call
          using the persona you select. Switch personas any time from the
          header indicator.
        </p>

        <fieldset className="mt-5 space-y-3">
          <legend className="sr-only">Persona</legend>
          {PERSONA_ROLES.map((option) => (
            <label
              key={option}
              className={`flex cursor-pointer flex-col rounded border p-3 transition-colors ${
                role === option
                  ? "border-sky-500 bg-sky-50"
                  : "border-slate-200 hover:border-slate-300"
              }`}
            >
              <span className="flex items-center gap-2">
                <input
                  type="radio"
                  name="persona"
                  value={option}
                  checked={role === option}
                  onChange={() => setRole(option)}
                  data-testid={`persona-option-${option}`}
                />
                <span className="font-medium text-slate-900">
                  {PERSONA_LABELS[option]}
                </span>
              </span>
              <span className="ml-6 text-sm text-slate-600">
                {PERSONA_DESCRIPTIONS[option]}
              </span>
            </label>
          ))}
        </fieldset>

        {role === "technician_lead" && (
          <label className="mt-4 flex flex-col gap-1 text-sm text-slate-700">
            Service region
            <input
              type="text"
              className="rounded border border-slate-300 px-3 py-2"
              value={serviceRegion}
              onChange={(e) => setServiceRegion(e.target.value)}
              data-testid="service-region-input"
            />
          </label>
        )}

        {error && (
          <p
            className="mt-4 text-sm text-rose-600"
            data-testid="persona-modal-error"
          >
            {error}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-2">
          {cancellable && onCancel && (
            <button
              type="button"
              className="rounded border border-slate-300 px-4 py-2 text-slate-700 hover:bg-slate-100"
              onClick={onCancel}
              data-testid="persona-modal-cancel"
            >
              Cancel
            </button>
          )}
          <button
            type="button"
            className="rounded bg-sky-600 px-4 py-2 font-medium text-white hover:bg-sky-700 disabled:bg-slate-300"
            onClick={handleConfirm}
            data-testid="persona-modal-confirm"
          >
            Start chatting
          </button>
        </div>
      </div>
    </div>
  );
}
