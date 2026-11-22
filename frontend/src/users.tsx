import { useState, type FormEvent } from "react";
import { api } from "./api";
import { PageHeading, Status } from "./components/Layout";
import { StatusBadge } from "./components/Product";
import { contextHelp } from "./help/topics";
import { useApi } from "./hooks";
import { useI18n } from "./i18n";
import type { Role } from "./role";

type UserRow = { id: string; email: string; role: Role; is_active: boolean; created_at: string; permissions?: Record<string, boolean>; granted?: string[] };
type PermissionList = { pages: string[]; actions: string[]; defaults: Record<string, string[]> };
const roles: Role[] = ["ADMIN", "REVIEWER", "VIEWER"];

export function Users({ token }: { token: string }) {
  const { messages: m } = useI18n(); const { data, error, loading, reload } = useApi<{ items: UserRow[] }>("/users", token);
  const [message, setMessage] = useState(""); const [failed, setFailed] = useState(false);
  const list = useApi<PermissionList>("/users/permissions", token).data; const [editing, setEditing] = useState("");
  async function run(action: () => Promise<unknown>, done: string) {
    setMessage(""); setFailed(false);
    try { await action(); setMessage(done); reload(); } catch (reason) { setMessage(reason instanceof Error ? reason.message : m.users.failed); setFailed(true); }
  }
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form);
    await run(() => api("/users", token, { method: "POST", body: JSON.stringify({ email: values.get("email"), role: values.get("role"), password: values.get("password") }) }), m.users.added);
    form.reset();
  }
  const update = (user: UserRow, body: Record<string, unknown>) => run(() => api("/users/" + user.id, token, { method: "PATCH", body: JSON.stringify(body) }), m.users.saved);
  function resetPassword(user: UserRow) {
    const password = window.prompt(m.users.newPasswordFor.replace("{email}", user.email));
    if (password) void update(user, { password });
  }
  function remove(user: UserRow) {
    if (window.confirm(m.users.confirmDelete.replace("{email}", user.email))) void run(() => api("/users/" + user.id, token, { method: "DELETE" }), m.users.deleted);
  }
  return <section><PageHeading title={m.pages.users[0]} description={m.pages.users[1]} help={contextHelp.help} />
    <article className="panel"><h2>{m.users.add}</h2><form className="filter-bar" onSubmit={(event) => void add(event)}>
      <label className="grow">{m.users.email}<input name="email" type="email" autoComplete="off" required maxLength={254} /></label>
      <label>{m.users.role}<select name="role" defaultValue="REVIEWER">{roles.map((role) => <option key={role} value={role}>{m.users.roles[role]}</option>)}</select></label>
      <label>{m.users.password}<input name="password" type="password" autoComplete="new-password" minLength={12} maxLength={1024} required /></label>
      <button type="submit">{m.users.addButton}</button>
    </form><p className="hint">{m.users.roleHint}</p></article>
    {editing && list && data?.items.filter((user) => user.id === editing).map((user) => <PermissionEditor key={user.id + user.role} user={user} list={list} save={(permissions) => update(user, { permissions })} close={() => setEditing("")} />)}
    {message && <p className={"status" + (failed ? " error" : "")} role={failed ? "alert" : "status"}>{message}</p>}
    <Status loading={loading} error={error} />
    {data && <div className="table-wrap"><table className="responsive-table"><thead><tr><th>{m.users.email}</th><th>{m.users.role}</th><th>{m.users.status}</th><th>{m.users.actions}</th></tr></thead><tbody>{data.items.map((user) => <tr key={user.id}>
      <td data-label={m.users.email}><span dir="ltr">{user.email}</span></td>
      <td data-label={m.users.role}><select aria-label={m.users.role + " " + user.email} value={user.role} onChange={(event) => void update(user, { role: event.target.value })}>{roles.map((role) => <option key={role} value={role}>{m.users.roles[role]}</option>)}</select></td>
      <td data-label={m.users.status}><StatusBadge value={user.is_active ? m.users.active : m.users.disabled} tone={user.is_active ? "success" : "neutral"} /></td>
      <td data-label={m.users.actions}><div className="button-row">
        <button className={"user-action " + (user.is_active ? "warn" : "ok")} type="button" onClick={() => void update(user, { is_active: !user.is_active })}>{user.is_active ? m.users.disable : m.users.enable}</button>
        <button className="user-action neutral" type="button" onClick={() => resetPassword(user)}>{m.users.resetPassword}</button>
        <button className="user-action info" type="button" aria-expanded={editing === user.id} onClick={() => setEditing(editing === user.id ? "" : user.id)}>{m.users.permissions}</button>
        <button className="user-action danger" type="button" onClick={() => remove(user)}>{m.users.delete}</button>
      </div></td>
    </tr>)}</tbody></table></div>}
  </section>;
}

/** Tick the pages and actions one user may use; only the differences from the role defaults are stored. */
function PermissionEditor({ user, list, save, close }: { user: UserRow; list: PermissionList; save: (permissions: Record<string, boolean>) => Promise<unknown>; close: () => void }) {
  const { messages: m } = useI18n(); const u = m.users; const defaults = new Set(list.defaults[user.role] ?? []);
  const start = new Set(user.granted ?? [...defaults]); const [ticked, setTicked] = useState(start);
  const admin = user.role === "ADMIN";
  const flip = (key: string) => setTicked((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  const changes = () => Object.fromEntries([...list.pages, ...list.actions].filter((key) => ticked.has(key) !== defaults.has(key)).map((key) => [key, ticked.has(key)]));
  const group = (title: string, keys: string[]) => <fieldset className="permission-group" disabled={admin}><legend>{title}</legend>{keys.map((key) => <label key={key} className="check-row"><input type="checkbox" checked={admin || ticked.has(key)} onChange={() => flip(key)} /><span>{u.keys[key] ?? key}</span>{!admin && ticked.has(key) !== defaults.has(key) && <small className="hint"> · {u.changed}</small>}</label>)}</fieldset>;
  return <article className="panel permission-editor"><h2>{u.permissions}: <span dir="ltr">{user.email}</span> · {u.roles[user.role]}</h2>
    {admin && <p className="hint">{u.adminAll}</p>}
    <div className="permission-grid">{group(u.pagesTitle, list.pages)}{group(u.actionsTitle, list.actions)}</div>
    {!admin && <div className="button-row"><button type="button" onClick={() => void save(changes()).then(close)}>{u.savePermissions}</button><button type="button" className="quiet-button" onClick={() => setTicked(new Set(defaults))}>{u.resetPermissions}</button><button type="button" className="quiet-button" onClick={close}>{m.common.close}</button></div>}
  </article>;
}
