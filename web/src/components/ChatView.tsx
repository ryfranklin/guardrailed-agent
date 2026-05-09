import { useCallback, useState } from "react";

import { postAsk } from "../api/client";
import { ApiError, type PersonaRole } from "../api/types";
import { useSession } from "../state/session";

import {
  type ChatMessage,
  MessageList,
} from "./MessageList";
import { ComposerInput } from "./ComposerInput";
import { ErrorBanner } from "./ErrorBanner";
import { PersonaIndicator } from "./PersonaIndicator";

interface ChatViewProps {
  role: PersonaRole;
  serviceRegion: string | null;
  onChangePersona: () => void;
}

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
}: ChatViewProps) {
  const sessionId = useSession();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [pendingStartedAt, setPendingStartedAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

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
    [role, serviceRegion],
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
      {error && (
        <ErrorBanner message={error} onDismiss={() => setError(null)} />
      )}
      <MessageList
        messages={messages}
        pending={pending}
        pendingStartedAt={pendingStartedAt}
      />
      <ComposerInput disabled={pending} onSubmit={submit} />
    </section>
  );
}
