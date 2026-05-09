import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@aws-amplify/auth", () => ({
  fetchAuthSession: vi.fn(),
}));

import { fetchAuthSession } from "@aws-amplify/auth";

import { postAsk } from "./client";
import { ApiError } from "./types";

const mockedFetchAuthSession = vi.mocked(fetchAuthSession);

function fakeSession(token: string | null) {
  if (token === null) {
    return { tokens: undefined } as unknown as ReturnType<
      typeof fetchAuthSession
    > extends Promise<infer R>
      ? R
      : never;
  }
  return {
    tokens: {
      idToken: { toString: () => token },
    },
  } as unknown as ReturnType<typeof fetchAuthSession> extends Promise<infer R>
    ? R
    : never;
}

const ENDPOINT = "https://api.example.test";

describe("postAsk", () => {
  const originalFetch = globalThis.fetch;
  const fetchSpy = vi.fn();

  beforeEach(() => {
    vi.stubEnv("VITE_API_ENDPOINT", ENDPOINT);
    globalThis.fetch = fetchSpy as unknown as typeof globalThis.fetch;
    fetchSpy.mockReset();
    mockedFetchAuthSession.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    globalThis.fetch = originalFetch;
  });

  it("throws ApiError(401) when there is no id token", async () => {
    mockedFetchAuthSession.mockResolvedValue(fakeSession(null));

    await expect(
      postAsk({ question: "hi", persona: "owner" }),
    ).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      code: "unauthenticated",
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("posts to <endpoint>/ask with bearer token and JSON body", async () => {
    mockedFetchAuthSession.mockResolvedValue(fakeSession("jwt-token"));
    fetchSpy.mockResolvedValue(
      new Response(
        JSON.stringify({
          text: "ok",
          persona: "owner",
          service_region: null,
          tools_called: ["/customers"],
          guardrail_blocks: 0,
          duration_seconds: 1.5,
          session_id: "gw-owner-abc",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const result = await postAsk({
      question: "hi",
      persona: "owner",
      service_region: null,
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${ENDPOINT}/ask`);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer jwt-token");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      question: "hi",
      persona: "owner",
      service_region: null,
    });
    expect(result.text).toBe("ok");
    expect(result.tools_called).toEqual(["/customers"]);
  });

  it("maps a JSON error response to ApiError with code+detail", async () => {
    mockedFetchAuthSession.mockResolvedValue(fakeSession("jwt-token"));
    fetchSpy.mockResolvedValue(
      new Response(
        JSON.stringify({ error: "throttled", message: "slow down" }),
        { status: 429, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(
      postAsk({ question: "hi", persona: "owner" }),
    ).rejects.toMatchObject({
      status: 429,
      code: "throttled",
      detail: "slow down",
    });
  });

  it("falls through with empty fields when error body is not JSON", async () => {
    mockedFetchAuthSession.mockResolvedValue(fakeSession("jwt-token"));
    fetchSpy.mockResolvedValue(
      new Response("oops", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      }),
    );

    await expect(
      postAsk({ question: "hi", persona: "owner" }),
    ).rejects.toMatchObject({
      status: 500,
      code: "unknown",
      detail: "",
    });
  });

  it("throws ApiError(500 misconfigured) when VITE_API_ENDPOINT is missing", async () => {
    vi.stubEnv("VITE_API_ENDPOINT", "");
    mockedFetchAuthSession.mockResolvedValue(fakeSession("jwt-token"));

    await expect(
      postAsk({ question: "hi", persona: "owner" }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
