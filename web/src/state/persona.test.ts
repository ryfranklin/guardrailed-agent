import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { usePersona } from "./persona";

describe("usePersona", () => {
  it("starts unready with no role", () => {
    const { result } = renderHook(() => usePersona());
    expect(result.current.isReady).toBe(false);
    expect(result.current.role).toBeNull();
    expect(result.current.serviceRegion).toBeNull();
  });

  it("setPersona stores role + region for technician_lead", () => {
    const { result } = renderHook(() => usePersona());
    act(() => {
      result.current.setPersona("technician_lead", "tempe-mesa");
    });
    expect(result.current.role).toBe("technician_lead");
    expect(result.current.serviceRegion).toBe("tempe-mesa");
    expect(result.current.isReady).toBe(true);
  });

  it("setPersona drops region for non-tech-lead personas", () => {
    const { result } = renderHook(() => usePersona());
    act(() => {
      result.current.setPersona("dispatcher", "tempe-mesa");
    });
    expect(result.current.role).toBe("dispatcher");
    expect(result.current.serviceRegion).toBeNull();
  });

  it("clearPersona resets state", () => {
    const { result } = renderHook(() => usePersona());
    act(() => {
      result.current.setPersona("owner", null);
    });
    act(() => {
      result.current.clearPersona();
    });
    expect(result.current.role).toBeNull();
    expect(result.current.isReady).toBe(false);
  });
});
