import { useCallback, useEffect, useState } from "react";

import { postAsk } from "../api/client";
import {
  ApiError,
  type AskResponse,
  type PersonaRole,
} from "../api/types";
import { useSession } from "../state/session";

import {
  type ChatMessage,
  MessageList,
} from "./MessageList";
import { ComposerInput } from "./ComposerInput";
import { ErrorBanner } from "./ErrorBanner";
import { PersonaIndicator } from "./PersonaIndicator";
import { SamplePrompts } from "./SamplePrompts";
import { Spinner } from "./Spinner";

interface ChatViewProps {
  role: PersonaRole;
  serviceRegion: string | null;
  onChangePersona: () => void;
  onResponse?: (response: AskResponse) => void;
}

// A minimal prompt the agent can answer in one model turn with no tool
// calls. Warms the gateway Lambda, STS for the chosen persona, the Bedrock
// runtime client connection, and the agent's foundation-model invocation
// path so the user's first real query lands inside the API Gateway's 30s
// integration timeout.
const WARMUP_PROMPT =
  "Briefly describe your role in one sentence. Do not call any tools.";

let messageCounter = 0;
function nextMessageId(): string {
  messageCounter += 1;
  return `msg-${messageCounter}-${Date.now().toString(36)}`;
}

function formatApiError(err: ApiError): string {
  if (err.status === 504 || (err.status === 503 && !err.detail)) {
    return (
      "Request timed out (>30s). The agent is still running but API Gateway " +
      "gave up at its 30-second integration timeout. Streaming responses " +
      "land in Phase 3.5. Try a simpler question (e.g. 'Show me one " +
      "customer in service_region tempe-mesa') so the turn completes within " +
      "the window."
    );
  }
  if (err.status === 502 || err.status === 503) {
    return `Gateway error (${err.status}): ${err.detail || "no detail"}.`;
  }
  if (err.status === 401) {
    return "Session expired. Refresh the page to sign in again.";
  }
  if (err.status === 403) {
    return `Access denied: ${err.detail || "no detail"}.`;
  }
  if (err.status === 429) {
    return "Rate-limited by the gateway. Wait 30 seconds and try again.";
  }
  return `${err.code}: ${err.detail || "(no detail)"} [${err.status}]`;
}

export function ChatView({
  role,
  serviceRegion,
  onChangePersona,
  onResponse,
}: ChatViewProps) {
  const sessionId = useSession();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [pendingStartedAt, setPendingStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warmupState, setWarmupState] = useState<"warming" | "ready">(
    "warming",
  );
  const [warmupStartedAt, setWarmupStartedAt] = useState<number>(() =>
    Date.now(),
  );

  // Pre-warm the agent path so the first real query doesn't pay the cold-
  // start tax. Fires once per ChatView mount; ChatView remounts on persona
  // change via its `key` prop, so this runs again per persona switch.
  useEffect(() => {
    let cancelled = false;
    setWarmupState("warming");
    setWarmupStartedAt(Date.now());
    postAsk({
      question: WARMUP_PROMPT,
      persona: role,
      service_region: serviceRegion,
    })
      .catch(() => {
        // Warmup failures are silent — the user can still try real queries
        // (the cold start has been paid for at this point regardless).
      })
      .finally(() => {
        if (!cancelled) setWarmupState("ready");
      });
    return () => {
      cancelled = true;
    };
  }, [role, serviceRegion]);

  const submit = useCallback(
    async (text: string) => {
      const userMessage: ChatMessage = {
        id: nextMessageId(),
        role: "user",
        content: text,
      };
      setMessages((prev) => [...prev, userMessage]);
      setPending(true);
      setPendingStartedAt(Date.now());
      setError(null);
      try {
        const response = await postAsk({
          question: text,
          persona: role,
          service_region: serviceRegion,
        });
        const assistantMessage: ChatMessage = {
          id: nextMessageId(),
          role: "assistant",
          content: response.text,
          toolsCalled: response.tools_called,
          durationSeconds: response.duration_seconds,
          guardrailBlocks: response.guardrail_blocks,
        };
        setMessages((prev) => [...prev, assistantMessage]);
        onResponse?.(response);
      } catch (err) {
        if (err instanceof ApiError) {
          setError(formatApiError(err));
        } else if (err instanceof Error) {
          setError(`Couldn't reach the gateway: ${err.message}`);
        } else {
          setError("Unknown error.");
        }
      } finally {
        setPending(false);
        setPendingStartedAt(null);
      }
    },
    [role, serviceRegion, onResponse],
  );

  return (
    <section
      className="flex h-full flex-col"
      data-testid="chat-view"
      data-session-id={sessionId}
    >
      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-2">
        <PersonaIndicator
          role={role}
          serviceRegion={serviceRegion}
          onChange={onChangePersona}
        />
        <span className="text-xs text-slate-400">session {sessionId}</span>
      </div>
      {warmupState === "warming" && (
        <div
          className="border-b border-sky-200 bg-sky-50 px-6 py-3 text-sm text-sky-900"
          data-testid="warmup-banner"
        >
          <Spinner startedAt={warmupStartedAt} />
          <p className="mt-1 text-xs text-sky-800">
            Pre-warming the agent for{" "}
            <span className="font-medium">{role}</span> so your first query
            lands inside the 30s gateway window.
          </p>
        </div>
      )}
      {error && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}
      <MessageList
        messages={messages}
        pending={pending}
        pendingStartedAt={pendingStartedAt}
        emptyState={
          warmupState === "warming" ? null : (
            <SamplePrompts
              role={role}
              disabled={pending}
              onSubmit={submit}
            />
          )
        }
      />
      <ComposerInput
        disabled={pending || warmupState === "warming"}
        onSubmit={submit}
      />
    </section>
  );
}
