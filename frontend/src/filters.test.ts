import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { clearFilters, usePersisted } from "./filters";

describe("saved filters", () => {
  it("keep a value across pages and forget it after sign-in or sign-out", () => {
    const first = renderHook(() => usePersisted("day.dateFrom", "2026-11-16"));
    act(() => first.result.current[1]("2026-11-15"));
    first.unmount();
    const again = renderHook(() => usePersisted("day.dateFrom", "2026-11-16"));
    expect(again.result.current[0]).toBe("2026-11-15");
    again.unmount();
    clearFilters();
    const fresh = renderHook(() => usePersisted("day.dateFrom", "2026-11-16"));
    expect(fresh.result.current[0]).toBe("2026-11-16");
  });
});
