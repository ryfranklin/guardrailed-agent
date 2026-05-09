import { fetchAuthSession } from "@aws-amplify/auth";

import { ApiError, type AskRequest, type AskResponse } from "./types";

export async function postAsk(input: AskRequest): Promise<AskResponse> {
  const session = await fetchAuthSession();
  const token = session.tokens?.idToken?.toString();
  if (!token) throw new ApiError(401, "unauthenticated", "No active session.");

  const endpoint = import.meta.env.VITE_API_ENDPOINT;
  if (!endpoint)
    throw new ApiError(
      500,
      "misconfigured",
      "VITE_API_ENDPOINT is not set at build time.",
    );

  const res = await fetch(`${endpoint}/ask`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(input),
  });

  if (!res.ok) {
    let body: { error?: string; message?: string } = {};
    try {
      body = (await res.json()) as { error?: string; message?: string };
    } catch {
      // Some error paths return non-JSON; fall through with empty body.
    }
    throw new ApiError(
      res.status,
      body.error ?? "unknown",
      body.message ?? "",
    );
  }

  return (await res.json()) as AskResponse;
}
