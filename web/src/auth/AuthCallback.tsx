import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { fetchAuthSession } from "@aws-amplify/auth";

export function AuthCallback() {
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // Amplify's signInWithRedirect plugin processes the OAuth code from
        // the URL on its first auth call. fetchAuthSession completes the
        // dance and resolves once tokens are stored.
        await fetchAuthSession();
      } catch (err) {
        console.error("auth callback failed", err);
      } finally {
        if (!cancelled) navigate("/", { replace: true });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  return (
    <div className="flex h-full items-center justify-center text-slate-500">
      Completing sign-in…
    </div>
  );
}
