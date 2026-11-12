import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeviceActions } from "./components/DeviceAdmin";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./preferences";

const json = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
const mount = (status: string, changed = () => {}) => render(<ThemeProvider><I18nProvider><DeviceActions id="d1" name="Spare" status={status} token="t" changed={changed} linkOpen={false} setLinkOpen={() => {}} /></I18nProvider></ThemeProvider>);

beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); });
afterEach(() => cleanup());

describe("device delete", () => {
  it("deletes an unlinked device without disabling it first", async () => {
    const calls: { url: string; method?: string }[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => { calls.push({ url: String(request), method: init?.method }); return json({ removed: true }); });
    const changed = vi.fn();
    mount("PENDING", changed);
    fireEvent.click(screen.getByRole("button", { name: "Delete device" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, delete" }));
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(calls.some((call) => call.url.endsWith("/devices/d1") && call.method === "DELETE")).toBe(true);
  });

  it("asks to disable a linked device before it can be deleted", () => {
    mount("ACTIVE");
    expect(screen.queryByRole("button", { name: "Delete device" })).toBeNull();
  });
});
