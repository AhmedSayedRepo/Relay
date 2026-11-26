import { useState } from "react";
import { Link } from "react-router-dom";
import { downloadExport, query } from "./api";
import { DateFields, PageHeading, Pager, Status } from "./components/Layout";
import { Bidi } from "./components/Product";
import { usePersisted } from "./filters";
import { cairoDate, useApi } from "./hooks";
import { useI18n } from "./i18n";
import type { Page } from "./types";

type AuditRow = { candidate_id: string; at: string; action: string; actor: string | null; automatic: boolean; rule: string | null; reason: string | null; amount: string | null; currency: string | null; sender: string | null };
type AuditPage = Page<AuditRow> & { counts?: { automatic: number; people: number }; actors?: string[] };

const TONES: Record<string, string> = { confirmed: "success", rejected: "danger", rejected_copy: "danger", sent_back: "warning", not_a_copy: "warning" };

/** Who confirmed, rejected or sent back each receipt, and when. */
export function AuditTrail({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const a = m.audit;
  const [dateFrom, setDateFrom] = usePersisted("day.dateFrom", () => cairoDate()); const [dateTo, setDateTo] = usePersisted("day.dateTo", () => cairoDate());
  const [timeFrom, setTimeFrom] = usePersisted("day.timeFrom", "00:00"); const [timeTo, setTimeTo] = usePersisted("day.timeTo", "23:59");
  const [actor, setActor] = usePersisted("audit.actor", ""); const [action, setAction] = usePersisted("audit.action", ""); const [offset, setOffset] = usePersisted("audit.offset", 0); const limit = 50;
  const [message, setMessage] = useState("");
  const filters = { date_from: dateFrom, date_to: dateTo, time_from: timeFrom || undefined, time_to: timeTo || undefined, actor: actor || undefined, action: action || undefined };
  const { data, error, loading } = useApi<AuditPage>("/audit-trail" + query({ ...filters, limit, offset }), token, true);
  const reset = (apply: () => void) => { apply(); setOffset(0); };
  const money = (row: AuditRow) => row.amount ? Number(row.amount).toLocaleString(locale === "ar" ? "ar-EG" : "en", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + (row.currency || "") : "—";
  const why = (row: AuditRow) => [row.rule ? a.rules[row.rule] ?? row.rule : "", row.reason ?? ""].filter(Boolean).join(" · ") || "—";
  async function exportExcel() { setMessage(m.reports.preparing); try { await downloadExport("/audit-trail/export.xlsx" + query({ ...filters, language: locale }), token); setMessage(m.reports.downloaded); } catch { setMessage(m.reports.failed); } }
  return <section><PageHeading title={m.pages.audit[0]} description={m.pages.audit[1]} help="audit-trail" />
    <div className="panel dash-filters"><div className="filter-bar"><DateFields dateFrom={dateFrom} dateTo={dateTo} setDateFrom={(value) => reset(() => setDateFrom(value))} setDateTo={(value) => reset(() => setDateTo(value))} />
      <label>{m.dashboard.fromTime}<input type="time" value={timeFrom} onChange={(e) => reset(() => setTimeFrom(e.target.value))} /></label>
      <label>{m.dashboard.toTime}<input type="time" value={timeTo} onChange={(e) => reset(() => setTimeTo(e.target.value))} /></label>
      <label>{a.by}<select value={actor} onChange={(e) => reset(() => setActor(e.target.value))}><option value="">{a.everyone}</option><option value="automatic">{a.automatic}</option>{(data?.actors ?? []).map((email) => <option key={email} value={email}>{email}</option>)}</select></label>
      <label>{a.action}<select value={action} onChange={(e) => reset(() => setAction(e.target.value))}><option value="">{a.allActions}</option>{Object.entries(a.actions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <button type="button" className="quiet-button" onClick={exportExcel}>{a.export}</button></div>
      <p className="hint">{a.hint}</p>{message && <p className="hint" role="status">{message}</p>}</div>
    {data && <div className="metric-grid">
      <article className="metric"><span>{a.total}</span><strong><Bidi>{data.total}</Bidi></strong></article>
      <article className="metric"><span>{a.relay}</span><strong><Bidi>{data.counts?.automatic ?? 0}</Bidi></strong></article>
      <article className="metric"><span>{a.people}</span><strong><Bidi>{data.counts?.people ?? 0}</Bidi></strong></article>
    </div>}
    <Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={a.empty} />
    {data && data.total > 0 && <div className="table-wrap"><table><thead><tr><th>{a.time}</th><th>{a.action}</th><th>{a.by}</th><th>{a.why}</th><th>{a.amount}</th><th>{a.sender}</th><th>{a.receipt}</th></tr></thead><tbody>{data.items.map((row) => <tr key={row.candidate_id + row.action + row.at}>
      <td data-label={a.time}><Bidi>{new Date(row.at).toLocaleString(locale === "ar" ? "ar-EG" : "en", { dateStyle: "medium", timeStyle: "short", timeZone: "Africa/Cairo" })}</Bidi></td>
      <td data-label={a.action}><span className={"badge " + (TONES[row.action] ?? "warning")}>{a.actions[row.action] ?? row.action}</span></td>
      <td data-label={a.by}>{row.automatic ? <span className="source-tag both">{a.automatic}</span> : <Bidi>{row.actor || "—"}</Bidi>}</td>
      <td data-label={a.why}><small>{why(row)}</small></td>
      <td data-label={a.amount}><Bidi>{money(row)}</Bidi></td>
      <td data-label={a.sender}><Bidi>{row.sender || "—"}</Bidi></td>
      <td data-label={a.receipt}><Link to={"/transactions/" + row.candidate_id}>{a.open}</Link></td>
    </tr>)}</tbody></table></div>}
    {data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}
  </section>;
}
