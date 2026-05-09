import { fetchAuthSession } from "@aws-amplify/auth";

import {
  ApiError,
  type AskRequest,
  type AskResponse,
  type PreviewRequest,
  type PreviewResponse,
} from "./types";

async function postJson<TBody, TResponse>(
  path: string,
  body: TBody,
): Promise<TResponse> {
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

  const res = await fetch(`${endpoint}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    let errBody: { error?: string; message?: string } = {};
    try {
      errBody = (await res.json()) as { error?: string; message?: string };
    } catch {
      // non-JSON error body
    }
    throw new ApiError(
      res.status,
      errBody.error ?? "unknown",
      errBody.message ?? "",
    );
  }

  return (await res.json()) as TResponse;
}

export async function postAsk(input: AskRequest): Promise<AskResponse> {
  return postJson<AskRequest, AskResponse>("/ask", input);
}

export async function postPreview(
  input: PreviewRequest,
): Promise<PreviewResponse> {
  return postJson<PreviewRequest, PreviewResponse>("/preview", input);
}
