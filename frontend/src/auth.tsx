import { FormEvent, useEffect, useState, type ReactNode } from "react";
import { ApiError, send } from "./api";
import { Icon, RelayMark } from "./components/Brand";
import { PreferenceControls } from "./components/Layout";
import { useI18n } from "./i18n";

type View = "sign-in" | "choose" | "code" | "email" | "reset";

function resetToken(): string {
  const match = window.location.hash.match(/^#\/reset-password\?token=([A-Za-z0-9_-]{20,128})$/);
  return match ? match[1] : "";
}

function AuthFrame({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  const { messages: m } = useI18n();
  return <main className="login-page" id="main-content">
    <section className="login-brand" aria-hidden="true"><RelayMark size={64} /><p>{m.common.secureEvidence}</p></section>
    <section className="login-card" aria-labelledby="login-title">
      <div className="login-top"><div className="brand large"><RelayMark /><b>{m.common.appName}</b></div><PreferenceControls compact /></div>
      <h1 id="login-title">{title}</h1>{intro && <p className="login-intro">{intro}</p>}
      {children}
    </section>
  </main>;
}

function Notice({ tone, children }: { tone: "error" | "success"; children: ReactNode }) {
  return <p className={"status " + tone} role={tone === "error" ? "alert" : "status"}>{children}</p>;
}

function BackLink({ onClick }: { onClick: () => void }) {
  const { messages: m } = useI18n();
  return <button className="text-button" type="button" onClick={onClick}><Icon name="back" size={18} />{m.recovery.back}</button>;
}

function PasswordPair({ optional = false }: { optional?: boolean }) {
  const { messages: m } = useI18n();
  return <>
    <label>{optional ? m.recovery.newPasswordOptional : m.recovery.newPassword}<input name="password" type="password" autoComplete="new-password" minLength={12} maxLength={1024} required={!optional} aria-describedby="password-hint" /></label>
    <label>{m.recovery.confirmPassword}<input name="confirm" type="password" autoComplete="new-password" maxLength={1024} required={!optional} /></label>
    <p className="hint" id="password-hint">{optional ? m.recovery.passwordHint : m.recovery.passwordRule}</p>
  </>;
}

/** Returns an error message, or the new password ("" when optional and left empty). */
function readPassword(form: FormData, messages: ReturnType<typeof useI18n>["messages"]): { error?: string; password: string } {
  const password = String(form.get("password") || ""); const confirm = String(form.get("confirm") || "");
  if (password !== confirm) return { error: messages.recovery.mismatch, password };
  if (password && password.length < 12) return { error: messages.recovery.tooShort, password };
  return { password };
}

function SignIn({ onLogin, onForgot, notice, email }: { onLogin: (token: string) => void; onForgot: () => void; notice: string; email: string }) {
  const { messages: m } = useI18n();
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
      if (!response.ok) throw new Error();
      const body = await response.json() as { access_token: string }; onLogin(body.access_token);
    } catch { setError(m.auth.failed); } finally { setBusy(false); }
  }
  return <AuthFrame title={m.auth.welcome} intro={m.auth.intro}>
    {notice && <Notice tone="success">{notice}</Notice>}{error && <Notice tone="error">{error}</Notice>}
    <form onSubmit={(event) => void submit(event)}>
      <label>{m.auth.email}<input name="email" type="email" autoComplete="username" defaultValue={email} required /></label>
      <label>{m.auth.password}<input name="password" type="password" autoComplete="current-password" required /></label>
      <button disabled={busy} type="submit">{busy ? m.auth.signingIn : m.auth.signIn}</button>
    </form>
    <button className="text-button forgot" type="button" onClick={onForgot}>{m.recovery.forgot}</button>
    <p className="pilot-note">{m.auth.pilotNote}</p>
  </AuthFrame>;
}

function Choose({ emailReset, go }: { emailReset: boolean; go: (view: View) => void }) {
  const { messages: m } = useI18n();
  return <AuthFrame title={m.recovery.title} intro={m.recovery.intro}>
    <div className="choice-list">
      <button className="choice" type="button" onClick={() => go("code")}><strong>{m.recovery.codeOption}</strong><span>{m.recovery.codeOptionHint}</span></button>
      {emailReset && <button className="choice" type="button" onClick={() => go("email")}><strong>{m.recovery.emailOption}</strong><span>{m.recovery.emailOptionHint}</span></button>}
    </div>
    <p className="hint server-hint">{m.recovery.serverHint}</p>
    <BackLink onClick={() => go("sign-in")} />
  </AuthFrame>;
}

