import { useEffect, useState } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import { Account } from "./account";
import { api } from "./api";
import { Login } from "./auth";
import { Layout } from "./components/Layout";
import { AuditTrail } from "./audit";
import { BankSms } from "./bankSms";
import { EmailSettingsPage } from "./emailSettings";
import { endSession, resumeSession, useSessionKeepAlive } from "./session";
import { clearFilters } from "./filters";
import { CompareReceipts, Dashboard, Devices, DuplicateDetail, Duplicates, HelpPage, ProcessingQueue, Reports, ReviewQueue, SetAside, TransactionDetail, Transactions } from "./pages";
import { RoleContext, can, type Access, type Role } from "./role";
import { Users } from "./users";

export default function App() {
  const [token, setToken] = useState("");
  const [role, setRole] = useState<Access>({ role: "ADMIN", granted: [] });
  useSessionKeepAlive(token, setToken);
  useEffect(() => {
    // A reload keeps the operator signed in through the httpOnly session cookie.
    let live = true;
    void resumeSession().then((value) => { if (live && value) setToken((current) => current || value); });
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!token) return;
    let live = true;
    api<{ role?: Role; granted?: string[] }>("/auth/me", token).then((user) => { if (live && user.role) setRole({ role: user.role, granted: Array.isArray(user.granted) ? user.granted : [] }); }).catch(() => undefined);
    return () => { live = false; };
  }, [token]);
  if (!token) return <Login onLogin={(value) => { clearFilters(); setToken(value); }} />;
  return <RoleContext.Provider value={role}><HashRouter><Routes><Route element={<Layout signOut={() => { endSession(); clearFilters(); setToken(""); }} />}><Route index element={<Dashboard token={token} />} /><Route path="transactions" element={<Transactions token={token} />} /><Route path="transactions/:id" element={<TransactionDetail token={token} />} /><Route path="rejected" element={<SetAside token={token} />} /><Route path="compare/:id" element={<CompareReceipts token={token} />} /><Route path="duplicates" element={<Duplicates token={token} />} /><Route path="duplicates/:id" element={<DuplicateDetail token={token} />} /><Route path="review" element={<ReviewQueue token={token} />} /><Route path="bank-sms" element={can(role, "page.bank_sms") ? <BankSms token={token} /> : <Dashboard token={token} />} /><Route path="audit" element={can(role, "page.audit") ? <AuditTrail token={token} /> : <Dashboard token={token} />} /><Route path="processing" element={<ProcessingQueue token={token} />} /><Route path="reports" element={<Reports token={token} />} />{can(role, "page.devices") && <Route path="devices" element={<Devices token={token} />} />}{can(role, "page.users") && <Route path="users" element={<Users token={token} />} />}{can(role, "page.email_settings") && <Route path="email-settings" element={<EmailSettingsPage token={token} />} />}<Route path="account" element={<Account token={token} />} /><Route path="help" element={<HelpPage />} /></Route></Routes></HashRouter></RoleContext.Provider>;
}
