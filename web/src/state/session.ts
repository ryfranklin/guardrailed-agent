import { useMemo } from "react";

const SESSION_STORAGE_KEY = "gagent.session_id";

function generateSessionId(): string {
  const random = Math.random().toString(36).slice(2, 10);
  const ts = Date.now().toString(36);
  return `web-${ts}-${random}`;
}

function readOrCreateSessionId(): string {
  if (typeof window === "undefined") return generateSessionId();
  const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
  if (existing) return existing;
  const fresh = generateSessionId();
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, fresh);
  return fresh;
}

export function useSession(): string {
  return useMemo(readOrCreateSessionId, []);
}

export const __SESSION_INTERNALS = {
  generateSessionId,
  SESSION_STORAGE_KEY,
};
