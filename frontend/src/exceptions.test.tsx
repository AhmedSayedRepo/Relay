import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./preferences";
import { TransactionDetail } from "./pages";

const candidate = { value: "790", normalized_value: "790", original_text: "790 EGP <script>unsafe</script>", source: "receipt", source_event_id: "event-1", confidence: .9, adapter: "instapay", adapter_version: "v1", layout_profile: "v1", original_polygon: [[10, 10], [90, 10], [90, 30], [10, 30]] };
const field = { field: "amount", value: "790", state: "UNRESOLVED", reason: "CONFLICT", candidates: [candidate] };
const evidence = { schema_version: 1, state: "NEEDS_REVIEW", classification_resolved: true, supported_layout: true, blocking_fields: ["amount"], fields: { amount: field, reference_number: { field: "reference_number", value: "123456789012", state: "RESOLVED", reason: "AGREEMENT", candidates: [] } }, trusted_reference: null };
const receipt = { id: "receipt-1", object_id: "object-1", device_id: "device-1", device_name: "Relay", job_id: "job-1", receipt_type: "INSTAPAY", review_status: "NEEDS_REVIEW", processing_status: "NEEDS_REVIEW", reference_original: "123456789012", amount: "790", currency: "EGP", ocr_mean_confidence: .9, image_url: "/image", created_at: "2026-09-01T00:00:00Z", updated_at: "2026-09-01T00:00:01Z", machine_result: { evidence_first: evidence }, reviewed_result: null };
const json = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
function mount() { return render(<ThemeProvider><I18nProvider><MemoryRouter initialEntries={["/transactions/receipt-1"]}><Routes><Route path="/transactions/:id" element={<TransactionDetail token="test-token" />} /></Routes></MemoryRouter></I18nProvider></ThemeProvider>); }
beforeEach(() => { vi.restoreAllMocks(); localStorage.clear(); Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:receipt") }); Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() }); });
afterEach(cleanup);
describe("Exception-only evidence review", () => {
  it("confirms one blocking field in one action and refreshes authoritative evidence", async () => {
    let current: Record<string, unknown> = receipt;
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      if (String(request) === "/image") return Promise.resolve(new Response(new Blob(["image"])));
      if (init?.method === "POST") { current = { ...receipt, updated_at: "2026-09-01T00:00:02Z", machine_result: receipt.machine_result, reviewed_result: { evidence_first: { ...evidence, state: "REVIEW_FINALIZED", blocking_fields: [], fields: { ...evidence.fields, amount: { ...field, state: "RESOLVED" } } } } }; return json(current); }
      return json(current);
    });
    mount(); expect(await screen.findByRole("button", { name: "Confirm" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Record audited review" })).not.toBeInTheDocument();
    expect(document.querySelector("script")).not.toBeInTheDocument();
    expect(screen.getByText("Duplicate checked").closest("li")).not.toHaveClass("complete");
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText("No unresolved fields remain.")).toBeInTheDocument();
    const call = fetcher.mock.calls.find(([, init]) => init?.method === "POST");
    expect(String(call?.[0])).toBe("/api/v1/receipts/receipt-1/exceptions");
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ field: "amount", action: "CONFIRM", value: "790", expected_updated_at: receipt.updated_at });
    expect(JSON.parse(String(call?.[1]?.body)).command_id).toMatch(/^[0-9a-f-]{36}$/);
  });
  it("requires explicit selection for competing candidates and corrects only the blocking field", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : json(init?.method === "POST" ? {} : { ...receipt, machine_result: { evidence_first: { ...evidence, fields: { ...evidence.fields, amount: { ...field, candidates: [candidate, { ...candidate, value: "7900", normalized_value: "7900" }] } } } } }));
    mount(); expect(await screen.findByRole("button", { name: "Confirm" })).toBeDisabled();
    fireEvent.click(screen.getByRole("radio", { name: "7900" })); expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Correct" })); fireEvent.change(screen.getByRole("textbox", { name: "Corrected Amount" }), { target: { value: "790.00" } }); fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const payload = JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === "POST")?.[1]?.body)); expect(payload).toMatchObject({ field: "amount", action: "CORRECT", value: "790.00" }); expect(payload.corrections).toBeUndefined();
  });
  it("reuses an idempotency command only for an unchanged failed payload", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : json(init?.method === "POST" ? {} : receipt, init?.method === "POST" ? 503 : 200));
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Confirm" })); await screen.findByRole("alert"); fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2));
    const payloads = fetcher.mock.calls.filter(([, init]) => init?.method === "POST").map(([, init]) => JSON.parse(String(init?.body))); expect(payloads[0].command_id).toBe(payloads[1].command_id);
  });
  it("marks an unreadable field unavailable without accepting a value", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : json(init?.method === "POST" ? { ...receipt, resolution_result: { ...evidence, schema_version: 2, state: "NEEDS_REVIEW", blocking_fields: ["amount"] } } : receipt));
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Unavailable" }));
    await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const payload = JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === "POST")?.[1]?.body));
    expect(payload).toMatchObject({ field: "amount", action: "UNAVAILABLE", value: null });
  });
  it("advances directly after resolving the only blocking field", async () => {
    const next = { ...receipt, id: "receipt-2" };
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      if (String(request) === "/image") return Promise.resolve(new Response(new Blob(["image"])));
      if (init?.method === "POST") return json({ ...receipt, resolution_result: { ...evidence, schema_version: 2, state: "REVIEW_FINALIZED", blocking_fields: [], fields: { ...evidence.fields, amount: { ...field, state: "RESOLVED", trust: "OPERATOR_CONFIRMED" } } } });
      if (String(request).includes("next-exception")) return json(next);
      return json(String(request).endsWith("receipt-2") ? next : receipt);
    });
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(fetcher.mock.calls.some(([request]) => String(request).endsWith("/transactions/receipt-2"))).toBe(true));
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });
  it("renders Arabic exception controls with the preserved dark theme and LTR values", async () => {
    localStorage.setItem("relay.locale", "ar"); localStorage.setItem("relay.theme", "dark");
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : json(receipt));
    mount(); expect(await screen.findByRole("button", { name: "تأكيد" })).toBeEnabled(); expect(screen.getByRole("button", { name: "غير متاح" })).toBeVisible(); expect(document.documentElement).toHaveAttribute("dir", "rtl"); expect(document.documentElement.dataset.theme).toBe("dark"); expect(screen.getAllByText("790")[0].closest("bdi")).toHaveAttribute("dir", "ltr");
  });
  it("never falls back to blanket accept when evidence schema is malformed", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : json({ ...receipt, machine_result: { evidence_first: { ...evidence, fields: { amount: null } } } }));
    mount(); await screen.findByRole("alert"); expect(screen.queryByRole("button", { name: "Record audited review" })).not.toBeInTheDocument();
  });
  it("overlays a candidate region in original image coordinates without transforming evidence", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : json(receipt));
    mount(); const image = await screen.findByRole("img", { name: /Receipt evidence/ }); Object.defineProperty(image, "naturalWidth", { configurable: true, value: 100 }); Object.defineProperty(image, "naturalHeight", { configurable: true, value: 60 }); fireEvent.load(image);
    fireEvent.click(screen.getByRole("button", { name: "Show evidence region" })); const overlay = screen.getByRole("img", { name: "Show evidence region" }); expect(overlay).toHaveAttribute("viewBox", "0 0 100 60"); expect(overlay.querySelector("polygon")).toHaveAttribute("points", "10,10 90,10 90,30 10,30");
  });
  it("Next navigates without recording a decision and safely hides the previous receipt", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : String(request).includes("/review-queue") ? json({ items: [receipt, { ...receipt, id: "receipt-2" }], total: 2, limit: 100, offset: 0 }) : json({ ...receipt, id: String(request).endsWith("receipt-2") ? "receipt-2" : receipt.id }));
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Next" })); await waitFor(() => expect(fetcher.mock.calls.some(([request]) => String(request).endsWith("/transactions/receipt-2"))).toBe(true)); expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });
  it("Reject uses the receipt-level exception endpoint, not legacy acceptance", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => String(request) === "/image" ? Promise.resolve(new Response(new Blob(["image"]))) : json(init?.method === "POST" ? {} : receipt));
    mount(); fireEvent.click(await screen.findByRole("button", { name: "Reject" })); await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true)); const payload = JSON.parse(String(fetcher.mock.calls.find(([, init]) => init?.method === "POST")?.[1]?.body)); expect(payload.action).toBe("REJECT"); expect(payload.field).toBeUndefined(); expect(payload.value).toBeNull();
  });
});
