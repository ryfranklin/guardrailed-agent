import { type ReactNode, useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Spinner } from "./Spinner";

export type MessageRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  toolsCalled?: string[];
  durationSeconds?: number;
  guardrailBlocks?: number;
}

interface MessageListProps {
  messages: ChatMessage[];
  pending: boolean;
  pendingStartedAt: number | null;
  emptyState?: ReactNode;
}

export function MessageList({
  messages,
  pending,
  pendingStartedAt,
  emptyState,
}: MessageListProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, pending]);

  return (
    <div
      className="flex-1 overflow-y-auto px-6 py-4"
      data-testid="message-list"
    >
      {messages.length === 0 && !pending && (
        emptyState ?? (
          <div className="flex h-full items-center justify-center text-slate-400">
            Ask the agent a question to get started.
          </div>
        )
      )}
      <ul className="space-y-3">
        {messages.map((m) => (
          <li
            key={m.id}
            className={`max-w-2xl rounded-lg border px-4 py-3 ${
              m.role === "user"
                ? "ml-auto border-sky-200 bg-sky-50"
                : "mr-auto border-slate-200 bg-white"
            }`}
          >
            {m.role === "assistant" ? (
              <div className="prose prose-sm max-w-none text-slate-900 prose-pre:bg-slate-900 prose-pre:text-slate-100 prose-code:text-rose-700 prose-code:before:content-none prose-code:after:content-none prose-headings:text-slate-900">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {m.content}
                </ReactMarkdown>
              </div>
            ) : (
              <div className="whitespace-pre-wrap text-sm text-slate-900">
                {m.content}
              </div>
            )}
            {m.role === "assistant" && (m.toolsCalled?.length ||
              typeof m.durationSeconds === "number") && (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
                {m.toolsCalled?.length ? (
                  <span>tools: {m.toolsCalled.join(", ")}</span>
                ) : null}
                {typeof m.durationSeconds === "number" && (
                  <span>{m.durationSeconds.toFixed(2)}s</span>
                )}
                {typeof m.guardrailBlocks === "number" &&
                  m.guardrailBlocks > 0 && (
                    <span className="text-amber-600">
                      guardrail: {m.guardrailBlocks}
                    </span>
                  )}
              </div>
            )}
          </li>
        ))}
        {pending && (
          <li
            className="mr-auto max-w-2xl rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm"
            data-testid="message-pending"
          >
            <Spinner startedAt={pendingStartedAt ?? Date.now()} />
          </li>
        )}
      </ul>
      <div ref={endRef} />
    </div>
  );
}
