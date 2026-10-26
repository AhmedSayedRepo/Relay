import { FormEvent, useState } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import { Layout, PreferenceControls } from "./components/Layout";
import { useI18n } from "./i18n";
import { Dashboard, Devices, DuplicateDetail, Duplicates, HelpPage, ProcessingQueue, Reports, ReviewQueue, TransactionDetail, Transactions } from "./pages";

function Login({ onLogin }: { onLogin: (token: string) => void }) {
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
  return <main className="login-page" id="main-content"><section className="login-card" aria-labelledby="login-title"><div className="login-top"><div className="brand large"><span aria-hidden="true">R</span><b>{m.common.appName}</b></div><PreferenceControls compact /></div><p className="eyebrow">{m.auth.eyebrow}</p><h1 id="login-title">{m.auth.welcome}</h1><p>{m.auth.intro}</p>{error && <p className="status error" role="alert">{error}</p>}<form onSubmit={(event) => void submit(event)}><label>{m.auth.email}<input name="email" type="email" autoComplete="username" required /></label><label>{m.auth.password}<input name="password" type="password" autoComplete="current-password" required /></label><button disabled={busy} type="submit">{busy ? m.auth.signingIn : m.auth.signIn}</button></form><p className="pilot-note">{m.auth.pilotNote}</p></section></main>;
}

export default function App() {
  const [token, setToken] = useState("");
  if (!token) return <Login onLogin={setToken} />;
  return <HashRouter><Routes><Route element={<Layout signOut={() => setToken("")} />}><Route index element={<Dashboard token={token} />} /><Route path="transactions" element={<Transactions token={token} />} /><Route path="transactions/:id" element={<TransactionDetail token={token} />} /><Route path="duplicates" element={<Duplicates token={token} />} /><Route path="duplicates/:id" element={<DuplicateDetail token={token} />} /><Route path="review" element={<ReviewQueue token={token} />} /><Route path="processing" element={<ProcessingQueue token={token} />} /><Route path="reports" element={<Reports token={token} />} /><Route path="devices" element={<Devices token={token} />} /><Route path="help" element={<HelpPage />} /></Route></Routes></HashRouter>;
}
