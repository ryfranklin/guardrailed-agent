import { useCallback, useEffect, useMemo, useState } from "react";

import { postPreview } from "../api/client";
import {
  PERSONA_LABELS,
  ApiError,
  type PersonaRole,
  type PreviewResponse,
} from "../api/types";
import { TABLES, type TableId, type TableMeta } from "../api/schema";

import { PersonaIndicator } from "./PersonaIndicator";
import { Spinner } from "./Spinner";

interface DataViewProps {
  role: PersonaRole;
  serviceRegion: string | null;
  onChangePersona: () => void;
}

interface CacheKey {
  table: TableId;
  role: PersonaRole;
  serviceRegion: string | null;
}

function cacheKey(k: CacheKey): string {
  return `${k.table}|${k.role}|${k.serviceRegion ?? ""}`;
}

export function DataView({
  role,
  serviceRegion,
  onChangePersona,
}: DataViewProps) {
  const [selectedId, setSelectedId] = useState<TableId>("customers");
  const [cache, setCache] = useState<Record<string, PreviewResponse>>({});
  const [loading, setLoading] = useState(false);
  const [loadingStartedAt, setLoadingStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedTable: TableMeta = useMemo(
    () => TABLES.find((t) => t.id === selectedId) ?? TABLES[0]!,
    [selectedId],
  );

  const currentKey = cacheKey({ table: selectedId, role, serviceRegion });
  const currentResponse = cache[currentKey];

  const fetchPreview = useCallback(async () => {
    const key = cacheKey({ table: selectedId, role, serviceRegion });
    if (cache[key]) return; // already loaded
    setLoading(true);
    setLoadingStartedAt(Date.now());
    setError(null);
    try {
      const response = await postPreview({
        table: selectedId,
        persona: role,
        service_region: serviceRegion,
        limit: 10,
      });
      setCache((prev) => ({ ...prev, [key]: response }));
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.code}: ${err.detail || "(no detail)"} [${err.status}]`);
      } else if (err instanceof Error) {
        setError(`Couldn't reach the gateway: ${err.message}`);
      } else {
        setError("Unknown error.");
      }
    } finally {
      setLoading(false);
      setLoadingStartedAt(null);
    }
  }, [selectedId, role, serviceRegion, cache]);

  // Refetch on persona / region / table change.
  useEffect(() => {
    void fetchPreview();
  }, [fetchPreview]);

  const rows = currentResponse?.rows ?? [];
  const columns = selectedTable.columns;

  return (
    <section className="flex h-full flex-col" data-testid="data-view">
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-2">
        <PersonaIndicator
          role={role}
          serviceRegion={serviceRegion}
          onChange={onChangePersona}
        />
        <span className="text-xs text-slate-400">
          Same SQL, different rows — Lake Formation enforces per persona.
        </span>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Table rail */}
        <aside className="w-64 flex-shrink-0 overflow-y-auto border-r border-slate-200 bg-slate-50">
          <ul className="divide-y divide-slate-200">
            {TABLES.map((t) => {
              const active = t.id === selectedId;
              return (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(t.id)}
                    className={`w-full px-4 py-3 text-left text-sm transition-colors ${
                      active
                        ? "bg-white font-medium text-slate-900"
                        : "text-slate-700 hover:bg-white"
                    }`}
                    data-testid={`data-table-${t.id}`}
                  >
                    <div className="flex items-center justify-between">
                      <span>{t.label}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                          t.governedColumns.length > 0
                            ? "bg-amber-100 text-amber-800"
                            : "bg-slate-200 text-slate-600"
                        }`}
                        title={
                          t.governedColumns.length > 0
                            ? "Has LF-governed columns"
                            : "No governed columns"
                        }
                      >
                        {t.governedColumns.length > 0
                          ? `${t.governedColumns.length} gov`
                          : "open"}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      {t.glueTable}
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        {/* Preview pane */}
        <div className="flex-1 overflow-hidden">
          <div className="flex h-full flex-col">
            <div className="border-b border-slate-200 bg-white px-6 py-3">
              <h2 className="text-base font-semibold text-slate-900">
                {selectedTable.label}{" "}
                <span className="font-mono text-xs font-normal text-slate-500">
                  ({selectedTable.glueTable})
                </span>
              </h2>
              <p className="mt-1 text-sm text-slate-600">
                {selectedTable.description}
              </p>
              <p className="mt-2 text-xs text-amber-700" data-testid="lf-note">
                <span className="font-semibold">LF governance:</span>{" "}
                {selectedTable.governanceNote}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                Showing first {currentResponse?.row_count ?? "—"} row
                {currentResponse?.row_count === 1 ? "" : "s"} as{" "}
                <span className="font-medium text-slate-700">
                  {PERSONA_LABELS[role]}
                </span>
                {serviceRegion ? ` (${serviceRegion})` : ""}.
              </p>
            </div>

            <div className="flex-1 overflow-auto px-6 py-4">
              {loading && (
                <div className="rounded border border-slate-200 bg-white p-4">
                  <Spinner startedAt={loadingStartedAt ?? Date.now()} />
                </div>
              )}
              {error && !loading && (
                <div
                  className="rounded border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800"
                  role="alert"
                >
                  {error}
                </div>
              )}
              {!loading && !error && rows.length === 0 && currentResponse && (
                <div className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-500">
                  Lake Formation returned 0 rows for this persona on this
                  table. Try switching personas in the header to see the same
                  query under different governance.
                </div>
              )}
              {rows.length > 0 && (
                <div className="overflow-x-auto rounded border border-slate-200 bg-white">
                  <table className="min-w-full divide-y divide-slate-200 text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        {columns.map((c) => {
                          const governed =
                            selectedTable.governedColumns.includes(c);
                          return (
                            <th
                              key={c}
                              scope="col"
                              className={`whitespace-nowrap px-3 py-2 text-left font-medium ${
                                governed
                                  ? "text-amber-800"
                                  : "text-slate-700"
                              }`}
                              title={
                                governed
                                  ? "LF-governed column — value depends on persona"
                                  : undefined
                              }
                            >
                              {c}
                              {governed && (
                                <span
                                  className="ml-1 text-[10px] uppercase text-amber-600"
                                  aria-hidden
                                >
                                  ●
                                </span>
                              )}
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {rows.map((r, i) => (
                        <tr key={i} className="hover:bg-slate-50">
                          {columns.map((c) => {
                            const value = r[c];
                            const isRedacted = value === "REDACTED";
                            const isNull = value === null || value === undefined;
                            return (
                              <td
                                key={c}
                                className={`whitespace-nowrap px-3 py-2 align-top font-mono text-xs ${
                                  isRedacted
                                    ? "bg-rose-50 text-rose-700"
                                    : isNull
                                      ? "text-slate-400"
                                      : "text-slate-900"
                                }`}
                              >
                                {isRedacted
                                  ? "REDACTED"
                                  : isNull
                                    ? "—"
                                    : String(value)}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
