// Renders the built operator console against mocked, synthetic API data.
// Usage: node scripts/console-shots.cjs dist screenshots (CI: operator-screenshots.yml).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const dist = process.argv[2];
const out = process.argv[3];
fs.mkdirSync(out, { recursive: true });
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const iso = (minutes) => new Date(Date.now() + minutes * 60000).toISOString();

const devices = { items: [
  { id: "d1", name: "Cashier phone 1", status: "ACTIVE", last_seen_at: iso(-3), external_device_id: "phone-3fa91c", app_version: "0.6.0", configuration_version: 4, transaction_count: 148, duplicate_count: 2, needs_review_count: 5, configuration: { sms_enabled: false, image_rules: 1 } },
  { id: "d2", name: "Bank SMS phone", status: "ACTIVE", last_seen_at: iso(-1), external_device_id: "phone-77b20e", app_version: "0.6.0", configuration_version: 2, transaction_count: 0, duplicate_count: 0, needs_review_count: 0, configuration: { sms_enabled: true, sms_rules: 2 } },
], total: 2, limit: 100, offset: 0 };
const dashboard = { date_from: "2026-10-23", date_to: "2026-10-29", final_transaction_count: 142, reviewed_count: 131, needs_review_count: 5, duplicate_group_count: 2, duplicate_candidate_count: 3, processing_failure_count: 1, pending_processing_count: 4, receipt_image_activity: 151, sms_activity: 139, totals_by_currency: [{ currency: "EGP", transaction_count: 142, amount: "418350.00" }] };

function api(url, method) {
  const p = new URL(url).pathname.replace("/api/v1", "");
  if (p === "/auth/recovery-options") return { email_reset: true };
  if (p === "/auth/login") return { access_token: "t", token_type: "bearer", expires_in: 900 };
  if (p === "/auth/recovery-code") return { email: "owner@example.com" };
  if (p === "/auth/me") return { id: "u1", email: "owner@example.com", is_active: true, created_at: iso(-9000) };
  if (p === "/auth/recovery-codes") return method === "POST" ? { codes: ["K7QF-2MXH-9RTD", "PW4N-ZC8B-3JYE", "HG6V-TA2S-WQ5M", "B9RE-XK3P-7NDF", "MZ2C-Y8WH-4TGA", "QD5J-N7VB-2KXR", "T3HW-6PZM-9ECS", "XA8K-4RFQ-7YBN", "E2NT-M9GD-5HWV", "VR7Y-3CJK-8QPZ"] } : { remaining: 3 };
  if (p.endsWith("/enrollment-challenges")) return { challenge_id: "6b0f3c2e-9a41-4d7e-8a51-0c1d2e3f4a5b", challenge: "6b0f3c2e-9a41-4d7e-8a51-0c1d2e3f4a5b.Xr4kP2mQ9vT7nB1yW8sD3fH6jL0cZ5aE2gU", device_id: "d1", expires_at: iso(9.6) };
  if (p.startsWith("/operations/devices")) return devices;
  if (p.startsWith("/dashboard")) return dashboard;
  if (p.includes("training-consent")) return { device_id: "d1", enabled: false };
  if (p.includes("sms-sources")) return { device_id: "d1", sms_device_ids: ["d2"], direction: "IN" };
  return { items: [], total: 0, limit: 25, offset: 0 };
}

(async () => {
  const browser = await chromium.launch();
  async function open(name, { width = 1440, height = 900, locale = "en", theme = "light", hash = "", steps = async () => {} } = {}) {
    const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1 });
    await context.addInitScript(([l, t]) => { localStorage.setItem("relay.locale", l); localStorage.setItem("relay.theme", t); }, [locale, theme]);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
    await page.route("**/*", async (route) => {
      const url = route.request().url();
      if (url.includes("/api/v1/")) return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(api(url, route.request().method())) });
      const rel = new URL(url).pathname.replace(/^\/operator\/?/, "") || "index.html";
      const file = path.join(dist, rel);
      if (!fs.existsSync(file)) return route.fulfill({ status: 404, body: "" });
      route.fulfill({ status: 200, contentType: types[path.extname(file)] || "application/octet-stream", body: fs.readFileSync(file) });
    });
    await page.goto("http://relay.test/operator/" + hash);
    await page.waitForLoadState("networkidle");
    await steps(page);
    await page.waitForTimeout(250);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    await page.screenshot({ path: path.join(out, name + ".png"), fullPage: true });
    console.log(`::notice title=${name}::overflow ${overflow}px${errors.length ? " ERRORS " + errors.join(" | ").slice(0, 600) : ""}`);
    await context.close();
  }
  const signIn = async (page) => { await page.fill("input[name=email]", "owner@example.com"); await page.fill("input[name=password]", "x"); await page.click("form button[type=submit]"); await page.waitForTimeout(400); };
  const go = (where) => async (page) => { await signIn(page); await page.evaluate((h) => { location.hash = h; }, where); await page.waitForTimeout(500); };
  await open("login-en-light");
  await open("login-ar-dark", { locale: "ar", theme: "dark" });
  await open("login-mobile-en", { width: 390, height: 844 });
  await open("recover-choose-en", { steps: async (p) => { await p.click("button.forgot"); } });
  await open("recover-code-done-ar", { locale: "ar", width: 390, height: 844, steps: async (p) => { await p.click("button.forgot"); await p.click(".choice >> nth=0"); await p.fill("input[name=code]", "K7QF-2MXH-9RTD"); await p.click("form button[type=submit]"); await p.waitForTimeout(300); } });
  await open("reset-link-en-dark", { theme: "dark", hash: "#/reset-password?token=abcdefghijklmnopqrstuvwxyz012345" });
  await open("dashboard-en-light", { steps: go("#/") });
  await open("dashboard-ar-dark-mobile", { locale: "ar", theme: "dark", width: 390, height: 844, steps: go("#/") });
  await open("devices-en-light", { steps: go("#/devices") });
  await open("devices-add-en", { steps: async (p) => { await go("#/devices")(p); await p.click(".page-actions button"); } });
  await open("link-dialog-en-light", { steps: async (p) => { await go("#/devices")(p); await p.click(".device-actions button.with-icon >> nth=0"); await p.waitForTimeout(400); } });
  await open("link-dialog-ar-dark-mobile", { locale: "ar", theme: "dark", width: 390, height: 844, steps: async (p) => { await go("#/devices")(p); await p.click(".device-actions button.with-icon >> nth=0"); await p.waitForTimeout(400); } });
  await open("account-en-light", { steps: async (p) => { await go("#/account")(p); await p.fill("input[name=password] >> nth=-1", "x"); await p.click(".inline-confirm button"); await p.waitForTimeout(300); } });
  await open("account-ar-dark", { locale: "ar", theme: "dark", steps: go("#/account") });
  await browser.close();
})();
