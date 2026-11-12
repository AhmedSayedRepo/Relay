import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DeviceActions } from "./components/DeviceAdmin";
import { Devices } from "./pages";
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

  it("hides the training switch on a phone that only collects bank SMS", async () => {
    const device = (id: string, name: string, configuration: Record<string, unknown>) => ({ id, name, status: "ACTIVE", last_seen_at: null, external_device_id: id, app_version: "0.6.2", configuration_version: 1, transaction_count: 0, duplicate_count: 0, needs_review_count: 0, configuration });
    const items = [device("sms", "Bank phone", { sms_enabled: true, images_enabled: false }), device("rx", "Counter", { sms_enabled: false, images_enabled: true })];
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request).includes("/operations/devices") ? json({ items, total: 2, limit: 100, offset: 0 }) : json({ device_id: "x", enabled: false, sms_device_ids: [], direction: "IN" }));
    render(<ThemeProvider><I18nProvider><Devices token="t" /></I18nProvider></ThemeProvider>);
    expect(await screen.findByRole("heading", { name: "Counter" })).toBeInTheDocument();
    expect(await screen.findAllByRole("checkbox", { name: "Use this device’s reviewed receipts for model training" })).toHaveLength(1);
  });
});
