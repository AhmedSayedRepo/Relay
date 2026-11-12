import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { Account } from "./account";
import { LinkPhoneDialog, QrCode } from "./components/LinkPhone";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./preferences";

const json = (value: unknown, status = 200) => Promise.resolve(new Response(status === 204 ? null : JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
const renderRelay = (node: React.ReactNode) => render(<ThemeProvider><I18nProvider>{node}</I18nProvider></ThemeProvider>);

beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); window.location.hash = ""; });
afterEach(() => cleanup());

describe("account recovery and phone linking", () => {
  it("explains how to recover when the server cannot send email", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request).endsWith("/auth/recovery-options") ? json({ email_reset: false }) : json({}, 404));
    renderRelay(<App />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: "Forgot username or password?" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("reset-password command");
    expect(screen.queryByText(/recovery code/i)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Back to sign in" }));
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });

  it("sends a reset link by email when the server can send email", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request).endsWith("/auth/recovery-options") ? json({ email_reset: true }) : json({ status: "accepted" }, 202));
    renderRelay(<App />);
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledWith("/api/v1/auth/recovery-options"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    fireEvent.click(screen.getByRole("button", { name: "Forgot username or password?" }));
    fireEvent.change(await screen.findByLabelText("Admin email"), { target: { value: "owner@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Send reset link" }));
    expect(await screen.findByRole("status")).toHaveTextContent("reset link is on its way");
  });

  it("sets a new password from an emailed reset link", async () => {
    window.location.hash = "#/reset-password?token=abcdefghijklmnopqrstuvwxyz012345";
    const bodies: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      if (String(request).endsWith("/password-reset/confirm")) { bodies.push(JSON.parse(String(init?.body))); return json(null, 204); }
      return json({ email_reset: true });
    });
    renderRelay(<App />);
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: "another-long-password" } });
    fireEvent.change(screen.getByLabelText("Confirm new password"), { target: { value: "another-long-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new password" }));
    expect(await screen.findByRole("heading", { name: "Welcome to Relay" })).toBeInTheDocument();
    expect(bodies).toEqual([{ token: "abcdefghijklmnopqrstuvwxyz012345", new_password: "another-long-password" }]);
    expect(window.location.hash).toBe("");
  });

  it("shows only the password card on Account (no recovery codes)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json({ id: "u1", email: "owner@example.com", is_active: true, created_at: "2026-10-01T00:00:00Z" }));
    renderRelay(<Account token="token" />);
    expect(await screen.findByText("owner@example.com")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Password" })).toBeInTheDocument();
    expect(screen.queryByText("Recovery codes")).toBeNull();
  });

  it("shows a scannable QR code and asks before replacing a linked phone", async () => {
    const bodies: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((_request, init) => {
      const body = JSON.parse(String(init?.body)) as { replace_existing: boolean }; bodies.push(body);
      if (!body.replace_existing) return json({ error: { message: "Replacing an installation requires explicit authorization." } }, 409);
      return json({ challenge_id: "c1", challenge: "c1.secret-value", device_id: "d1", expires_at: new Date(Date.now() + 600000).toISOString() }, 201);
    });
    renderRelay(<LinkPhoneDialog deviceId="d1" name="Front desk" token="token" open onOpenChange={() => undefined} />);
    expect(await screen.findByText(/already linked/)).toBeInTheDocument();
    const create = screen.getByRole("button", { name: "Create a new code" }); expect(create).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Replace the phone that is linked now" })); fireEvent.click(create);
    expect(await screen.findByRole("img", { name: "QR code for the one-time enrollment code" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("c1.secret-value")).toBeInTheDocument();
    expect(screen.getByText(/^(9|10):\d\d$/)).toBeInTheDocument();
    expect(bodies).toEqual([{ replace_existing: false }, { replace_existing: true }]);
  });

  it("draws a QR symbol whose quiet zone surrounds the modules", () => {
    const { container } = render(<QrCode value="c1.secret" label="code" />);
    const viewBox = container.querySelector("svg")?.getAttribute("viewBox")?.split(" ").map(Number) ?? [];
    expect(viewBox[2]).toBeGreaterThanOrEqual(29); // version 1 (21 modules) + 8 quiet modules
    expect(container.querySelector("path")?.getAttribute("d")).toMatch(/^M\d+,\d+h1v1h-1z/);
  });
});