function CodeForm({ done, back }: { done: (email: string, notice: string) => void; back: () => void }) {
  const { messages: m } = useI18n();
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ email: string; reset: boolean } | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const { error: invalid, password } = readPassword(form, m); if (invalid) { setError(invalid); return; }
    setBusy(true); setError("");
    try {
      const body = await send<{ email: string }>("/auth/recovery-code", { code: String(form.get("code") || "").trim(), ...(password ? { new_password: password } : {}) });
      setResult({ email: body.email, reset: Boolean(password) });
    } catch (reason) { setError(reason instanceof ApiError && reason.status < 500 ? m.recovery.codeFailed : m.recovery.unavailable); } finally { setBusy(false); }
  }
  if (result) return <AuthFrame title={m.recovery.title}>
    <div className="reveal"><span>{m.recovery.usernameIs}</span><strong dir="ltr">{result.email}</strong></div>
    <Notice tone="success">{result.reset ? m.recovery.passwordSaved : m.recovery.codeUsed}</Notice>
    <button type="button" onClick={() => done(result.email, result.reset ? m.recovery.passwordSaved : "")}>{m.recovery.signIn}</button>
  </AuthFrame>;
  return <AuthFrame title={m.recovery.codeOption} intro={m.recovery.codeOptionHint}>
    {error && <Notice tone="error">{error}</Notice>}
    <form onSubmit={(event) => void submit(event)}>
      <label>{m.recovery.code}<input className="code-input" name="code" dir="ltr" autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false} placeholder={m.recovery.codePlaceholder} minLength={12} maxLength={32} required /></label>
      <PasswordPair optional />
      <button disabled={busy} type="submit">{busy ? m.recovery.working : m.recovery.useCode}</button>
    </form>
    <BackLink onClick={back} />
  </AuthFrame>;
}

function EmailForm({ back }: { back: () => void }) {
  const { messages: m } = useI18n();
  const [sent, setSent] = useState(false); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError("");
    try { await send("/auth/password-reset/request", { email: form.get("email") }); setSent(true); }
    catch { setError(m.recovery.unavailable); } finally { setBusy(false); }
  }
  return <AuthFrame title={m.recovery.emailOption} intro={sent ? undefined : m.recovery.emailOptionHint}>
    {sent ? <Notice tone="success">{m.recovery.linkSent}</Notice> : <>
      {error && <Notice tone="error">{error}</Notice>}
      <form onSubmit={(event) => void submit(event)}>
        <label>{m.recovery.email}<input name="email" type="email" autoComplete="username" required /></label>
        <button disabled={busy} type="submit">{busy ? m.recovery.working : m.recovery.sendLink}</button>
      </form>
    </>}
    <BackLink onClick={back} />
  </AuthFrame>;
}

function ResetForm({ token, done }: { token: string; done: (notice: string) => void }) {
  const { messages: m } = useI18n();
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const { error: invalid, password } = readPassword(form, m); if (invalid) { setError(invalid); return; }
    setBusy(true); setError("");
    try { await send("/auth/password-reset/confirm", { token, new_password: password }); done(m.recovery.resetDone); }
    catch (reason) { setError(reason instanceof ApiError && reason.status < 500 ? m.recovery.resetFailed : m.recovery.unavailable); } finally { setBusy(false); }
  }
  return <AuthFrame title={m.recovery.resetTitle}>
    {error && <Notice tone="error">{error}</Notice>}
    <form onSubmit={(event) => void submit(event)}>
      <PasswordPair />
      <button disabled={busy} type="submit">{busy ? m.recovery.working : m.recovery.saveReset}</button>
    </form>
    <BackLink onClick={() => done("")} />
  </AuthFrame>;
}

export function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [token] = useState(resetToken);
  const [view, setView] = useState<View>(token ? "reset" : "sign-in");
  const [notice, setNotice] = useState(""); const [email, setEmail] = useState("");
  const [emailReset, setEmailReset] = useState(false);
  useEffect(() => {
    let active = true;
    fetch("/api/v1/auth/recovery-options").then((response) => response.ok ? response.json() as Promise<{ email_reset: boolean }> : null)
      .then((body) => { if (active && body) setEmailReset(Boolean(body.email_reset)); }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  function signIn(message = "", username = "") {
    // Drop the reset token from the address bar once it has been used or abandoned.
    if (window.location.hash.startsWith("#/reset-password")) history.replaceState(null, "", window.location.pathname + window.location.search);
    setNotice(message); if (username) setEmail(username); setView("sign-in");
  }
  if (view === "reset") return <ResetForm token={token} done={(message) => signIn(message)} />;
  if (view === "choose") return <Choose emailReset={emailReset} go={(next) => { setNotice(""); setView(next); }} />;
  if (view === "code") return <CodeForm done={(username, message) => signIn(message, username)} back={() => setView("choose")} />;
  if (view === "email") return <EmailForm back={() => setView("choose")} />;
  return <SignIn key={email} onLogin={onLogin} onForgot={() => setView("choose")} notice={notice} email={email} />;
}
