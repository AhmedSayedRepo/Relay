import { FormEvent, useState } from "react";
import { ApiError, send } from "./api";
import { Icon } from "./components/Brand";
import { PageHeading, Status } from "./components/Layout";
import { useApi } from "./hooks";
import { formatMessage, useI18n } from "./i18n";

function PasswordCard({ token }: { token: string }) {
  const { messages: m } = useI18n();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle"); const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    const next = String(form.get("new") || "");
    if (next !== String(form.get("confirm") || "")) { setError(m.recovery.mismatch); return; }
    if (next.length < 12) { setError(m.recovery.tooShort); return; }
    setState("busy"); setError("");
    try {
      await send("/auth/password", { current_password: form.get("current"), new_password: next }, { token });
      element.reset(); setState("done");
    } catch (reason) { setError(reason instanceof ApiError && reason.status === 401 ? m.account.wrongPassword : m.account.failed); setState("idle"); }
  }
  return <article className="panel account-panel">
    <h2>{m.account.passwordTitle}</h2>
    {error && <p className="status error" role="alert">{error}</p>}
    {state === "done" && <p className="status success" role="status">{m.account.changed}</p>}
    <form className="stack-form" onSubmit={(event) => void submit(event)}>
      <label>{m.account.currentPassword}<input name="current" type="password" autoComplete="current-password" required /></label>
      <label>{m.account.newPassword}<input name="new" type="password" autoComplete="new-password" minLength={12} maxLength={1024} required /></label>
      <label>{m.account.confirmPassword}<input name="confirm" type="password" autoComplete="new-password" required /></label>
      <p className="hint">{m.recovery.passwordRule}</p>
      <button disabled={state === "busy"} type="submit">{state === "busy" ? m.account.changing : m.account.change}</button>
    </form>
  </article>;
}

function CodesCard({ token }: { token: string }) {
  const { messages: m } = useI18n();
  const { data, reload } = useApi<{ remaining: number }>("/auth/recovery-codes", token);
  const [codes, setCodes] = useState<string[]>([]); const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""); const [copied, setCopied] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element); setBusy(true); setError("");
    try {
      const body = await send<{ codes: string[] }>("/auth/recovery-codes", { password: form.get("password") }, { token });
      element.reset(); setCodes(body.codes); setCopied(false); reload();
    } catch { setError(m.account.failed); } finally { setBusy(false); }
  }
  const text = codes.join("\n");
  function copy() { void navigator.clipboard?.writeText(text).then(() => setCopied(true)).catch(() => undefined); }
  function download() {
    const url = URL.createObjectURL(new Blob([text + "\n"], { type: "text/plain" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "relay-recovery-codes.txt"; anchor.click(); URL.revokeObjectURL(url);
  }
  const remaining = data?.remaining;
  return <article className="panel account-panel">
    <h2>{m.account.codesTitle}</h2>
    <p className="hint">{m.account.codesIntro}</p>
    {remaining !== undefined && <p className={"code-count" + (remaining <= 3 ? " low" : "")}>{remaining === 0 ? m.account.none : formatMessage(m.account.remaining, { count: remaining })}{remaining > 0 && remaining <= 3 && <span> {m.account.fewLeft}</span>}</p>}
    {codes.length > 0 && <section className="fresh-codes" aria-labelledby="fresh-codes-title">
      <h3 id="fresh-codes-title">{m.account.newCodes}</h3><p className="hint">{m.account.newCodesHint}</p>
      <ol className="code-grid" dir="ltr">{codes.map((code) => <li key={code}><code>{code}</code></li>)}</ol>
      <div className="button-row"><button className="quiet-button" type="button" onClick={copy}><Icon name="copy" size={18} />{copied ? m.account.copied : m.account.copy}</button><button className="quiet-button" type="button" onClick={download}><Icon name="download" size={18} />{m.account.download}</button></div>
    </section>}
    {error && <p className="status error" role="alert">{error}</p>}
    <form className="stack-form inline-confirm" onSubmit={(event) => void submit(event)}>
      <label>{m.account.confirmWithPassword}<input name="password" type="password" autoComplete="current-password" required /></label>
      <button disabled={busy} type="submit">{busy ? m.account.creating : m.account.create}</button>
    </form>
  </article>;
}

export function Account({ token }: { token: string }) {
  const { messages: m } = useI18n();
  const { data, error, loading } = useApi<{ email: string }>("/auth/me", token);
  return <section>
    <PageHeading title={m.pages.account[0]} description={m.pages.account[1]} help="privacy-security" />
    <Status loading={loading} error={error} />
    {data && <p className="signed-in">{m.account.signedInAs} <strong dir="ltr">{data.email}</strong></p>}
    <div className="two-column">
      <PasswordCard token={token} />
      <CodesCard token={token} />
    </div>
  </section>;
}
