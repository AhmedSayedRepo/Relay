import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "./api";
import { PageHeading, Status } from "./components/Layout";
import { contextHelp } from "./help/topics";
import { useApi } from "./hooks";
import { formatMessage, useI18n } from "./i18n";

type EmailSettings = { sender_name: string; sender_email: string; report_email: string | null; password_set: boolean; source: "portal" | "server" | "none" };

/** Admin page: the Gmail sender Relay uses for reset links and the daily report. */
export function EmailSettingsPage({ token }: { token: string }) {
  const { messages: m } = useI18n(); const e = m.emailSettings;
  const { data, error, loading } = useApi<EmailSettings>("/settings/email", token);
  const [form, setForm] = useState({ sender_name: "", sender_email: "", app_password: "", report_email: "" });
  const [saved, setSaved] = useState<EmailSettings | null>(null); const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  useEffect(() => {
    // Copy the loaded settings into the form once they arrive.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (data) { setSaved(data); setForm({ sender_name: data.sender_name, sender_email: data.sender_email, app_password: "", report_email: data.report_email ?? "" }); }
  }, [data]);
  const current = saved ?? data;
  const change = (patch: Partial<typeof form>) => { setForm((value) => ({ ...value, ...patch })); setNotice(null); };
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(null);
    try {
      const body = { sender_name: form.sender_name.trim(), sender_email: form.sender_email.trim(), app_password: form.app_password || null, report_email: form.report_email.trim() || null };
      const next = await api<EmailSettings>("/settings/email", token, { method: "PUT", body: JSON.stringify(body) });
      setSaved(next); setForm((value) => ({ ...value, app_password: "" })); setNotice({ text: e.saved, error: false });
    } catch (failure) {
      setNotice({ text: failure instanceof ApiError && failure.status === 409 ? e.passwordNeeded : failure instanceof ApiError && failure.status === 422 ? e.invalid : e.saveFailed, error: true });
    } finally { setBusy(false); }
  }
  async function test() {
    setBusy(true); setNotice(null);
    try {
      const result = await api<{ sent_to: string }>("/settings/email/test", token, { method: "POST" });
      setNotice({ text: formatMessage(e.testSent, { email: result.sent_to }), error: false });
    } catch (failure) {
      const status = failure instanceof ApiError ? failure.status : 0;
      setNotice({ text: status === 409 ? e.saveFirst : status === 502 ? e.rejected : e.unreachable, error: true });
    } finally { setBusy(false); }
  }
  return <section><PageHeading title={m.pages.emailSettings[0]} description={m.pages.emailSettings[1]} help={contextHelp.help} />
    <Status loading={loading && !current} error={error} />
    {current && <div className="two-column"><form className="panel form-panel" onSubmit={(event) => void submit(event)}>
      <p className={"badge " + (current.source === "none" ? "warning" : "success")}>{e.source[current.source]}</p>
      <label>{e.senderName}<input value={form.sender_name} maxLength={120} required disabled={busy} placeholder="Relay - Ahmed" onChange={(event) => change({ sender_name: event.target.value })} /></label>
      <label>{e.senderEmail}<input type="email" dir="ltr" value={form.sender_email} maxLength={254} required disabled={busy} placeholder="name@gmail.com" onChange={(event) => change({ sender_email: event.target.value })} /></label>
      <label>{e.appPassword}<input type="password" dir="ltr" autoComplete="new-password" value={form.app_password} maxLength={200} disabled={busy} placeholder={current.password_set ? e.passwordKept : "xxxx xxxx xxxx xxxx"} onChange={(event) => change({ app_password: event.target.value })} /></label>
      <label>{e.reportEmail}<input type="email" dir="ltr" value={form.report_email} maxLength={254} disabled={busy} placeholder="you@example.com" onChange={(event) => change({ report_email: event.target.value })} /></label>
      <div className="form-actions"><button type="submit" disabled={busy}>{e.save}</button><button type="button" className="quiet-button" disabled={busy} onClick={() => void test()}>{e.test}</button></div>
      {notice && <p className={"status" + (notice.error ? " error" : "")} role={notice.error ? "alert" : "status"}>{notice.text}</p>}
    </form>
    <article className="panel"><h2>{e.howTitle}</h2><ol className="steps">{e.how.map((step) => <li key={step}>{step}</li>)}</ol><p className="hint">{e.privacy}</p></article></div>}
  </section>;
}
