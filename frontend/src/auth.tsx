import { FormEvent, useEffect, useState, type ReactNode } from "react";
import { ApiError, send } from "./api";
import { Icon, RelayMark } from "./components/Brand";
import { AppsLink, PreferenceControls, RailArt } from "./components/Layout";
import { useI18n } from "./i18n";

type View = "sign-in" | "email" | "reset";

function resetToken(): string {
  const match = window.location.hash.match(/^#\/reset-password\?token=([A-Za-z0-9_-]{20,128})$/);
  return match ? match[1] : "";
}

function AuthFrame({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  const { messages: m } = useI18n();
  return <main className="login-page" id="main-content">
    <section className="login-brand" aria-hidden="true"><RailArt className="login-art" /><RelayMark size={64} /><p>{m.common.secureEvidence}</p></section>
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

/** Bridge from the Relay Operator app: sign-in details kept behind the fingerprint. */
type RelayAuth = { hasSaved(): boolean; unlock(): void; offerSave(email: string, password: string): void };
const appAuth = () => (window as { RelayAuth?: RelayAuth }).RelayAuth;
const REMEMBER_KEY = "relay.remember";

function SignIn({ onLogin, onForgot, notice, email }: { onLogin: (token: string) => void; onForgot: () => void; notice: string; email: string }) {
  const { messages: m } = useI18n();
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [shown, setShown] = useState(false);
  const [remember, setRemember] = useState(() => { try { return localStorage.getItem(REMEMBER_KEY) !== "0"; } catch { return true; } });
  const bridge = appAuth(); const [saved] = useState(() => { try { return Boolean(bridge?.hasSaved()); } catch { return false; } });
  async function login(address: string, password: string, keep: boolean, offer: boolean) {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: address, password, remember: keep }) });
      if (response.status === 429) { setError(m.auth.tooMany); return; }
      if (!response.ok) throw new Error();
      try { localStorage.setItem(REMEMBER_KEY, keep ? "1" : "0"); } catch { /* storage off */ }
      // In the app, offer to keep these details behind the fingerprint.
      if (offer && bridge && !saved) { try { bridge.offerSave(address, password); } catch { /* app without fingerprint */ } }
      const body = await response.json() as { access_token: string }; onLogin(body.access_token);
    } catch { setError(m.auth.failed); } finally { setBusy(false); }
  }
  useEffect(() => {
    // The app calls this after the fingerprint unlocks the saved details.
    const target = window as { __relayCredentials?: (address: string, password: string) => void };
    target.__relayCredentials = (address, password) => void login(address, password, true, false);
    return () => { delete target.__relayCredentials; };
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    void login(String(form.get("email") ?? ""), String(form.get("password") ?? ""), remember, true);
  }
  return <AuthFrame title={m.auth.welcome} intro={m.auth.intro}>
    {notice && <Notice tone="success">{notice}</Notice>}{error && <Notice tone="error">{error}</Notice>}
    {saved && <button className="fingerprint-button" type="button" disabled={busy} onClick={() => { try { bridge?.unlock(); } catch { setError(m.auth.failed); } }}><svg aria-hidden="true" viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M12 11v3a6 6 0 0 1-1.2 3.6" /><path d="M8.5 9.5A3.5 3.5 0 0 1 15.5 10v3" /><path d="M5.5 8a7 7 0 0 1 13 3.5V13" /><path d="M8 13.5V16" /><path d="M15.5 16.5c-.3 1-.7 1.9-1.3 2.7" /></svg>{m.auth.fingerprint}</button>}
    <form onSubmit={submit}>
      <label>{m.auth.email}<input name="email" type="email" autoComplete="username" defaultValue={email} required /></label>
      <label>{m.auth.password}<span className="password-field"><input name="password" type={shown ? "text" : "password"} autoComplete="current-password" required /><button type="button" className="reveal-button" aria-pressed={shown} aria-label={shown ? m.auth.hidePassword : m.auth.showPassword} title={shown ? m.auth.hidePassword : m.auth.showPassword} onClick={() => setShown((value) => !value)}><svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="2.8" />{shown && <path d="M4 4l16 16" />}</svg></button></span></label>
      <label className="toggle-row remember-row"><input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />{m.auth.remember}</label>
      <button disabled={busy} type="submit">{busy ? m.auth.signingIn : m.auth.signIn}</button>
    </form>
    <button className="text-button forgot" type="button" onClick={onForgot}>{m.recovery.forgot}</button>
    <p className="pilot-note">{m.auth.pilotNote}</p>
    <AppsLink />
  </AuthFrame>;
}

function EmailForm({ back, available }: { back: () => void; available: boolean }) {
  const { messages: m } = useI18n();
  const [sent, setSent] = useState(false); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError("");
    try { await send("/auth/password-reset/request", { email: form.get("email") }); setSent(true); }
    catch { setError(m.recovery.unavailable); } finally { setBusy(false); }
  }
  if (!available) return <AuthFrame title={m.recovery.title}>
    <Notice tone="error">{m.recovery.noEmail}</Notice>
    <BackLink onClick={back} />
  </AuthFrame>;
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
  if (view === "email") return <EmailForm available={emailReset} back={() => setView("sign-in")} />;
  return <SignIn key={email} onLogin={onLogin} onForgot={() => { setNotice(""); setView("email"); }} notice={notice} email={email} />;
}
