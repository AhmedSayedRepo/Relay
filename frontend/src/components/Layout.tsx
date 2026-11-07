import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useI18n, type Locale } from "../i18n";
import { canReview, isAdmin, useRole } from "../role";
import { useTheme, type Theme } from "../preferences";
import { Icon, RelayMark } from "./Brand";
import { HelpDialog } from "./HelpDialog";

const routes = ["dashboard", "transactions", "duplicates", "review", "processing", "reports", "devices", "users", "account", "help"] as const;
const paths = ["/", "/transactions", "/duplicates", "/review", "/processing", "/reports", "/devices", "/users", "/account", "/help"];
const adminOnly = new Set<string>(["devices", "users"]);

export function PreferenceControls({ compact = false }: { compact?: boolean }) {
  const { locale, setLocale, messages: m } = useI18n(); const { theme, setTheme } = useTheme();
  return <div className={"preferences" + (compact ? " compact" : "")}>
    <label><span>{m.preferences.language}</span><select aria-label={m.preferences.switchLanguage} value={locale} onChange={(e) => setLocale(e.target.value as Locale)}><option value="en">English</option><option value="ar">العربية</option></select></label>
    <label><span>{m.preferences.theme}</span><select aria-label={m.preferences.switchTheme} value={theme} onChange={(e) => setTheme(e.target.value as Theme)}><option value="system">{m.preferences.system}</option><option value="light">{m.preferences.light}</option><option value="dark">{m.preferences.dark}</option></select></label>
  </div>;
}

export function Layout({ signOut }: { signOut: () => void }) {
  const { messages: m } = useI18n(); const [open, setOpen] = useState(false); const role = useRole();
  const visible = (key: string) => (!adminOnly.has(key) || isAdmin(role)) && (key !== "review" || canReview(role));
  return <div className="app-shell"><a className="skip-link" href="#main-content">{m.common.skip}</a>
    <aside className={"side-rail" + (open ? " open" : "")}><NavLink className="brand" to="/" aria-label={m.common.appName + " " + m.navigation.dashboard} onClick={() => setOpen(false)}><RelayMark /><b>{m.common.appName}</b></NavLink><p className="rail-caption">{m.common.secureEvidence}</p><nav className="primary-nav" aria-label={m.common.workspace}>{routes.map((key, index) => visible(key) && <NavLink end={index === 0} key={key} to={paths[index]} onClick={() => setOpen(false)}><Icon name={key} /><span>{m.navigation[key]}</span></NavLink>)}</nav><div className="signal-mark" aria-hidden="true"><span /><span /><span /></div></aside>
    <header className="topbar"><button className="menu-button" type="button" aria-label={m.common.menu} aria-expanded={open} onClick={() => setOpen((value) => !value)}><Icon name="menu" /></button><div className="top-context"><strong>{m.common.workspace}</strong><span>{m.common.operator}</span><span className="role-chip">{m.users.roles[role]}</span></div><div className="top-actions"><PreferenceControls /><HelpDialog /><button className="quiet-button" onClick={signOut} type="button">{m.common.signOut}</button></div></header>
    {open && <button className="rail-scrim" aria-label={m.common.close} onClick={() => setOpen(false)} />}
    <main id="main-content"><Outlet /></main>
  </div>;
}

export function PageHeading({ title, description, help }: { title: string; description: string; help: string }) {
  return <div className="page-heading"><div><h1>{title}</h1><p>{description}</p></div><HelpDialog topicId={help} /></div>;
}

export function Status({ loading, error, empty = false, emptyMessage }: { loading: boolean; error: string; empty?: boolean; emptyMessage?: string }) {
  const { messages: m } = useI18n();
  if (loading) return <div className="status loading" role="status"><span aria-hidden="true" />{m.state.loading}</div>;
  if (error) { const message = error === "AUTH" ? m.state.authExpired : error === "UNAVAILABLE" ? m.state.unavailable : m.state.genericError; return <div className="status error" role="alert"><strong>{m.state.loadError}</strong><p>{message}</p></div>; }
  if (empty) return <div className="status empty"><strong>{emptyMessage || m.state.noResults}</strong><p>{m.state.noResultsAction}</p></div>;
  return null;
}

export function DateFields({ dateFrom, dateTo, setDateFrom, setDateTo }: { dateFrom: string; dateTo: string; setDateFrom: (v: string) => void; setDateTo: (v: string) => void }) {
  const { messages: m } = useI18n(); return <><label>{m.filters.from}<input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label><label>{m.filters.to}<input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label></>;
}

export function Pager({ total, limit, offset, setOffset }: { total: number; limit: number; offset: number; setOffset: (value: number) => void }) {
  const { messages: m } = useI18n(); const range = total ? String(offset + 1) + "–" + String(Math.min(total, offset + limit)) + " " + m.common.of + " " + String(total) : "0 " + m.common.records;
  return <nav className="pager" aria-label={m.common.page}><span dir="ltr">{range}</span><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>{m.common.previous}</button><button type="button" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>{m.common.next}</button></nav>;
}
