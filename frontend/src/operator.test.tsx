import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { Layout } from "./components/Layout";
import { Dashboard, Devices, DuplicateDetail, HelpPage, ProcessingQueue, Reports, SmsSources, SmsVerificationPanel, TransactionDetail, Transactions } from "./pages";
import { contextHelp, getHelpTopics, helpRegistry, helpTopics } from "./help/topics";
import { ar } from "./i18n/ar";
import { en } from "./i18n/en";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./preferences";

const json = (value: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }));
const page = <T,>(items: T[]) => ({ items, total: items.length, limit: 25, offset: 0 });
const dashboard = { date_from: "2026-09-01", date_to: "2026-09-07", final_transaction_count: 2, reviewed_count: 1, needs_review_count: 1, duplicate_group_count: 1, duplicate_candidate_count: 1, processing_failure_count: 0, pending_processing_count: 1, receipt_image_activity: 2, sms_activity: 1, totals_by_currency: [{ currency: "EGP", amount: "2501.00", transaction_count: 2 }], processing_by_status: [], activity_by_source: [], transactions_by_device: [] };
const transaction = {
  id: "candidate-1", job_id: "job-1", object_id: "object-1", device_id: "device-1", device_name: "مكتب الاستقبال",
  receipt_type: "INSTAPAY", review_status: "NEEDS_REVIEW", processing_status: "NEEDS_REVIEW", processing_failure: null,
  amount: "2501.00", currency: "EGP", transaction_occurred_at: "2026-09-06T12:00:00Z", reference_original: "IPN-123/A",
  sender_name: "أحمد علي", recipient_name: "Nora Smith", duplicate_group_id: "group-1", duplicate_of_candidate_id: "candidate-0",
  duplicate_reason: "NORMALIZED_REFERENCE_MATCH", is_canonical: false, source_type: "IMAGE", received_at: "2026-09-06T12:01:00Z",
  created_at: "2026-09-06T12:02:00Z", ocr_mean_confidence: 0.91, image_url: "/api/v1/receipts/candidate-1/image",
};

function providers(node: React.ReactNode) {
  return <ThemeProvider><I18nProvider>{node}</I18nProvider></ThemeProvider>;
}
function renderRelay(node: React.ReactNode) { return render(providers(node)); }
function atRoute(path: string, routePath: string, element: React.ReactNode) {
  return renderRelay(<MemoryRouter initialEntries={[path]}><Routes><Route path={routePath} element={element} /></Routes></MemoryRouter>);
}
function leafPaths(value: unknown, prefix = ""): string[] {
  if (typeof value === "string") return [prefix];
  if (Array.isArray(value)) return value.flatMap((item, index) => leafPaths(item, prefix + "." + index));
  if (value && typeof value === "object") return Object.entries(value).flatMap(([key, item]) => leafPaths(item, prefix ? prefix + "." + key : key));
  return [];
}

beforeEach(() => {
  vi.restoreAllMocks(); localStorage.clear(); sessionStorage.clear(); document.documentElement.removeAttribute("lang"); document.documentElement.removeAttribute("dir"); delete document.documentElement.dataset.theme;
});
afterEach(() => { cleanup(); document.body.removeAttribute("data-scroll-locked"); document.body.removeAttribute("style"); });

