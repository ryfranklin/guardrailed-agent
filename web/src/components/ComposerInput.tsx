import { useState, type KeyboardEvent } from "react";

interface ComposerInputProps {
  disabled: boolean;
  onSubmit: (text: string) => void;
}

export function ComposerInput({ disabled, onSubmit }: ComposerInputProps) {
  const [value, setValue] = useState("");

  const send = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setValue("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  return (
    <form
      className="flex gap-2 border-t border-slate-200 bg-white p-4"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <textarea
        className="flex-1 resize-none rounded border border-slate-300 px-3 py-2 text-sm focus:border-sky-500 focus:outline-none"
        rows={2}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask the agent a question…"
        disabled={disabled}
        data-testid="composer-input"
      />
      <button
        type="submit"
        className="rounded bg-sky-600 px-4 py-2 text-sm font-medium text-white hover:bg-sky-700 disabled:bg-slate-300"
        disabled={disabled || value.trim().length === 0}
        data-testid="composer-submit"
      >
        Send
      </button>
    </form>
  );
}
