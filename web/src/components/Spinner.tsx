import { useEffect, useState } from "react";

interface SpinnerProps {
  startedAt: number;
}

export function Spinner({ startedAt }: SpinnerProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);

  const elapsedSec = Math.max(0, (now - startedAt) / 1000);
  const elapsedLabel = elapsedSec < 60
    ? `${elapsedSec.toFixed(1)}s`
    : `${Math.floor(elapsedSec / 60)}m ${Math.floor(elapsedSec % 60)}s`;

  // The gateway has a 30-second API Gateway integration timeout. Past that
  // the browser sees a 503 even though the agent may still be running in the
  // backend. Surface a hint so a user watching the timer knows what's about
  // to happen.
  const hint = elapsedSec < 20
    ? null
    : elapsedSec < 30
      ? "Multi-tool answers can run close to the 30s limit."
      : "Past the 30s gateway timeout — the agent may still finish in the backend, but the response can't be returned over this connection.";

  return (
    <div className="flex items-start gap-3" data-testid="thinking-spinner">
      <span
        aria-hidden
        className="mt-0.5 inline-block h-4 w-4 flex-shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-sky-500"
      />
      <div className="flex flex-col gap-0.5">
        <span className="font-medium text-slate-700">
          Thinking… <span className="font-mono text-xs text-slate-500">{elapsedLabel}</span>
        </span>
        {hint && (
          <span
            className={
              elapsedSec >= 30 ? "text-xs text-amber-600" : "text-xs text-slate-500"
            }
          >
            {hint}
          </span>
        )}
      </div>
    </div>
  );
}
