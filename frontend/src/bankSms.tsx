import { useState } from "react";
import { Link } from "react-router-dom";
import { query } from "./api";
import { DateFields, PageHeading, Pager, Status } from "./components/Layout";
import { Bidi } from "./components/Product";
import { contextHelp } from "./help/topics";
import { cairoDate, useApi } from "./hooks";
import { useI18n } from "./i18n";
import type { Json, Page } from "./types";

type BankSmsRow = { event_id: string; device_id: string; device_name: string; sender: string | null; occurred_at: string; amount: string | null; currency: string | null; status: "MATCHED" | "UNUSED" | "NO_AMOUNT"; candidate_id: string | null };
type BankSmsPage = Page<BankSmsRow> & { counts?: Record<string, number>; unused_totals?: { currency: string; amount: string }[] };

/** Every bank SMS the SMS phone received, and whether it confirmed a receipt. */
export function BankSms({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const b = m.bankSms;
  const [dateFrom, setDateFrom] = useState(() => cairoDate()); const [dateTo, setDateTo] = useState(() => cairoDate());
  const [timeFrom, setTimeFrom] = useState("00:00"); const [timeTo, setTimeTo] = useState("23:59");
  const [device, setDevice] = useState(""); const [status, setStatus] = useState(""); const [offset, setOffset] = useState(0); const limit = 50;
  const filters = { date_from: dateFrom, date_to: dateTo, time_from: timeFrom || undefined, time_to: timeTo || undefined, device_id: device || undefined };
  const { data, error, loading } = useApi<BankSmsPage>("/bank-sms" + query({ ...filters, status: status || undefined, limit, offset }), token);
  const phones = useApi<Page<Record<string, Json>>>("/operations/devices?limit=100", token).data?.items ?? [];
  const labels: Record<string, string> = { MATCHED: b.matched, UNUSED: b.unused, NO_AMOUNT: b.noAmount };
  const tone: Record<string, string> = { MATCHED: "success", UNUSED: "warning", NO_AMOUNT: "neutral" };
  const money = (amount: string | null, currency: string | null) => amount ? Number(amount).toLocaleString(locale === "ar" ? "ar-EG" : "en", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + (currency || "") : "—";
  const reset = (apply: () => void) => { apply(); setOffset(0); };
  return <section><PageHeading title={m.pages.bankSms[0]} description={m.pages.bankSms[1]} help={contextHelp.devices} />
    <div className="panel dash-filters"><div className="filter-bar"><DateFields dateFrom={dateFrom} dateTo={dateTo} setDateFrom={(value) => reset(() => setDateFrom(value))} setDateTo={(value) => reset(() => setDateTo(value))} />
      <label>{m.dashboard.fromTime}<input type="time" value={timeFrom} onChange={(e) => reset(() => setTimeFrom(e.target.value))} /></label>
      <label>{m.dashboard.toTime}<input type="time" value={timeTo} onChange={(e) => reset(() => setTimeTo(e.target.value))} /></label>
      <label>{b.phone}<select value={device} onChange={(e) => reset(() => setDevice(e.target.value))}><option value="">{b.allPhones}</option>{phones.map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.name ?? "")}</option>)}</select></label>
      <label>{b.status}<select value={status} onChange={(e) => reset(() => setStatus(e.target.value))}><option value="">{b.allStatuses}</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label></div>
      <p className="hint">{b.hint}</p></div>
    {data && <div className="metric-grid">
      {(["MATCHED", "UNUSED", "NO_AMOUNT"] as const).map((key) => <article key={key} className="metric"><span>{labels[key]}</span><strong><Bidi>{Number(data.counts?.[key]) || 0}</Bidi></strong></article>)}
      <article className="metric money"><span>{b.unusedMoney}</span><strong><Bidi>{(data.unused_totals ?? []).map((item) => money(item.amount, item.currency)).join(" · ") || "0"}</Bidi></strong></article>
    </div>}
    <Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={b.empty} />
    {data && data.total > 0 && <div className="table-wrap"><table><thead><tr><th>{b.time}</th><th>{b.phone}</th><th>{b.sender}</th><th>{b.amount}</th><th>{b.status}</th><th>{b.receipt}</th></tr></thead><tbody>{data.items.map((row) => <tr key={row.event_id}>
      <td data-label={b.time}><Bidi>{new Date(row.occurred_at).toLocaleString(locale === "ar" ? "ar-EG" : "en", { dateStyle: "medium", timeStyle: "short", timeZone: "Africa/Cairo" })}</Bidi></td>
      <td data-label={b.phone}>{row.device_name}</td>
      <td data-label={b.sender}><Bidi>{row.sender || "—"}</Bidi></td>
      <td data-label={b.amount}><Bidi>{money(row.amount, row.currency)}</Bidi></td>
      <td data-label={b.status}><span className={"badge " + tone[row.status]}>{labels[row.status]}</span></td>
      <td data-label={b.receipt}>{row.candidate_id ? <Link to={"/transactions/" + row.candidate_id}>{b.openReceipt}</Link> : "—"}</td>
    </tr>)}</tbody></table></div>}
    {data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}
  </section>;
}
