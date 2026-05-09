import { useEffect, useState, type ReactNode } from "react";
import {
  fetchAuthSession,
  signInWithRedirect,
  signOut,
} from "@aws-amplify/auth";

interface AuthGateProps {
  children: ReactNode;
}

type AuthStatus = "checking" | "authenticated" | "redirecting";

export function AuthGate({ children }: AuthGateProps) {
  const [status, setStatus] = useState<AuthStatus>("checking");
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await fetchAuthSession();
        const token = session.tokens?.idToken;
        if (token) {
          if (cancelled) return;
          const payload = token.payload;
          const claimEmail = typeof payload.email === "string"
            ? payload.email
            : null;
          setEmail(claimEmail);
          setStatus("authenticated");
          return;
        }
      } catch {
        // No active session — redirect to Hosted UI below.
      }
      if (cancelled) return;
      setStatus("redirecting");
      try {
        await signInWithRedirect();
      } catch (err) {
        console.error("signInWithRedirect failed", err);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (status !== "authenticated") {
    return (
      <div className="flex h-full items-center justify-center text-slate-500">
        {status === "checking" ? "Checking session…" : "Redirecting to sign in…"}
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
        <div className="text-lg font-semibold text-slate-900">
          Guardrailed Agent — public demo
        </div>
        <div className="flex items-center gap-4 text-sm text-slate-600">
          {email && <span data-testid="auth-email">{email}</span>}
          <button
            type="button"
            className="rounded border border-slate-300 px-3 py-1 hover:bg-slate-100"
            onClick={() => {
              void signOut();
            }}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="flex-1 overflow-hidden">{children}</main>
    </div>
  );
}
