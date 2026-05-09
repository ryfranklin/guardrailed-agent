import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { __SESSION_INTERNALS, useSession } from "./session";

const { SESSION_STORAGE_KEY } = __SESSION_INTERNALS;

describe("useSession", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  afterEach(() => {
    window.sessionStorage.clear();
  });

  it("generates a session id on first call and persists it", () => {
    const { result } = renderHook(() => useSession());
    const stored = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    expect(stored).toBe(result.current);
    expect(result.current.startsWith("web-")).toBe(true);
  });

  it("returns the same id across re-renders", () => {
    const { result, rerender } = renderHook(() => useSession());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});
