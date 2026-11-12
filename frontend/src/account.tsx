import { FormEvent, useState } from "react";
import { ApiError, send } from "./api";
import { PageHeading, Status } from "./components/Layout";
import { useApi } from "./hooks";
import { useI18n } from "./i18n";

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

export function Account({ token }: { token: string }) {
  const { messages: m } = useI18n();
  const { data, error, loading } = useApi<{ email: string }>("/auth/me", token);
  return <section>
    <PageHeading title={m.pages.account[0]} description={m.pages.account[1]} help="privacy-security" />
    <Status loading={loading} error={error} />
    {data && <p className="signed-in">{m.account.signedInAs} <strong dir="ltr">{data.email}</strong></p>}
    <div className="two-column">
      <PasswordCard token={token} />
    </div>
  </section>;
}
