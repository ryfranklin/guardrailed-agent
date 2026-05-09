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

export function ChatView({
  role,
  serviceRegion,
  onChangePersona,
}: ChatViewProps) {
  const sessionId = useSession();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
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
          setError(`${err.code}: ${err.detail || "(no detail)"}`);
        } else if (err instanceof Error) {
          setError(err.message);
        } else {
          setError("Unknown error.");
        }
      } finally {
        setPending(false);
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
      <MessageList messages={messages} pending={pending} />
      <ComposerInput disabled={pending} onSubmit={submit} />
    </section>
  );
}
