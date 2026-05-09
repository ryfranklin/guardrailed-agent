import { type ReactNode } from "react";

import {
  PERSONA_LABELS,
  type AskResponse,
  type PersonaRole,
} from "../api/types";

import { PersonaIndicator } from "./PersonaIndicator";

interface FlowViewProps {
  role: PersonaRole;
  serviceRegion: string | null;
  onChangePersona: () => void;
  response: AskResponse | null;
}

// Mirrors the ARCHITECTURE.md §1 mermaid diagram's box names so the
// in-app flow chart and the static doc tell the same story.
export function FlowView({
  role,
  serviceRegion,
  onChangePersona,
  response,
}: FlowViewProps) {
  const populated = response !== null;

  return (
    <section className="flex h-full flex-col" data-testid="flow-view">
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-2">
        <PersonaIndicator
          role={role}
          serviceRegion={serviceRegion}
          onChange={onChangePersona}
        />
        <span className="text-xs text-slate-400">
          How your last question travelled through the stack.
        </span>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-6">
        {!populated && (
          <div
            className="mx-auto mb-6 max-w-2xl rounded border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600"
            data-testid="flow-idle-hint"
          >
            <p>
              Submit a question on the <strong>Chat</strong> tab to see how the
              request travels through the stack — Cognito JWT validation, STS
              AssumeRole into the persona role, Bedrock Agent + Guardrails,
              the action group Lambda, Athena under Lake Formation governance,
              and CloudWatch Logs at the end.
            </p>
            <p className="mt-2 text-xs text-slate-500">
              The diagram below mirrors{" "}
              <span className="font-mono">ARCHITECTURE.md</span> §1 and lights
              up with the most recent turn&apos;s data once a question is asked.
            </p>
          </div>
        )}

        <div
          className={`mx-auto flex max-w-3xl flex-col items-stretch gap-2 transition-opacity duration-300 ${
            populated ? "opacity-100" : "opacity-50"
          }`}
        >
          <Node
            populated={populated}
            title="SPA"
            subtitle="Vite + React, served by CloudFront from demo.ms3dm.tech."
            badge={populated ? <span>POST /ask</span> : null}
            tone="default"
          />
          <Arrow />

          <Node
            populated={populated}
            title="API Gateway HTTP API"
            subtitle="JWT authorizer validates the Cognito ID token before the request reaches Lambda."
            badge={
              populated ? (
                <span>
                  Cognito JWT verified — caller is{" "}
                  <span className="font-medium">{PERSONA_LABELS[role]}</span>
                </span>
              ) : null
            }
            tone="default"
          />
          <Arrow />

          <Node
            populated={populated}
            title="Gateway Lambda"
            subtitle="Resolves persona from JWT claims via CognitoPersonaResolver, then invokes the agent through gagent_client."
            badge={
              populated && response ? (
                <span>
                  Resolved <code>{response.persona}</code>
                  {response.service_region
                    ? ` / ${response.service_region}`
                    : ""}
                </span>
              ) : null
            }
            tone="default"
          />
          <Arrow />

          <Node
            populated={populated}
            title="STS AssumeRole + session tags"
            subtitle="Tags role + service_region transitively so Lake Formation evaluates aws:PrincipalTag/role at access-check time."
            badge={
              populated && response ? (
                <span>
                  tags: role=<code>{response.persona}</code>
                  {response.service_region ? (
                    <>
                      , service_region=<code>{response.service_region}</code>
                    </>
                  ) : null}
                </span>
              ) : null
            }
            tone="default"
          />
          <Arrow />

          <Node
            populated={populated}
            title="Bedrock Agent"
            subtitle="Anthropic Claude Sonnet 4.6 — orchestrates tool calls + synthesizes the response."
            badge={
              populated && response ? (
                <span>
                  Sonnet 4.6 — total{" "}
                  <span className="font-medium">
                    {response.duration_seconds.toFixed(2)}s
                  </span>
                </span>
              ) : null
            }
            tone="default"
          />
          <Arrow />

          <Node
            populated={populated}
            title="Bedrock Guardrails"
            subtitle="ApplyGuardrail on prompt + response. Blocks PII leakage and off-topic content."
            badge={
              populated && response ? (
                response.guardrail_blocks > 0 ? (
                  <span className="text-amber-700">
                    {response.guardrail_blocks} block
                    {response.guardrail_blocks === 1 ? "" : "s"}
                  </span>
                ) : (
                  <span className="text-emerald-700">0 blocks</span>
                )
              ) : null
            }
            tone={
              populated && response && response.guardrail_blocks > 0
                ? "warning"
                : "default"
            }
          />
          <Arrow />

          <Node
            populated={populated}
            title="Action group Lambda (governed_query)"
            subtitle="Six SQL templates over the HVAC schema; assumes the persona role and runs Athena."
            badge={
              populated && response ? (
                response.tools_called.length > 0 ? (
                  <span>
                    {response.tools_called.length} tool call
                    {response.tools_called.length === 1 ? "" : "s"}:{" "}
                    <code>{response.tools_called.join(", ")}</code>
                  </span>
                ) : (
                  <span className="text-slate-500">
                    no tool calls — agent answered from context
                  </span>
                )
              ) : null
            }
            tone="default"
          />
          <Arrow />

          <Node
            populated={populated}
            title="Athena workgroup"
            subtitle="Runs the parameterized SELECT under the assumed persona's credentials."
            badge={null}
            tone="muted"
          />
          <Arrow />

          <Node
            populated={populated}
            title="Lake Formation"
            subtitle="LF-Tags + grants. Filters PII columns for Dispatcher; masks sensitivity-tagged columns for non-Owner."
            badge={null}
            tone="muted"
          />
          <Arrow />

          <Node
            populated={populated}
            title="S3 — Iceberg tables + Athena results"
            subtitle="Encrypted at rest (SSE-S3); only the persona role's session has read access."
            badge={null}
            tone="muted"
          />
          <Arrow />

          <Node
            populated={populated}
            title="CloudWatch Logs — /gagent/invocations"
            subtitle="One structured JSON line per turn, surface=web. The same log group serves CLI / MCP / eval / SMUS."
            badge={
              populated && response ? (
                <span className="font-mono text-xs">
                  session_id: {response.session_id}
                </span>
              ) : null
            }
            tone="default"
          />
        </div>

        {populated && response && (
          <div
            className="mx-auto mt-6 max-w-3xl rounded border border-slate-200 bg-white p-4 text-xs text-slate-600"
            data-testid="flow-footer"
          >
            <p className="font-medium text-slate-800">
              Verify in CloudWatch Logs Insights
            </p>
            <p className="mt-1">
              Open <code>/gagent/invocations</code> in CloudWatch Logs Insights
              and run:
            </p>
            <pre className="mt-2 overflow-x-auto rounded bg-slate-900 p-3 font-mono text-[11px] text-slate-100">{`fields @timestamp, persona, surface, tools_called, guardrail_blocks
| filter session_id = '${response.session_id}'
| sort @timestamp desc`}</pre>
          </div>
        )}
      </div>
    </section>
  );
}

interface NodeProps {
  populated: boolean;
  title: string;
  subtitle: string;
  badge: ReactNode | null;
  tone: "default" | "muted" | "warning";
}

function Node({ populated, title, subtitle, badge, tone }: NodeProps) {
  const dataState = populated ? "populated" : "idle";
  const toneClasses =
    tone === "warning"
      ? "border-amber-300 bg-amber-50"
      : tone === "muted"
        ? "border-slate-200 bg-slate-50"
        : "border-slate-200 bg-white";

  return (
    <div
      data-testid={`flow-node-${slugify(title)}`}
      data-state={dataState}
      className={`rounded border px-4 py-3 ${toneClasses}`}
    >
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
        {badge && (
          <span
            className="text-xs text-slate-700"
            data-testid={`flow-badge-${slugify(title)}`}
          >
            {badge}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>
    </div>
  );
}

function Arrow() {
  return (
    <div
      aria-hidden
      className="flex justify-center text-slate-400"
      data-testid="flow-arrow"
    >
      <span className="font-mono text-base leading-none">↓</span>
    </div>
  );
}

function slugify(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}
