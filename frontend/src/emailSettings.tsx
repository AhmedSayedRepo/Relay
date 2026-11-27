import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "./api";
import { PageHeading, Status } from "./components/Layout";
import { contextHelp } from "./help/topics";
import { useApi } from "./hooks";
import { formatMessage, useI18n } from "./i18n";

const EMAIL = /^[^\s@,]+@[^\s@,]+\.[^\s@,]+$/;
const split = (text: string) => text.split(",").map((part) => part.trim().toLowerCase()).filter(Boolean);

/** Report emails as chips: type or paste addresses, comma or Enter adds them. */
function EmailChips({ label, value, disabled, remove, hint, onChange }: { label: string; value: string; disabled: boolean; remove: string; hint: string; onChange: (value: string) => void }) {
  const [draft, setDraft] = useState(""); const emails = split(value);
  const add = (text: string) => {
    const fresh = split(text); const bad = fresh.filter((item) => !EMAIL.test(item));
    const next = [...new Set([...emails, ...fresh.filter((item) => EMAIL.test(item))])].slice(0, 8);
    onChange(next.join(", ")); setDraft(bad.join(", "));
  };
  return <div className="email-chips"><label>{label}<input type="text" inputMode="email" dir="ltr" value={draft} disabled={disabled} placeholder="you@example.com, ops@example.com"
    onChange={(event) => { const text = event.target.value; if (text.includes(",")) add(text); else setDraft(text); }}
    onKeyDown={(event) => { if (event.key === "Enter" && draft.trim()) { event.preventDefault(); add(draft); } else if (event.key === "Backspace" && !draft && emails.length) onChange(emails.slice(0, -1).join(", ")); }}
    onBlur={() => { if (draft.trim()) add(draft); }} /></label>
    <p className="hint">{hint}</p>
    {emails.length > 0 && <ul className="chip-list" aria-label={label}>{emails.map((email) => <li key={email} className="chip" dir="ltr">{email}<button type="button" className="chip-remove" aria-label={remove + " " + email} disabled={disabled} onClick={() => onChange(emails.filter((item) => item !== email).join(", "))}>×</button></li>)}</ul>}
  </div>;
}

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
      <EmailChips label={e.reportEmail} value={form.report_email} disabled={busy} remove={e.removeEmail} hint={e.reportHint} onChange={(report_email) => change({ report_email })} />
      <div className="form-actions"><button type="submit" disabled={busy}>{e.save}</button><button type="button" className="quiet-button" disabled={busy} onClick={() => void test()}>{e.test}</button></div>
      {notice && <p className={"status" + (notice.error ? " error" : "")} role={notice.error ? "alert" : "status"}>{notice.text}</p>}
    </form>
    <article className="panel"><h2>{e.howTitle}</h2><ol className="steps">{e.how.map((step) => <li key={step}>{step}</li>)}</ol><p className="hint">{e.privacy}</p></article></div>}
    <RecipientCard token={token} />
    <ShiftCloseCard token={token} />
  </section>;
}

type Recipient = { name: string; phone: string | null };

/** Who receipts must be sent to; checked before a bank SMS can confirm one. */
function RecipientCard({ token }: { token: string }) {
  const { messages: m } = useI18n(); const r = m.recipientSettings;
  const { data } = useApi<Recipient>("/settings/recipient", token);
  const [form, setForm] = useState<Recipient>({ name: "", phone: "" }); const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (data) setForm({ name: data.name, phone: data.phone ?? "" });
  }, [data]);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(null);
    try {
      const next = await api<Recipient>("/settings/recipient", token, { method: "PUT", body: JSON.stringify({ name: form.name.trim(), phone: form.phone?.trim() || null }) });
      setForm({ name: next.name, phone: next.phone ?? "" }); setNotice({ text: r.saved, error: false });
    } catch (failure) {
      setNotice({ text: failure instanceof ApiError && failure.status === 422 ? r.invalid : r.saveFailed, error: true });
    } finally { setBusy(false); }
  }
  return <form className="panel form-panel" onSubmit={(event) => void submit(event)}><h2>{r.title}</h2><p className="hint">{r.hint}</p>
    <label>{r.name}<input dir="ltr" value={form.name} maxLength={60} disabled={busy} placeholder="YOUSSEF" onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
    <label>{r.phone}<input dir="ltr" inputMode="numeric" value={form.phone ?? ""} maxLength={11} disabled={busy} placeholder="01xxxxxxxxx" onChange={(event) => setForm({ ...form, phone: event.target.value })} /></label>
    <div className="form-actions"><button type="submit" disabled={busy}>{r.save}</button></div>
    {notice && <p className={"status" + (notice.error ? " error" : "")} role={notice.error ? "alert" : "status"}>{notice.text}</p>}
  </form>;
}

type ShiftClose = { time: string | null; enabled: boolean; language: "en" | "ar" };

/** The open shift closes by itself every day at this time when switched on; its report is emailed. */
function ShiftCloseCard({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const c = m.shiftCloseSettings;
  const { data } = useApi<ShiftClose>("/settings/shift-close", token);
  const [time, setTime] = useState(""); const [enabled, setEnabled] = useState(false); const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (data) { setTime(data.time ?? ""); setEnabled(data.enabled); }
  }, [data]);
  async function save(nextTime: string, nextOn: boolean) {
    setBusy(true); setNotice(null);
    try {
      const saved = await api<ShiftClose>("/settings/shift-close", token, { method: "PUT", body: JSON.stringify({ time: nextTime || null, enabled: nextOn, language: locale }) });
      setTime(saved.time ?? ""); setEnabled(saved.enabled);
      setNotice({ text: saved.enabled && saved.time ? formatMessage(c.saved, { time: saved.time }) : c.off, error: false });
    } catch { setNotice({ text: c.saveFailed, error: true }); } finally { setBusy(false); }
  }
  return <form className="panel form-panel" onSubmit={(event) => { event.preventDefault(); void save(time, enabled); }}><h2>{c.title}</h2><p className="hint">{c.hint}</p>
    <label className="checkbox"><input type="checkbox" checked={enabled} disabled={busy || (!enabled && !time)} onChange={(event) => void save(time, event.target.checked)} />{enabled ? c.on : c.offLabel}</label>
    <label>{c.time}<input type="time" dir="ltr" value={time} disabled={busy} onChange={(event) => setTime(event.target.value)} /></label>
    <div className="form-actions"><button type="submit" disabled={busy || !time}>{c.save}</button></div>
    {notice && <p className={"status" + (notice.error ? " error" : "")} role={notice.error ? "alert" : "status"}>{notice.text}</p>}
  </form>;
}