describe("Sprint 7 operator experience", () => {
  it("keeps complete English and Arabic catalogs in exact key parity", () => {
    expect(leafPaths(ar).sort()).toEqual(leafPaths(en).sort());
    for (const catalog of [en, ar]) for (const path of leafPaths(catalog)) expect(path).not.toBe("");
    expect(ar.navigation.dashboard).toBe("لوحة التحكم");
    expect(ar.navigation.transactions).toBe("المعاملات");
  });

  it("keeps all 25 Help topics stable, connected, and bilingual", () => {
    const english = getHelpTopics("en"); const arabic = getHelpTopics("ar");
    expect(english).toHaveLength(25); expect(arabic).toHaveLength(25); expect(arabic.map((topic) => topic.id)).toEqual(english.map((topic) => topic.id));
    expect(helpRegistry.size).toBe(helpTopics.length);
    for (const topic of english) for (const related of topic.related) expect(helpRegistry.has(related)).toBe(true);
    for (const topicId of Object.values(contextHelp)) expect(helpRegistry.has(topicId)).toBe(true);
    expect(arabic.every((topic) => topic.title && topic.summary && topic.steps.every(Boolean))).toBe(true);
  });

  it("persists only allowlisted locale and sets true document direction", async () => {
    renderRelay(<App />);
    fireEvent.change(screen.getByLabelText("Change language"), { target: { value: "ar" } });
    await waitFor(() => expect(document.documentElement).toHaveAttribute("dir", "rtl"));
    expect(document.documentElement).toHaveAttribute("lang", "ar");
    expect(screen.getByRole("heading", { name: "مرحباً بك في Relay" })).toBeInTheDocument();
    expect(localStorage.getItem("relay.locale")).toBe("ar");
    expect(localStorage.getItem("access_token")).toBeNull();
    cleanup(); localStorage.setItem("relay.locale", "unsafe-locale");
    renderRelay(<App />); await waitFor(() => expect(document.documentElement).toHaveAttribute("lang", "en"));
  });

  it("persists light, dark, and system theme choices without credentials", async () => {
    renderRelay(<App />);
    fireEvent.change(screen.getByLabelText("Change color theme"), { target: { value: "dark" } });
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(localStorage.getItem("relay.theme")).toBe("dark");
    fireEvent.change(screen.getByLabelText("Change color theme"), { target: { value: "light" } });
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    fireEvent.change(screen.getByLabelText("Change color theme"), { target: { value: "system" } });
    await waitFor(() => expect(localStorage.getItem("relay.theme")).toBe("system"));
    expect([...Array(localStorage.length)].map((_, index) => localStorage.key(index)).sort()).toEqual(["relay.locale", "relay.theme"]);
  });

  it("shows dashboard loading and currency-separated operational hierarchy", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json(dashboard));
    renderRelay(<Dashboard token="token" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading operational data");
    expect(await screen.findByText("Final money by currency")).toBeInTheDocument();
    expect(screen.getByText(/2,501/)).toBeInTheDocument();
    expect(screen.getByText("Currencies remain separate so totals keep their meaning.")).toBeInTheDocument();
  });

  it("renders intentional authorization, service, and empty states", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch");
    fetcher.mockImplementationOnce(() => json({ error: { message: "Denied" } }, 403));
    renderRelay(<Transactions token="token" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("session is invalid or expired");
    cleanup();
    fetcher.mockImplementationOnce(() => json(page([])));
    renderRelay(<Transactions token="token" />);
    expect(await screen.findByText("No transactions in this range.")).toBeInTheDocument();
    expect(screen.getByText(/Change the filters/)).toBeInTheDocument();
  });

  it("applies server filters and isolates references, money, and mixed Arabic data", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(() => json(page([transaction])));
    atRoute("/transactions", "/transactions", <Transactions token="token" />);
    const link = await screen.findByRole("link", { name: /Open transaction evidence IPN-123/ });
    expect(link).toHaveAttribute("href", "/transactions/candidate-1");
    expect(screen.getByText("IPN-123/A").closest("bdi")).toHaveAttribute("dir", "ltr");
    expect(screen.getByText(/2,501/).closest("bdi")).toHaveAttribute("dir", "ltr");
    expect(screen.getByText("أحمد علي")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Reference, sender, or recipient"), { target: { value: "IPN-123" } });
    fireEvent.change(screen.getByLabelText("Review"), { target: { value: "NEEDS_REVIEW" } });
    fireEvent.change(screen.getByLabelText("Duplicate"), { target: { value: "DUPLICATE" } });
    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "egp" } });
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "reference" } });
    await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringMatching(/search=IPN-123.*review_status=NEEDS_REVIEW.*duplicate_status=DUPLICATE.*currency=EGP.*sort_by=reference/), expect.any(Object)));
  });

  it("shows protected evidence and fails closed without a trust projection", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:receipt") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request) => {
      const url = String(request);
      if (url.endsWith("/image")) return Promise.resolve(new Response(new Blob(["image"]), { status: 200 }));
      return json({ ...transaction, ocr: { provider: "tesseract", text: "safe <script>text</script>" }, review_history: [], duplicate_resolution_history: [] });
    });
    atRoute("/transactions/candidate-1", "/transactions/:id", <TransactionDetail token="token" />);
    expect(await screen.findByRole("heading", { name: "Receipt journey" })).toBeInTheDocument();
    expect(screen.getByText("Final").closest("li")).toHaveClass("pending");
    expect(await screen.findByRole("img", { name: /Receipt evidence for IPN-123/ })).toHaveAttribute("src", "blob:receipt");
    expect(document.querySelector("script")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Show technical evidence"));
    expect(screen.getByText((_, element) => element?.tagName === "PRE" && Boolean(element.textContent?.includes("safe <script>text</script>")))).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Record audited review" })).not.toBeInTheDocument();
    expect(fetcher.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("compares canonical and duplicate evidence while preserving provenance", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:receipt") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const canonical = { ...transaction, id: "candidate-0", is_canonical: true, duplicate_of_candidate_id: null, amount: "2500.00", sender_name: "Ahmed" };
    vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      const url = String(request); if (url.endsWith("/image")) return Promise.resolve(new Response(new Blob(["image"]), { status: 200 }));
      if (url.includes("/resolution") && init?.method === "POST") return json({ status: "recorded" });
      return json({ group_id: "group-1", group_size: 2, members: [canonical, transaction] });
    });
    atRoute("/duplicates/group-1", "/duplicates/:id", <DuplicateDetail token="token" />);
    expect(await screen.findByText("Why Relay grouped these")).toBeInTheDocument();
    expect(screen.getByText("Canonical candidate")).toBeInTheDocument();
    expect(screen.getAllByText("Differs from canonical").length).toBeGreaterThanOrEqual(2);
    fireEvent.change(screen.getAllByLabelText("Operator decision")[1], { target: { value: "CONFIRMED_DUPLICATE" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Record audited decision" })[1]);
    expect(await screen.findByText(/deterministic link remains preserved/i)).toBeInTheDocument();
  });

  it("offers retry only for eligible bounded transient failures", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      if (String(request).endsWith("/retry") && init?.method === "POST") return json({ id: "job-1", status: "PENDING" });
      return json({ items: [{ id: "job-1", status: "FAILED", failure_code: "OCR_TIMEOUT", attempt_count: 3, max_attempts: 5, next_attempt_at: "2026-09-07T00:00:00Z", object_id: "one" }, { id: "job-2", status: "FAILED", failure_code: "RESOURCE_LIMIT", attempt_count: 1, max_attempts: 3, next_attempt_at: "2026-09-07T00:00:00Z", object_id: "two" }], has_more: false });
    });
    renderRelay(<ProcessingQueue token="token" />);
    const retry = await screen.findByRole("button", { name: "Retry processing" });
    expect(screen.getAllByText("No retry")).toHaveLength(1);
    fireEvent.click(retry);
    expect(await screen.findByText(/queued for one bounded retry/i)).toBeInTheDocument();
  });

  it("downloads a filtered export and translates device configuration without secrets", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:export") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request).includes("export.xlsx") ? Promise.resolve(new Response(new Blob(["xlsx"]), { status: 200, headers: { "Content-Disposition": 'attachment; filename="relay-filtered.xlsx"' } })) : json(dashboard));
    renderRelay(<Reports token="token" />); await screen.findByText(/2 final transactions/);
    fireEvent.change(screen.getByLabelText("Currency"), { target: { value: "egp" } }); fireEvent.click(screen.getByRole("button", { name: "Export Excel" }));
    expect(await screen.findByText("Workbook downloaded.")).toBeInTheDocument(); expect(click).toHaveBeenCalled();
    cleanup();
    fetcher.mockImplementation(() => json(page([{ id: "device-1", name: "Front desk", status: "ACTIVE", last_seen_at: "2026-09-07T00:00:00Z", external_device_id: "installation-1", app_version: "0.6.0", configuration_version: 4, transaction_count: 10, duplicate_count: 2, needs_review_count: 1, configuration: { sms_enabled: true, image_rules: 2 } }])));
    renderRelay(<Devices token="token" />);
    expect(await screen.findByRole("heading", { name: "Front desk" })).toBeInTheDocument();
    expect(screen.getByText("SMS collection")).toBeInTheDocument(); expect(screen.getByText("Image rules")).toBeInTheDocument();
    expect(screen.queryByText("sensitive-credential-value")).not.toBeInTheDocument();
  });

  it("lets an operator opt a device in and out of training capture", async () => {
    const device = { id: "device-1", name: "Front desk", status: "ACTIVE", last_seen_at: null, external_device_id: "installation-1", app_version: "0.6.0", configuration_version: 1, transaction_count: 0, duplicate_count: 0, needs_review_count: 0, configuration: {} };
    let saved = false; const writes: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((request, init) => {
      const url = String(request);
      if (url.endsWith("/devices/device-1/training-consent")) {
        if (init?.method === "PUT") { const body = JSON.parse(String(init.body)) as { enabled: boolean }; writes.push(body); if (body.enabled === false) return json({ error: { message: "nope" } }, 500); saved = body.enabled; }
        return json({ device_id: "device-1", enabled: saved });
      }
      return json(page([device]));
    });
    renderRelay(<Devices token="token" />);
    const toggle = await screen.findByRole("checkbox", { name: "Use this device’s reviewed receipts for model training" });
    expect(toggle).not.toBeChecked(); expect(screen.getByText(/^Off:/)).toBeInTheDocument();
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).toBeChecked()); expect(screen.getByText(/^On:/)).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(await screen.findByRole("alert")).toHaveTextContent("The change was not saved."); expect(toggle).toBeChecked();
    expect(writes).toEqual([{ enabled: true }, { enabled: false }]);
  });

  it("shows SMS verification and lets an operator pick the matching SMS", async () => {
    const options = [{ event_id: "sms-1", device_id: "phone-b", amount: "1250.50", currency: "EGP", occurred_at: "2026-09-06T12:01:00Z", in_window: true }];
    const writes: unknown[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((_request, init) => {
      if (init?.method === "PUT") { writes.push(JSON.parse(String(init.body))); return json({ status: "VERIFIED", method: "MANUAL", sms: options[0], option_count: 1, options: [] }); }
      return json({ status: "AMBIGUOUS", method: "AUTO", sms: null, option_count: 2, options });
    });
    renderRelay(<SmsVerificationPanel id="candidate-1" token="token" />);
    expect(await screen.findByText("Choose SMS")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use this SMS" }));
    expect(await screen.findByText("SMS verified")).toBeInTheDocument();
    expect(screen.getByText("Operator")).toBeInTheDocument();
    expect(writes).toEqual([{ sms_event_id: "sms-1" }]);
  });

  it("links a receipt device to the phone that receives the bank SMS", async () => {
    let saved: string[] = [];
    vi.spyOn(globalThis, "fetch").mockImplementation((_request, init) => {
      if (init?.method === "PUT") saved = (JSON.parse(String(init.body)) as { sms_device_ids: string[] }).sms_device_ids;
      return json({ device_id: "device-a", sms_device_ids: saved });
    });
    renderRelay(<SmsSources deviceId="device-a" token="token" devices={[{ id: "device-a", name: "Counter phone" }, { id: "device-b", name: "Bank SMS phone" }]} />);
    const box = await screen.findByRole("checkbox", { name: "Bank SMS phone" });
    expect(box).not.toBeChecked(); expect(screen.queryByRole("checkbox", { name: "Counter phone" })).toBeNull();
    fireEvent.click(box);
    await waitFor(() => expect(box).toBeChecked()); expect(saved).toEqual(["device-b"]);
  });

  it("supports Arabic Help search with no mixed Relay-controlled labels", () => {
    localStorage.setItem("relay.locale", "ar"); renderRelay(<HelpPage />);
    expect(screen.getByRole("heading", { name: "دليل Relay" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("البحث في المساعدة"), { target: { value: "إعادة المعالجة" } });
    expect(screen.getByRole("heading", { name: "إعادة المعالجة الآمنة" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "نطاقات التاريخ" })).not.toBeInTheDocument();
  });

  it("exposes keyboard-operable responsive navigation semantics", () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => json(dashboard));
    atRoute("/", "*", <Routes><Route element={<Layout signOut={() => undefined} />}><Route index element={<Dashboard token="token" />} /></Route></Routes>);
    const menu = screen.getByRole("button", { name: "Open navigation" });
    expect(menu).toHaveAttribute("aria-expanded", "false"); fireEvent.click(menu); expect(menu).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "Transactions" })).toHaveAttribute("href", "/transactions");
    expect(screen.getByText("Skip to main content")).toHaveAttribute("href", "#main-content");
  });

  it("keeps the bearer token in memory through sign-in", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((request) => String(request).includes("/auth/login") ? json({ access_token: "memory-token" }) : json(dashboard));
    renderRelay(<App />);
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "admin@example.com" } }); fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret-password" } }); fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Operations overview" })).toBeInTheDocument());
    expect(localStorage.getItem("access_token")).toBeNull(); expect(sessionStorage.length).toBe(0);
    expect([...Array(localStorage.length)].map((_, index) => localStorage.key(index)).sort()).toEqual(["relay.locale", "relay.theme"]);
  });
});
