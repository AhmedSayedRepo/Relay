import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LIVE_REFRESH_MS, useApi } from "./hooks";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("live lists", () => {
  it("refresh themselves every 30 seconds without a spinner", async () => {
    let count = 0;
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ n: ++count }), { status: 200 })));
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderHook(() => useApi<{ n: number }>("/dashboard", "t", true));
    await waitFor(() => expect(result.current.data).toEqual({ n: 1 }));
    await act(async () => { vi.advanceTimersByTime(LIVE_REFRESH_MS); });
    await waitFor(() => expect(result.current.data).toEqual({ n: 2 }));
    expect(result.current.loading).toBe(false);
  });

  it("other pages load once (a refresh never resets a form)", async () => {
    const fetcher = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderHook(() => useApi("/transactions/1", "t"));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => { vi.advanceTimersByTime(LIVE_REFRESH_MS * 2); });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
