import { useState } from "react";
import { HashRouter, Route, Routes } from "react-router-dom";
import { Account } from "./account";
import { Login } from "./auth";
import { Layout } from "./components/Layout";
import { useSessionKeepAlive } from "./session";
import { Dashboard, Devices, DuplicateDetail, Duplicates, HelpPage, ProcessingQueue, Reports, ReviewQueue, TransactionDetail, Transactions } from "./pages";

export default function App() {
  const [token, setToken] = useState("");
  useSessionKeepAlive(token, setToken);
  if (!token) return <Login onLogin={setToken} />;
  return <HashRouter><Routes><Route element={<Layout signOut={() => setToken("")} />}><Route index element={<Dashboard token={token} />} /><Route path="transactions" element={<Transactions token={token} />} /><Route path="transactions/:id" element={<TransactionDetail token={token} />} /><Route path="duplicates" element={<Duplicates token={token} />} /><Route path="duplicates/:id" element={<DuplicateDetail token={token} />} /><Route path="review" element={<ReviewQueue token={token} />} /><Route path="processing" element={<ProcessingQueue token={token} />} /><Route path="reports" element={<Reports token={token} />} /><Route path="devices" element={<Devices token={token} />} /><Route path="account" element={<Account token={token} />} /><Route path="help" element={<HelpPage />} /></Route></Routes></HashRouter>;
}
