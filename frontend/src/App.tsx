import { useEffect, useState } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import { Account } from "./account";
import { api } from "./api";
import { Login } from "./auth";
import { Layout } from "./components/Layout";
import { BankSms } from "./bankSms";
import { EmailSettingsPage } from "./emailSettings";
import { endSession, resumeSession, useSessionKeepAlive } from "./session";
import { Dashboard, Devices, DuplicateDetail, Duplicates, HelpPage, ProcessingQueue, Reports, ReviewQueue, TransactionDetail, Transactions } from "./pages";
import { RoleContext, isAdmin, type Role } from "./role";
import { Users } from "./users";

export default function App() {
  const [token, setToken] = useState("");
  const [role, setRole] = useState<Role>("ADMIN");
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
    api<{ role?: Role }>("/auth/me", token).then((user) => { if (live && user.role) setRole(user.role); }).catch(() => undefined);
    return () => { live = false; };
  }, [token]);
  if (!token) return <Login onLogin={setToken} />;
  return <RoleContext.Provider value={role}><HashRouter><Routes><Route element={<Layout signOut={() => { endSession(); setToken(""); }} />}><Route index element={<Dashboard token={token} />} /><Route path="transactions" element={<Transactions token={token} />} /><Route path="transactions/:id" element={<TransactionDetail token={token} />} /><Route path="duplicates" element={<Duplicates token={token} />} /><Route path="duplicates/:id" element={<DuplicateDetail token={token} />} /><Route path="review" element={<ReviewQueue token={token} />} /><Route path="bank-sms" element={<BankSms token={token} />} /><Route path="processing" element={<ProcessingQueue token={token} />} /><Route path="reports" element={<Reports token={token} />} />{isAdmin(role) && <Route path="devices" element={<Devices token={token} />} />}{isAdmin(role) && <Route path="users" element={<Users token={token} />} /><Route path="email-settings" element={<EmailSettingsPage token={token} />} />}<Route path="account" element={<Account token={token} />} /><Route path="help" element={<HelpPage />} /></Route></Routes></HashRouter></RoleContext.Provider>;
}
