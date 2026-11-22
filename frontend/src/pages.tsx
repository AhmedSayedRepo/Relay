import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, downloadExport, query } from "./api";
import { DateFields, PageHeading, Pager, Status } from "./components/Layout";
import { Bidi, ReceiptJourney, StatusBadge } from "./components/Product";
import { AddDevice, DeviceActions } from "./components/DeviceAdmin";
import { Icon } from "./components/Brand";
import { ExceptionReview, ExceptionSummary } from "./components/ExceptionReview";
import { evidenceFirst } from "./evidence";
import { contextHelp, getHelpTopics } from "./help/topics";
import { cairoDate, defaultDates, useApi } from "./hooks";
import { formatMessage, useI18n, type Locale } from "./i18n";
import type { Messages } from "./i18n/en";
import type { DashboardData, DuplicateGroup, Job, Json, Page, PhoneHealth, Transaction } from "./types";
import { can, canReview, useRole } from "./role";
import { usePersisted } from "./filters";

const dash = "—";
function formatAmount(amount: string | null, currency: string | null, locale: Locale) {
  if (!amount || !currency) return dash;
  const number = Number(amount);
  try { return Number.isFinite(number) ? new Intl.NumberFormat(locale === "ar" ? "ar-EG" : "en", { style: "currency", currency, currencyDisplay: "code" }).format(number) : amount + " " + currency; }
  catch { return amount + " " + currency; }
}
function stamp(value: string | null | undefined, locale: Locale) { return value ? new Date(value).toLocaleString(locale === "ar" ? "ar-EG" : "en", { dateStyle: "medium", timeStyle: "short" }) : dash; }
function display(value: Json | undefined) { return value === null || value === undefined ? dash : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value); }
function Amount({ amount, currency, locale }: { amount: string | null; currency: string | null; locale: Locale }) { return <Bidi>{formatAmount(amount, currency, locale)}</Bidi>; }
function Timestamp({ value, locale }: { value: string | null | undefined; locale: Locale }) { return <Bidi>{stamp(value, locale)}</Bidi>; }

function ProtectedImage({ path, token, alt, polygon = [] }: { path: string; token: string; alt: string; polygon?: number[][] }) {
  const { messages: m } = useI18n(); const [url, setUrl] = useState(""); const [failed, setFailed] = useState(false);
  const [size, setSize] = useState([0, 0]);
  useEffect(() => {
    let current = true; let objectUrl = "";
    fetch(path, { headers: { Authorization: "Bearer " + token } })
      .then((response) => { if (!response.ok) throw new Error(); return response.blob(); })
      .then((blob) => { if (current) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(() => current && setFailed(true));
    return () => { current = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [path, token]);
  if (failed) return <p className="status error" role="alert">{m.detail.imageFailed}</p>;
  if (!url) return <div className="image-placeholder" role="status">{m.detail.imageLoading}</div>;
  const safePolygon = polygon.length >= 3 && polygon.every((point) => point.length === 2 && point.every(Number.isFinite) && point[0] >= 0 && point[1] >= 0 && point[0] <= size[0] && point[1] <= size[1]);
  return <div className="receipt-region"><img className="receipt-image" src={url} alt={alt} onLoad={(event) => setSize([event.currentTarget.naturalWidth, event.currentTarget.naturalHeight])} />{safePolygon && size[0] > 0 && <svg aria-label={m.exceptions.region} role="img" viewBox={`0 0 ${size[0]} ${size[1]}`} preserveAspectRatio="none"><polygon points={polygon.map((point) => point.join(",")).join(" ")} /></svg>}</div>;
}

type Money = { currency: string; amount: string | null; transaction_count: number }[];
function MoneyValue({ totals, locale }: { totals: Money | undefined; locale: Locale }) {
  const list = Array.isArray(totals) ? totals : [];
  if (!list.length) return <Bidi>0</Bidi>;
  return <>{list.map((item) => <span className="money-line" key={item.currency}><Amount amount={item.amount} currency={item.currency} locale={locale} /></span>)}</>;
}

/** A dashboard number; the whole card opens its list when `to` is set. */
function MetricCard({ to, tone = "", label, value, hint, tip }: { to: string; tone?: string; label: string; value: ReactNode; hint?: ReactNode; tip?: string }) {
  const body = <><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</>;
  return to ? <Link className={"metric metric-link " + tone} to={to} title={tip}>{body}</Link> : <article className={"metric " + tone} title={tip}>{body}</article>;
}

type Alert = { key: string; kind: "silent_phone" | "sms_without_receipt" | "old_receipt"; id?: string; device?: string; amount?: string | null; currency?: string; reference?: string | null; since?: string | null; health?: PhoneHealth | null; reason?: "reference" | "date" | null; paid_on?: string | null; first_seen?: string | null };

const cairoDay = (iso: string, locale: Locale) => new Date(iso).toLocaleDateString(locale === "ar" ? "ar-EG" : "en-GB", { day: "numeric", month: "short", timeZone: "Africa/Cairo" });
const cairoClock = (iso: string | null | undefined, locale: Locale) => iso ? new Date(iso).toLocaleTimeString(locale === "ar" ? "ar-EG" : "en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Africa/Cairo" }) : "";

/** "5 min ago" / "منذ ٥ دقائق", or "" when never. */
function ago(iso: string | null | undefined, locale: Locale): string {
  if (!iso) return "";
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60_000);
  const words = new Intl.RelativeTimeFormat(locale === "ar" ? "ar-EG" : "en", { numeric: "auto" });
  if (Math.abs(minutes) < 60) return words.format(minutes, "minute");
  if (Math.abs(minutes) < 60 * 24) return words.format(Math.round(minutes / 60), "hour");
  return words.format(Math.round(minutes / 1440), "day");
}

/** What the phone's app says about why it was quiet: current risks first, then the last silence. */
function phoneNotes(health: PhoneHealth | null | undefined, d: Messages["dashboard"], locale: Locale): string[] {
  if (!health) return [];
  const notes = (health.risks ?? []).map((code) => d.phoneRisks[code]).filter(Boolean);
  const gap = health.gap;
  if (gap && d.phoneGaps[gap.reason]) notes.push(formatMessage(d.phoneGapLine, { reason: d.phoneGaps[gap.reason], time: cairoClock(gap.at ?? gap.to, locale) }));
  if (health.last_crash?.error) notes.push(formatMessage(d.phoneCrash, { time: cairoClock(health.last_crash.at, locale), error: health.last_crash.error }));
  return notes;
}

/** Green within the hour, amber after, grey when never. */
const freshness = (iso: string | null | undefined) => !iso ? "never" : Date.now() - Date.parse(iso) < 3_600_000 ? "fresh" : "stale";

// Today's warnings (a silent cashier phone, a bank SMS with no receipt), beside
// the review workload. Always "today", whatever range the dashboard shows.
function Alerts({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const d = m.dashboard;
  const alerts = useApi<{ alerts: Alert[] }>("/alerts", token, true).data?.alerts ?? [];
  const money = (item: Alert) => Number(item.amount ?? 0).toLocaleString(locale === "ar" ? "ar-EG" : "en-US", { minimumFractionDigits: 2 }) + " " + (item.currency ?? "");
  return <article className={"panel alerts-panel" + (alerts.length ? " has-alerts" : "")} aria-live="polite">
    <div className="panel-head"><h2>{d.alertsTitle}</h2>{alerts.length > 0 ? <span className="count-pill danger"><Bidi>{alerts.length}</Bidi></span> : null}</div>
    {alerts.length === 0 ? <div className="all-clear"><Icon name="review" size={28} /><p>{d.alertsNone}</p></div> : <>
      <p className="subhead">{d.alertsResets}</p>
      <ul className="alerts-list">{alerts.map((item) => item.kind === "old_receipt"
        ? <li key={item.key} className="alert-item old"><span className="alert-icon"><Icon name="review" size={18} /></span><div><strong><Link to={"/transactions/" + item.id}>{d.alertOldTitle}{item.amount ? <> · <Bidi>{money(item)}</Bidi></> : null}</Link></strong><span>{formatMessage(d.alertOldMeta, { phone: item.device ?? "", time: cairoClock(item.since, locale) })}</span><span>{item.reason === "reference" && item.first_seen ? formatMessage(d.alertOldReference, { day: cairoDay(item.first_seen, locale) }) : item.paid_on ? formatMessage(d.alertOldDate, { day: cairoDay(item.paid_on + "T12:00:00Z", locale) }) : null}</span><small>{d.alertOldTip}</small></div></li>
        : item.kind === "silent_phone"
        ? <li key={item.key} className="alert-item phone"><span className="alert-icon"><Icon name="devices" size={18} /></span><div><strong>{item.device}</strong><span>{item.since ? formatMessage(d.alertSilentMeta, { time: cairoClock(item.since, locale) }) : d.alertSilentNeverMeta}</span>{phoneNotes(item.health, d, locale).map((note) => <small key={note} className="health-note">{note}</small>)}<small>{d.alertSilentTip}</small></div></li>
        : <li key={item.key} className="alert-item sms"><span className="alert-icon"><Icon name="bankSms" size={18} /></span><div><strong><Bidi>{money(item)}</Bidi></strong><span>{formatMessage(d.alertSmsMeta, { time: cairoClock(item.since, locale) })}</span><small>{d.alertSmsTip}</small></div></li>)}</ul></>}
  </article>;
}

const QUEUE_TONES = { ready: "success", missing: "warning", duplicate: "duplicate", in_progress: "accent", done: "neutral" } as const;

/** The review workload: one bar for the mix, then a clickable row per kind. */
function Workload({ queue, waiting, sms, unused, role }: { queue: (readonly [keyof typeof QUEUE_TONES, number])[]; waiting: number; sms: Record<string, number>; unused: number; role: ReturnType<typeof useRole> }) {
  const { messages: m } = useI18n(); const d = m.dashboard; const total = queue.reduce((sum, [, value]) => sum + value, 0);
  const row = (key: keyof typeof QUEUE_TONES, value: number) => {
    const body = <><span className={"dot " + QUEUE_TONES[key]} aria-hidden="true" /><span className="label">{m.review.categories[key]}</span><Bidi className="value">{value}</Bidi></>;
    return <li key={key} className={value ? "" : "zero"} title={d.tips.queue[key]}>{can(role, "page.review") ? <Link to={"/review?category=" + key}>{body}</Link> : <div>{body}</div>}</li>;
  };
  return <article className="panel workload-panel">
    <div className="panel-head"><h2>{d.workloadTitle}</h2><span className="count-pill"><Bidi>{waiting}</Bidi></span></div>
    <p className="subhead">{formatMessage(d.workloadIntro, { count: waiting })}</p>
    <div className="mix-bar" aria-hidden="true">{total > 0 ? queue.filter(([, value]) => value > 0).map(([key, value]) => <span key={key} className={QUEUE_TONES[key]} style={{ flexGrow: value }} />) : <span className="neutral" style={{ flexGrow: 1 }} />}</div>
    <ul className="queue-rows">{queue.map(([key, value]) => row(key, value))}</ul>
    <h3>{d.smsTitle}</h3>
    <div className="sms-tiles">{(["VERIFIED", "WAITING", "UNMATCHED", "AMBIGUOUS"] as const).map((key) => <div key={key} className={"sms-tile " + key.toLowerCase()} title={d.tips.sms[key]}><Bidi className="value">{Number(sms[key]) || 0}</Bidi><span>{(d.sms as Record<string, string>)[key]}</span></div>)}
      <div className={"sms-tile unused wide" + (unused ? " attention" : "")} title={d.tips.sms.UNUSED}><Bidi className="value">{unused}</Bidi><span>{d.smsUnused}</span></div></div>
  </article>;
}

export function Dashboard({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const role = useRole(); const d = m.dashboard;
  // Opens on today, 12:00 AM to 11:59 PM Cairo time; every number follows the chosen range.
  const [dateFrom, setDateFrom] = usePersisted("day.dateFrom", () => cairoDate()); const [dateTo, setDateTo] = usePersisted("day.dateTo", () => cairoDate());
  const [timeFrom, setTimeFrom] = usePersisted("day.timeFrom", "00:00"); const [timeTo, setTimeTo] = usePersisted("day.timeTo", "23:59");
  const [message, setMessage] = useState(""); const [messageError, setMessageError] = useState(false);
  const range = { date_from: dateFrom, date_to: dateTo, time_from: timeFrom || undefined, time_to: timeTo || undefined };
  const { data, error, loading } = useApi<DashboardData>("/dashboard" + query(range), token, true);
  const workload = useApi<Record<string, number>>("/review-queue/categories" + query(range), token, true).data;
  const sms = (data?.sms_by_status ?? {}) as Record<string, number>; const n = (value: unknown) => Number(value) || 0;
  const queue = (["ready", "missing", "duplicate", "in_progress", "done"] as const).map((key) => [key, n(workload?.[key])] as const);
  const waiting = queue.reduce((sum, [, value]) => sum + value, 0);
  const received = data ? data.transactions_by_device.reduce((sum, item) => sum + n(item.count), 0) : 0;
  async function download() { setMessage(m.reports.preparing); setMessageError(false); try { await downloadExport("/reports/export.xlsx" + query(range), token); setMessage(m.reports.downloaded); } catch { setMessage(m.reports.failed); setMessageError(true); } }
  return <section className="dashboard"><PageHeading title={m.pages.dashboard[0]} description={m.pages.dashboard[1]} help={contextHelp.dashboard} />
    <div className="panel dash-filters"><RangePicker token={token} allowClose value={[dateFrom, dateTo, timeFrom, timeTo]} set={(from, to, tf, tt) => { setDateFrom(from); setDateTo(to); setTimeFrom(tf); setTimeTo(tt); }} hint={<>{d.filterHint} {m.reports.folderBasis}</>} extra={<button type="button" className="with-icon range-export" onClick={() => void download()}><Icon name="download" size={18} /><span>{d.exportExcel}</span></button>} />
      {message && <p role={messageError ? "alert" : "status"} className={"status" + (messageError ? " error" : "")}>{message}</p>}</div>
    <Status loading={loading} error={error} />{data && <>
      <div className="metric-grid">
        <MetricCard to={can(role, "page.bank_sms") ? "/bank-sms" : ""} tone="money" label={d.smsReceived} tip={d.tips.smsReceived} value={<MoneyValue totals={data.bank_sms_received_totals} locale={locale} />} hint={formatMessage(d.smsReceivedHint, { count: n(data.bank_sms_received_count) })} />
        <MetricCard to="/transactions" label={d.confirmedTotal} tip={d.tips.confirmedTotal} value={<MoneyValue totals={data.totals_by_currency} locale={locale} />} hint={<>{formatMessage(d.receiptsCount, { count: n(data.final_transaction_count) })} · {d.autoConfirmed} <Bidi>{n(data.auto_confirmed_count)}</Bidi> · {d.manualConfirmed} <Bidi>{n(data.manual_confirmed_count)}</Bidi></>} />
        <MetricCard to={can(role, "page.review") ? "/review" : ""} tone="review" label={d.notConfirmed} tip={d.tips.notConfirmed} value={<MoneyValue totals={data.review_totals_by_currency} locale={locale} />} hint={<>{formatMessage(d.receiptsCount, { count: n(data.needs_review_count) })}{canReview(role) ? <> · {d.openReview}</> : null}</>} />
        <MetricCard to={can(role, "page.bank_sms") ? "/bank-sms" : ""} label={d.smsMatchedCard} tip={d.tips.smsMatched} value={<Bidi>{n(sms.VERIFIED)}</Bidi>} hint={formatMessage(n(data.rejected_count) ? d.smsMatchedHintRejected : d.smsMatchedHint, { count: received, rejected: n(data.rejected_count) })} />
        <MetricCard to="/duplicates" tone="duplicate" label={d.duplicatesConfirmed} tip={d.tips.duplicatesConfirmed} value={<Bidi>{data.duplicate_group_count}</Bidi>} hint={d.duplicatesConfirmedHint} />
        <MetricCard to={can(role, "page.review") ? "/review?category=duplicate" : ""} tone="duplicate" label={d.duplicatesPossible} tip={d.tips.duplicatesPossible} value={<Bidi>{n(workload?.duplicate)}</Bidi>} hint={d.duplicatesPossibleHint} />
      </div>
      <div className="two-column">
      <Workload queue={queue} waiting={waiting} sms={sms} unused={n(data.bank_sms_unused_count)} role={role} /><Alerts token={token} /></div>
      <article className="panel wide"><div className="section-heading"><div><h2>{d.phonesTitle}</h2><p className="subhead">{d.phonesIntro}</p></div></div>{data.transactions_by_device.length ? <div className="table-wrap flush"><table><thead><tr><th>{d.phone}</th><th>{d.received}</th><th>{d.confirmed}</th><th>{d.smsMatchedCol}</th><th>{d.total}</th><th>{d.lastPhoto}</th><th>{d.lastSeen}</th></tr></thead><tbody>{data.transactions_by_device.map((item) => <tr key={item.device_id}><td data-label={d.phone}>{item.device_name}</td><td data-label={d.received}><Bidi>{item.count}</Bidi></td><td data-label={d.confirmed}><Bidi>{n(item.confirmed_count)}</Bidi></td><td data-label={d.smsMatchedCol}><Bidi>{n(item.sms_verified_count)}</Bidi></td><td data-label={d.total}><Amount amount={item.confirmed_amount ?? null} currency="EGP" locale={locale} /></td><td data-label={d.lastPhoto}><span className={"seen " + freshness(item.last_photo_at)}>{ago(item.last_photo_at, locale) || d.never}</span></td><td data-label={d.lastSeen}><span className={"seen " + freshness(item.last_seen_at)}>{ago(item.last_seen_at, locale) || d.never}</span>{phoneNotes(item.health, d, locale).map((note) => <small key={note} className="health-note">{note}</small>)}</td></tr>)}</tbody></table></div> : <p className="empty-inline">{d.noPhones}</p>}</article>
      <article className="panel pulse-panel"><div className="section-heading"><div><h2>{d.activity}</h2><p className="subhead">{d.deviceHealth}</p></div><span className="pulse" aria-hidden="true" /></div><dl className="fact-list"><div title={d.tips.pulse.receiptImages}><dt>{d.receiptImages}</dt><dd><Bidi>{data.receipt_image_activity}</Bidi></dd></div>{data.hidden_image_count > 0 && <div title={d.tips.pulse.hiddenImages}><dt>{d.hiddenImages}</dt><dd><Bidi>{data.hidden_image_count}</Bidi></dd></div>}<div title={d.tips.pulse.smsEvents}><dt>{d.smsEvents}</dt><dd><Bidi>{data.sms_activity}</Bidi></dd></div><div title={d.tips.pulse.pendingProcessing}><dt>{d.pendingProcessing}</dt><dd><Bidi>{data.pending_processing_count}</Bidi></dd></div><div title={d.tips.pulse.failedProcessing}><dt>{d.failedProcessing}</dt><dd><Bidi>{data.processing_failure_count}</Bidi></dd></div></dl></article>
    </>}
  </section>;
}

/** Shared day filter (Dashboard, Transactions, Review): when images reached the folder, Cairo time. */
type Shift = { id: string; day: string; number: number; opened_at: string; closed_at: string | null; note: string | null; open: boolean };
/** Cairo date (YYYY-MM-DD) and time (HH:MM) of a moment. */
function cairoParts(moment: Date): [string, string] {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(moment);
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return [`${get("year")}-${get("month")}-${get("day")}`, `${get("hour")}:${get("minute")}`];
}

/** The day's shifts as quick filters; on the dashboard also "Close shift". */
function ShiftChips({ token, day, current, apply, allowClose = false }: { token: string; day: string; current: [string, string, string, string]; apply: (from: string, to: string, timeFrom: string, timeTo: string) => void; allowClose?: boolean }) {
  const { locale, messages: m } = useI18n(); const s = m.shifts; const role = useRole();
  const { data, reload } = useApi<{ items: Shift[]; open: Shift }>("/shifts" + query({ day }), token, true);
  const [notice, setNotice] = useState(""); const [asking, setAsking] = useState(false); const [note, setNote] = useState(""); const [busy, setBusy] = useState(false);
  const valid = (shift: unknown): shift is Shift => typeof shift === "object" && shift !== null && typeof (shift as Shift).opened_at === "string" && !Number.isNaN(Date.parse((shift as Shift).opened_at)) && ((shift as Shift).closed_at === null || !Number.isNaN(Date.parse(String((shift as Shift).closed_at))));
  const items: Shift[] = data && Array.isArray(data.items) ? data.items.filter(valid) : []; const openShift: Shift | null = data && valid(data.open) ? data.open : null;
  // The open shift belongs to the day it started; it also shows on today's view while it is still open.
  const openHere = openShift !== null && (openShift.day === day || day === cairoDate());
  const shown = [...items, ...(openShift && openHere && !items.some((item) => item.id === openShift.id) ? [openShift] : [])];
  const bounds = (shift: Shift): [string, string, string, string] => {
    const [from, timeFrom] = cairoParts(new Date(shift.opened_at));
    if (!shift.closed_at) return [from, cairoDate(), timeFrom, "23:59"];
    const [to, timeTo] = cairoParts(new Date(new Date(shift.closed_at).getTime() - 60000));
    return [from, to, timeFrom, timeTo];
  };
  // When the open shift started on another day, say which day so "Close shift 1" is not ambiguous.
  // A time on another day than the one being viewed carries its weekday: "Sat 12:00 AM – Sun 08:57 AM".
  const when = (iso: string) => { const [day0] = cairoParts(new Date(iso)); return day0 === day ? cairoClock(iso, locale) : `${new Date(day0 + "T12:00:00Z").toLocaleDateString(locale === "ar" ? "ar-EG" : "en-GB", { weekday: "short", timeZone: "UTC" })} ${cairoClock(iso, locale)}`; };
  const started = (shift: Shift) => { const [day0] = cairoParts(new Date(shift.opened_at)); const label = new Date(day0 + "T12:00:00Z").toLocaleDateString(locale === "ar" ? "ar-EG" : "en-GB", { weekday: "long", day: "numeric", month: "short", timeZone: "UTC" }); return day0 === cairoDate() ? cairoClock(shift.opened_at, locale) : `${label} ${cairoClock(shift.opened_at, locale)}`; };
  async function closeShift() {
    const open = openShift; if (!open) return; setBusy(true);
    try { await api("/shifts/close", token, { method: "POST", body: JSON.stringify({ note: note.trim() || null }) }); setNotice(formatMessage(s.closed, { number: number(open.number) })); setAsking(false); setNote(""); reload(); }
    catch (caught) { setNotice(caught instanceof Error ? caught.message : s.failed); }
    finally { setBusy(false); }
  }
  const number = (value: number) => new Intl.NumberFormat(locale === "ar" ? "ar-EG" : "en-GB").format(value);
  const listed = shown.filter((shift) => !shift.closed_at || shift.closed_at > shift.opened_at);
  if (listed.length === 0 && !notice) return null;
  return <><div className="shift-strip" role="group" aria-label={s.title}>
    {listed.map((shift) => {
      // A shift that covers the whole day is the same as "Today": only "Today" lights up.
      const b = bounds(shift); const on = b.every((value, index) => value === current[index]) && !(b[2] === "00:00" && b[3] === "23:59");
      return <button key={shift.id} type="button" className={"shift-card" + (on ? " active" : "")} aria-pressed={on} title={shift.note ?? undefined} onClick={() => apply(...b)}>
        <span className="shift-name">{!shift.closed_at && <span className="live-dot" role="img" aria-label={s.open} title={s.open} />}{formatMessage(s.label, { number: number(shift.number) })}</span>
        <small>{when(shift.opened_at)} – {shift.closed_at ? when(shift.closed_at) : s.now}</small></button>;
    })}
  </div>
    {allowClose && can(role, "action.close_shift") && openShift && openHere && !asking && <button type="button" className="close-shift" onClick={() => { setNotice(""); setAsking(true); }}>{formatMessage(s.close, { number: number(openShift.number) })}</button>}
    {asking && openShift && <div className="close-confirm" role="group" aria-label={formatMessage(s.close, { number: number(openShift.number) })}>
      <p>{formatMessage(s.closeAsk, { number: number(openShift.number), start: started(openShift) })}</p>
      <input value={note} onChange={(event) => setNote(event.target.value)} placeholder={s.notePlaceholder} aria-label={s.notePlaceholder} maxLength={500} />
      <div className="button-row"><button type="button" className="danger-button" disabled={busy} onClick={() => void closeShift()}>{busy ? s.closing : formatMessage(s.close, { number: number(openShift.number) })}</button><button type="button" className="quiet-button" disabled={busy} onClick={() => setAsking(false)}>{s.cancel}</button></div>
    </div>}
    {notice && <p className="status" role="status">{notice}</p>}</>;
}

function useDayRange() {
  const [dateFrom, setDateFrom] = usePersisted("day.dateFrom", () => cairoDate()); const [dateTo, setDateTo] = usePersisted("day.dateTo", () => cairoDate());
  const [timeFrom, setTimeFrom] = usePersisted("day.timeFrom", "00:00"); const [timeTo, setTimeTo] = usePersisted("day.timeTo", "23:59");
  return { dateFrom, dateTo, timeFrom, timeTo, setDateFrom, setDateTo, setTimeFrom, setTimeTo, params: { date_from: dateFrom, date_to: dateTo, time_from: timeFrom || undefined, time_to: timeTo || undefined } };
}

/** One period picker everywhere: Today / Yesterday / Custom, the day's shifts, and what is applied. */
function RangePicker({ value, set, token, allowClose = false, extra, hint }: { value: [string, string, string, string]; set: (from: string, to: string, timeFrom: string, timeTo: string) => void; token?: string; allowClose?: boolean; extra?: ReactNode; hint?: ReactNode }) {
  const { locale, messages: m } = useI18n(); const d = m.dashboard; const [from, to, timeFrom, timeTo] = value;
  const presets: [string, string][] = [[d.today, cairoDate()], [d.yesterday, cairoDate(1)]];
  const preset = presets.find(([, day]) => from === day && to === day && timeFrom === "00:00" && timeTo === "23:59");
  const [custom, setCustom] = useState(false);
  const tag = locale === "ar" ? "ar-EG" : "en-GB";
  const day = (iso: string) => iso ? new Date(iso + "T12:00:00Z").toLocaleDateString(tag, { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" }) : "";
  const clock = (hhmm: string) => hhmm ? new Date("2000-01-01T" + hhmm + ":00Z").toLocaleTimeString(tag, { hour: "2-digit", minute: "2-digit", timeZone: "UTC" }) : "";
  const summary = from === to ? `${day(from)} · ${clock(timeFrom)} – ${clock(timeTo)}` : `${day(from)} ${clock(timeFrom)} – ${day(to)} ${clock(timeTo)}`;
  return <div className="range-picker">
    <div className="range-top">
      <div className="segmented" role="group" aria-label={d.rangeLabel}>
        {presets.map(([label, iso]) => { const on = !custom && preset?.[1] === iso; return <button key={label} type="button" aria-pressed={on} className={on ? "active" : ""} onClick={() => { setCustom(false); set(iso, iso, "00:00", "23:59"); }}>{label}</button>; })}
        <button type="button" aria-pressed={custom} aria-expanded={custom} className={custom ? "active" : ""} onClick={() => setCustom(!custom)}>{d.custom}</button>
      </div>
      {extra}
    </div>
    {token && <ShiftChips token={token} day={from} current={value} apply={(a, b, c, e) => { setCustom(false); set(a, b, c, e); }} allowClose={allowClose} />}
    <p className="range-summary"><Icon name="calendar" size={16} /><Bidi>{summary}</Bidi></p>
    {custom && <div className="range-fields">
      <DateFields dateFrom={from} dateTo={to} setDateFrom={(v) => set(v, to, timeFrom, timeTo)} setDateTo={(v) => set(from, v, timeFrom, timeTo)} />
      <label>{d.fromTime}<input type="time" value={timeFrom} onChange={(e) => set(from, to, e.target.value, timeTo)} /></label>
      <label>{d.toTime}<input type="time" value={timeTo} onChange={(e) => set(from, to, timeFrom, e.target.value)} /></label>
      {hint && <p className="hint">{hint}</p>}
    </div>}
  </div>;
}

function DayRange({ range, changed, token }: { range: ReturnType<typeof useDayRange>; changed: () => void; token?: string }) {
  return <RangePicker token={token} value={[range.dateFrom, range.dateTo, range.timeFrom, range.timeTo]} set={(from, to, timeFrom, timeTo) => { range.setDateFrom(from); range.setDateTo(to); range.setTimeFrom(timeFrom); range.setTimeTo(timeTo); changed(); }} />;
}

/** A value read by a machine but not yet confirmed, shown as a suggestion. */
function suggested(item: Transaction, field: string): string | null {
  const resolution = item.resolution_result as { fields?: Record<string, { value?: unknown; trust?: unknown }> } | null | undefined;
  const fields = resolution?.fields;
  const entry = fields?.[field];
  return entry && entry.trust === "MACHINE_ONLY" && typeof entry.value === "string" ? entry.value : null;
}

/** A confirmed name, else the text reader's reading marked as suggested. */
/** One side of a transfer. A name the text reader saw is marked "suggested"
 * only while the receipt still waits for review; once accepted it is shown plainly. */
function Party({ label, value, guess, settled }: { label: string; value: string | null; guess: string | null; settled: boolean }) {
  const { messages: m } = useI18n(); const shown = value || guess;
  return <div className="party"><span className="party-label">{label}</span>
    {shown ? <bdi className="party-value" dir="ltr">{shown}</bdi> : <span className="muted">{dash}</span>}
    {!value && guess && !settled && <span className="tag-suggested">{m.transactions.suggestedValue}</span>}</div>;
}

/** Why a receipt waits for review or was rejected. */
function ReceiptFlags({ item }: { item: Transaction }) {
  const { messages: m } = useI18n(); const reasons = m.transactions.reasons as Record<string, string>;
  const text = item.status_reason ? reasons[item.status_reason] ?? item.status_reason : null;
  if (!text) return null;
  const tone = item.review_status === "REJECTED" || item.status_reason === "old_receipt" ? "danger" : "warning";
  return <span className="reason-line"><span className={"reason-tag " + tone}>{text}</span>
    {item.similar_to && <Link className="similar-link" to={"/compare/" + item.id}>{m.transactions.compareSimilar}</Link>}</span>;
}

export function Transactions({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const range = useDayRange();
  const [search, setSearch] = usePersisted("transactions.search", ""); const [review, setReview] = usePersisted("transactions.review", ""); const [duplicate, setDuplicate] = usePersisted("transactions.duplicate", "");
  const [currency, setCurrency] = usePersisted("transactions.currency", ""); const [sort, setSort] = usePersisted("transactions.sort", "folder_at"); const [direction, setDirection] = usePersisted("transactions.direction", "desc"); const [offset, setOffset] = usePersisted("transactions.offset", 0); const limit = 25;
  const path = "/transactions" + query({ ...range.params, search: search || undefined, review_status: review || undefined, duplicate_status: duplicate || undefined, currency: currency || undefined, sort_by: sort, sort_direction: direction, limit, offset });
  const { data, error, loading } = useApi<Page<Transaction>>(path, token, true);
  return <section><PageHeading title={m.pages.transactions[0]} description={m.pages.transactions[1]} help={contextHelp.transactions} />
    <div className="filter-bar"><label className="grow">{m.filters.search}<input placeholder={m.filters.searchTransactions} value={search} maxLength={64} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} /></label><DayRange range={range} changed={() => setOffset(0)} /><label>{m.filters.review}<select value={review} onChange={(e) => setReview(e.target.value)}><option value="">{m.common.all}</option><option value="NEEDS_REVIEW">{m.statuses.NEEDS_REVIEW}</option><option value="AUTO_ACCEPTED">{m.statuses.AUTO_ACCEPTED}</option><option value="REJECTED">{m.statuses.REJECTED}</option></select></label><label>{m.filters.duplicate}<select value={duplicate} onChange={(e) => setDuplicate(e.target.value)}><option value="">{m.common.all}</option><option value="DUPLICATE">{m.transactions.duplicateItem}</option><option value="CANONICAL">{m.transactions.canonical}</option><option value="UNIQUE">{m.transactions.unique}</option><option value="COPY">{m.transactions.copies}</option></select></label><label>{m.filters.currency}<input className="short" dir="ltr" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label><label>{m.filters.sort}<select value={sort} onChange={(e) => setSort(e.target.value)}><option value="folder_at">{m.filters.arrived}</option><option value="created_at">{m.filters.relayEntry}</option><option value="transaction_occurred_at">{m.filters.transactionDate}</option><option value="amount">{m.filters.amount}</option><option value="reference">{m.filters.reference}</option><option value="review_status">{m.filters.review}</option></select></label><label>{m.filters.order}<select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="desc">{m.filters.newest}</option><option value="asc">{m.filters.oldest}</option></select></label></div>
    <p className="hint">{m.filters.applyHint}</p><Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noTransactions} />{data && data.total > 0 && <div className="table-wrap"><table className="responsive-table"><thead><tr><th>{m.transactions.arrived}</th><th>{m.transactions.reference}</th><th>{m.transactions.amount}</th><th>{m.transactions.parties}</th><th>{m.transactions.review}</th><th>{m.transactions.duplicate}</th><th>{m.sms.column}</th><th>{m.transactions.device}</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.id}><td data-label={m.transactions.arrived}><Timestamp value={item.folder_at ?? item.created_at} locale={locale} /><span className="cell-sub">{m.transactions.relayEntry} <Timestamp value={item.created_at} locale={locale} /></span></td><td data-label={m.transactions.reference}><Link aria-label={m.transactions.openEvidence + " " + (item.reference_original || item.id)} to={"/transactions/" + item.id}><Bidi>{item.reference_original || suggested(item, "reference_number") || m.transactions.noReference}</Bidi></Link>{!item.reference_original && suggested(item, "reference_number") && <span className="muted"> · {m.transactions.suggestedValue}</span>}</td><td data-label={m.transactions.amount}>{item.amount || !suggested(item, "amount") ? <Amount amount={item.amount} currency={item.currency} locale={locale} /> : <><Amount amount={suggested(item, "amount")} currency={suggested(item, "currency") || "EGP"} locale={locale} /><span className="muted"> · {m.transactions.suggestedValue}</span></>}</td><td data-label={m.transactions.parties}><div className="parties"><Party label={m.transactions.partyFrom} value={item.sender_name} guess={item.suggested_sender} settled={item.review_status !== "NEEDS_REVIEW"} /><Party label={m.transactions.partyTo} value={item.recipient_name} guess={item.suggested_recipient} settled={item.review_status !== "NEEDS_REVIEW"} /></div></td><td data-label={m.transactions.review}><StatusBadge value={item.review_status} /> <ReceiptFlags item={item} /></td><td data-label={m.transactions.duplicate}>{item.is_canonical ? m.transactions.canonical : item.duplicate_of_candidate_id ? m.transactions.duplicateItem : m.transactions.unique}</td><td data-label={m.sms.column}><SmsBadge value={item.sms_verification} /></td><td data-label={m.transactions.device}>{item.device_name}</td></tr>)}</tbody></table></div>}{data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}
  </section>;
}

/** What the bank SMS that day says about a set-aside receipt's amount. */
function SmsMatch({ match }: { match?: { at: string | null; used_by: string | null } }) {
  const { locale, messages: m } = useI18n(); const r = m.rejected;
  if (!match) return null;
  if (!match.at) return <small className="sms-note">{r.smsNone}</small>;
  const time = cairoClock(match.at, locale);
  if (!match.used_by) return <small className="sms-note free">{formatMessage(r.smsFree, { time })}</small>;
  return <small className="sms-note used">{formatMessage(r.smsUsed, { time })} <Link to={"/transactions/" + match.used_by}>{r.openUsed}</Link></small>;
}

/** Receipts set aside: rejected (by a person or by Relay) or hidden as not a
 * receipt, each with a button to bring it back to Review. */
export function SetAside({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const r = m.rejected; const role = useRole(); const range = useDayRange();
  const [offset, setOffset] = usePersisted("rejected.offset", 0); const limit = 25;
  const [busy, setBusy] = useState(""); const [message, setMessage] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const { data, error, loading, reload } = useApi<Page<Transaction>>("/transactions" + query({ ...range.params, review_status: "SET_ASIDE", sort_by: "folder_at", sort_direction: "desc", limit, offset }), token, true);
  const reasons = m.transactions.reasons as Record<string, string>;
  const admin = can(role, "action.delete_receipts"); const shown = data?.items.map((item) => item.id) ?? [];
  const toggle = (id: string) => setPicked((now) => now.includes(id) ? now.filter((one) => one !== id) : [...now, id]);
  async function bringBack(id: string) {
    setBusy(id); setMessage("");
    try { await api("/receipts/" + encodeURIComponent(id) + "/reopen", token, { method: "POST" }); setMessage(r.broughtBack); reload(); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : r.failed); }
    finally { setBusy(""); }
  }
  async function remove(ids: string[]) {
    if (ids.length === 0 || !window.confirm(formatMessage(r.deleteConfirm, { count: ids.length }))) return;
    setBusy("delete"); setMessage("");
    try {
      const result = await api<{ deleted: number; skipped: number }>("/receipts/delete", token, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids }) });
      setMessage(formatMessage(r.deleted, { count: result.deleted })); setPicked([]); reload();
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : r.failed); }
    finally { setBusy(""); }
  }
  return <section><PageHeading title={m.pages.rejected[0]} description={m.pages.rejected[1]} help={contextHelp.transactions} />
    <div className="filter-bar"><DayRange range={range} changed={() => { setOffset(0); setPicked([]); }} /></div>
    {admin && data && data.total > 0 && <div className="bulk-bar"><button type="button" className="danger-button" disabled={picked.length === 0 || busy === "delete"} onClick={() => void remove(picked)}>{formatMessage(r.deleteSelected, { count: picked.length })}</button></div>}
    {message && <p className="status" role="status">{message}</p>}
    <Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={r.empty} />
    {data && data.total > 0 && <div className="table-wrap"><table className="responsive-table"><thead><tr>{admin && <th><input type="checkbox" aria-label={r.selectAll} checked={shown.length > 0 && shown.every((id) => picked.includes(id))} onChange={(event) => setPicked(event.target.checked ? shown : [])} /></th>}<th>{m.transactions.arrived}</th><th>{m.transactions.device}</th><th>{m.transactions.amount}</th><th>{r.why}</th><th>{r.action}</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.id}>
      {admin && <td data-label={r.select}><input type="checkbox" aria-label={r.select} checked={picked.includes(item.id)} onChange={() => toggle(item.id)} /></td>}
      <td data-label={m.transactions.arrived}><Link to={"/transactions/" + item.id}><Timestamp value={item.folder_at || item.created_at} locale={locale} /></Link></td>
      <td data-label={m.transactions.device}>{item.device_name}</td>
      <td data-label={m.transactions.amount}>{item.amount || item.suggested_amount ? <Amount amount={String(item.amount || item.suggested_amount)} currency={item.currency || "EGP"} locale={locale} /> : dash}</td>
      <td data-label={r.why}><span className="reason-tag danger">{item.status_reason ? reasons[item.status_reason] ?? item.status_reason : dash}</span><SmsMatch match={item.sms_match as { at: string | null; used_by: string | null } | undefined} /></td>
      <td data-label={r.action}><div className="row-actions">{can(role, "action.bring_back") ? <button type="button" className="quiet-button" disabled={busy === item.id} onClick={() => void bringBack(item.id)}>{r.bringBack}</button> : null}{admin && <button type="button" className="danger-link" disabled={busy === "delete"} onClick={() => void remove([item.id])}>{r.delete}</button>}{!canReview(role) && !admin && dash}</div></td>
    </tr>)}</tbody></table></div>}
    {data && <Pager total={data.total} limit={limit} offset={offset} setOffset={(next) => { setOffset(next); setPicked([]); }} />}
  </section>;
}

/** A possible copy beside the receipt it may copy: both photos and details,
 * with differences marked, and one click to reject it or keep it. */
export function CompareReceipts({ token }: { token: string }) {
  const { id = "" } = useParams(); const { locale, messages: m } = useI18n(); const c = m.compare; const role = useRole(); const navigate = useNavigate();
  const { data: mine, error, loading } = useApi<Transaction>("/transactions/" + encodeURIComponent(id), token);
  const otherId = typeof mine?.similar_to === "string" ? mine.similar_to : "";
  const { data: fetched } = useApi<Transaction>("/transactions/" + encodeURIComponent(otherId || id), token);
  const other = otherId && fetched?.id === otherId ? fetched : null;
  const [message, setMessage] = useState("");
  async function act(path: string, body?: object) {
    setMessage("");
    try { await api(path, token, { method: "POST", ...(body ? { body: JSON.stringify(body) } : {}) }); navigate("/review?category=duplicate"); }
    catch (caught) { setMessage(caught instanceof Error ? caught.message : c.failed); }
  }
  const rows: [string, (item: Transaction) => Json | undefined][] = [
    [m.transactions.amount, (item) => item.amount ?? suggested(item, "amount")],
    [m.transactions.reference, (item) => item.reference_original ?? suggested(item, "reference_number")],
    [m.transactions.arrived, (item) => item.folder_at ?? item.created_at],
    [m.transactions.device, (item) => item.device_name],
    [c.sender, (item) => item.suggested_sender],
    [c.status, (item) => item.review_status],
    [c.sms, (item) => item.sms_verification]
  ];
  const side = (item: Transaction | null | undefined, title: string) => <article className="panel compare-side"><h2>{title}</h2>{item ? <><ProtectedImage key={item.id} path={item.image_url} token={token} alt={title} /><dl className="compare-facts">{rows.map(([label, read]) => { const value = read(item); const theirs = item === mine ? (other ? read(other) : undefined) : (mine ? read(mine) : undefined); const differs = value !== undefined && theirs !== undefined && String(value) !== String(theirs); return <div key={label} className={differs ? "differs" : undefined}><dt>{label}</dt><dd>{value === null || value === undefined || value === "" ? dash : label === m.transactions.arrived ? <Timestamp value={String(value)} locale={locale} /> : <Bidi>{String(value)}</Bidi>}</dd></div>; })}</dl><Link to={"/transactions/" + item.id}>{c.openDetail}</Link></> : <p className="muted">{c.noOther}</p>}</article>;
  return <section><PageHeading title={c.title} description={c.description} help={contextHelp.review} />
    <Status loading={loading} error={error} />
    {mine && <><div className="compare-grid">{side(other, c.first)}{side(mine, c.copy)}</div>
      {message && <p className="status" role="status">{message}</p>}
      {can(role, "action.reject_copies") && mine.review_status === "NEEDS_REVIEW" && <div className="compare-actions"><button type="button" className="danger-button" onClick={() => void act("/receipts/reject-copies", { ids: [mine.id] })}>{c.rejectCopy}</button><button type="button" className="quiet-button" onClick={() => void act("/receipts/" + encodeURIComponent(mine.id) + "/not-a-copy")}>{c.notCopy}</button></div>}</>}
  </section>;
}

export function TransactionDetail({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const { id = "" } = useParams(); const role = useRole();
  const { data, error, loading, reload } = useApi<Transaction & Record<string, Json>>("/transactions/" + id, token);
  const [region, setRegion] = useState<{ receiptId: string; polygon: number[][] } | null>(null); const evidence = data ? evidenceFirst(data) : null;
  const facts = data ? [[m.detail.reference, <Bidi>{data.reference_original || dash}</Bidi>], [m.detail.amount, <Amount amount={data.amount} currency={data.currency} locale={locale} />], [m.detail.transactionTime, <Timestamp value={data.transaction_occurred_at} locale={locale} />], [m.detail.relayEntry, <Timestamp value={data.created_at} locale={locale} />], [m.detail.review, <StatusBadge value={data.review_status} />], [m.detail.processing, <StatusBadge value={data.processing_status} />], [m.detail.device, data.device_name], [m.detail.source, data.source_type], [m.detail.confidence, <Bidi>{data.ocr_mean_confidence === null ? dash : Math.round(data.ocr_mean_confidence * 100) + "%"}</Bidi>]] : [];
  return <section><PageHeading title={m.pages.transactionDetail[0]} description={m.pages.transactionDetail[1]} help={contextHelp.transactionDetail} /><Status loading={loading} error={error} />{data && data.id === id && <><ReceiptJourney transaction={data} /><div className="detail-grid">
    <article className="panel receipt-panel"><h2>{m.detail.receiptImage}</h2><ProtectedImage key={data.id} path={data.image_url} token={token} polygon={region?.receiptId === data.id ? region.polygon : []} alt={formatMessage(m.detail.receiptAlt, { reference: data.reference_original || data.id })} /></article>
    <article className="panel final-panel"><h2>{m.detail.reviewedTitle}</h2><p className="subhead">{m.detail.reviewedPriority}</p><dl className="fact-list">{facts.map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{data.duplicate_group_id && <Link className="button-link" to={"/duplicates/" + data.duplicate_group_id}>{m.detail.openDuplicate}</Link>}</article>
    {evidence && !canReview(role) ? <article className="panel wide"><h2>{m.exceptions.title}</h2><p className="hint">{m.users.readOnly}</p><ExceptionSummary receipt={data} /></article> : evidence ? <ExceptionReview key={data.id + String(data.updated_at)} receipt={data} evidence={evidence} token={token} reload={reload} focus={(polygon) => setRegion({ receiptId: data.id, polygon })} /> : <p className="panel wide status error" role="alert">{m.exceptions.failed}</p>}
    <SmsVerificationPanel key={"sms-" + data.id + String(data.updated_at)} id={data.id} token={token} />
    <details className="panel wide evidence-details"><summary>{m.detail.showEvidence}</summary><h2>{m.detail.machine}</h2><p className="hint">{m.detail.machineHint}</p><h3>{m.detail.provenance}</h3><pre dir="ltr">{display(data.ocr)}</pre><h3>{m.detail.history}</h3><pre dir="ltr">{display({ review_history: data.review_history, duplicate_resolution_history: data.duplicate_resolution_history })}</pre></details>
  </div></>}</section>;
}

export function Duplicates({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const initial = defaultDates(); const [dateFrom, setDateFrom] = useState(initial.dateFrom); const [dateTo, setDateTo] = useState(initial.dateTo); const [search, setSearch] = useState(""); const [offset, setOffset] = useState(0); const limit = 25;
  const { data, error, loading } = useApi<Page<DuplicateGroup>>("/duplicate-groups" + query({ date_from: dateFrom, date_to: dateTo, search: search || undefined, limit, offset }), token);
  return <section><PageHeading title={m.pages.duplicates[0]} description={m.pages.duplicates[1]} help={contextHelp.duplicates} /><div className="filter-bar"><label className="grow">{m.filters.searchReferences}<input value={search} onChange={(e) => setSearch(e.target.value)} /></label><DateFields {...{ dateFrom, dateTo, setDateFrom, setDateTo }} /></div><Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noDuplicates} />{data && data.total > 0 && <div className="card-list">{data.items.map((group) => <article className="record-card duplicate-row" key={group.group_id}><div><h2><Link to={"/duplicates/" + group.group_id}><Bidi>{group.reference_original || m.transactions.noReference}</Bidi></Link></h2><p className="subhead">{formatMessage(m.duplicates.groupSize, { count: group.group_size })} · {group.device_name}</p><p><Amount amount={group.amount} currency={group.currency} locale={locale} /> · {m.duplicates.canonical} <Bidi>{group.canonical_candidate_id}</Bidi></p></div><div><StatusBadge value="DUPLICATE" tone="warning" /><p className="hint">{m.duplicates.evidencePreserved}</p></div></article>)}</div>}{data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}</section>;
}

export function DuplicateDetail({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const { id = "" } = useParams(); const [offset, setOffset] = useState(0); const limit = 20; const [message, setMessage] = useState(""); const [messageError, setMessageError] = useState(false);
  const { data, error, loading, reload } = useApi<{ group_id: string; group_size: number; members: Transaction[] }>("/duplicate-groups/" + id + query({ limit, offset }), token);
  async function decide(candidateId: string, form: FormData) { setMessage(""); setMessageError(false); try { await api("/duplicate-groups/" + id + "/members/" + candidateId + "/resolution", token, { method: "POST", body: JSON.stringify({ decision: form.get("decision"), note: form.get("note") || null }) }); setMessage(m.duplicates.recorded); reload(); } catch { setMessage(m.duplicates.failed); setMessageError(true); } }
  const canonical = data?.members.find((item) => item.is_canonical) || data?.members[0];
  function difference(value: string | null, base: string | null) { return value === base ? m.duplicates.matches : m.duplicates.differs; }
  return <section><PageHeading title={m.pages.duplicateDetail[0]} description={m.pages.duplicateDetail[1]} help={contextHelp.duplicateDetail} /><div className="reason-banner"><strong>{m.duplicates.reason}</strong><p>{m.duplicates.referenceReason}</p><span>{m.duplicates.evidencePreserved}</span></div><Status loading={loading} error={error} />{message && <p className={"status" + (messageError ? " error" : "")} role={messageError ? "alert" : "status"}>{message}</p>}{data && <><div className="compare-grid">{data.members.map((item) => <article className={"panel comparison-card" + (item.is_canonical ? " canonical" : "")} key={item.id}><h2><Bidi>{item.reference_original || m.transactions.noReference}</Bidi></h2><p className="subhead">{item.is_canonical ? m.duplicates.canonical : m.duplicates.candidate}</p><ProtectedImage path={item.image_url} token={token} alt={formatMessage(m.detail.receiptAlt, { reference: item.reference_original || item.id })} /><dl className="fact-list compare-facts"><div><dt>{m.detail.amount}</dt><dd><Amount amount={item.amount} currency={item.currency} locale={locale} />{!item.is_canonical && <small className={difference(item.amount, canonical?.amount || null) === m.duplicates.differs ? "difference" : "match"}>{difference(item.amount, canonical?.amount || null)}</small>}</dd></div><div><dt>{m.detail.transactionTime}</dt><dd><Timestamp value={item.transaction_occurred_at} locale={locale} /></dd></div><div><dt>{m.duplicates.received}</dt><dd><Timestamp value={item.received_at} locale={locale} /></dd></div><div><dt>{m.duplicates.sender}</dt><dd>{item.sender_name || dash}{!item.is_canonical && <small className={difference(item.sender_name, canonical?.sender_name || null) === m.duplicates.differs ? "difference" : "match"}>{difference(item.sender_name, canonical?.sender_name || null)}</small>}</dd></div><div><dt>{m.duplicates.recipient}</dt><dd>{item.recipient_name || dash}</dd></div><div><dt>{m.detail.confidence}</dt><dd><Bidi>{item.ocr_mean_confidence === null ? dash : Math.round(item.ocr_mean_confidence * 100) + "%"}</Bidi></dd></div></dl><Link to={"/transactions/" + item.id}>{m.duplicates.openFull}</Link><form className="decision-form" action={(form) => void decide(item.id, form)}><label>{m.duplicates.operatorDecision}<select name="decision" defaultValue="UNRESOLVED"><option value="UNRESOLVED">{m.duplicates.unresolved}</option><option value="CONFIRMED_DUPLICATE">{m.duplicates.confirmed}</option><option value="NOT_DUPLICATE">{m.duplicates.notDuplicate}</option></select></label><label>{m.duplicates.note}<textarea name="note" maxLength={500} /></label><button type="submit">{m.duplicates.record}</button></form></article>)}</div><Pager total={data.group_size} limit={limit} offset={offset} setOffset={setOffset} /></>}</section>;
}

export function ReviewQueue({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const range = useDayRange(); const [search, setSearch] = usePersisted("review.search", ""); const [direction, setDirection] = usePersisted("review.direction", "desc"); const [offset, setOffset] = usePersisted("review.offset", 0); const limit = 25;
  const [params] = useSearchParams(); const [category, setCategory] = useState(params.get("category") || "");
  const filters = { ...range.params, search: search || undefined };
  const { data, error, loading, reload } = useApi<Page<Transaction>>("/review-queue" + query({ ...filters, category: category || undefined, sort_direction: direction, limit, offset }), token, true);
  const { data: counts } = useApi<Record<string, number>>("/review-queue/categories" + query(filters), token, true);
  const tabs: [string, string][] = [["", m.review.categories.all], ["ready", m.review.categories.ready], ["missing", m.review.categories.missing], ["duplicate", m.review.categories.duplicate], ["in_progress", m.review.categories.in_progress], ["done", m.review.categories.done]];
  const total = counts ? Object.entries(counts).reduce((sum, [key, value]) => key === "done" ? sum : sum + (Number(value) || 0), 0) : undefined;
  const role = useRole(); const [picked, setPicked] = useState<string[]>([]); const [notice, setNotice] = useState("");
  const copies = category === "duplicate" && can(role, "action.reject_copies");
  async function rejectCopies() {
    if (picked.length === 0 || !window.confirm(formatMessage(m.compare.rejectSelectedConfirm, { count: picked.length }))) return;
    try { const result = await api<{ rejected: number }>("/receipts/reject-copies", token, { method: "POST", body: JSON.stringify({ ids: picked }) }); setNotice(formatMessage(m.compare.rejected, { count: result.rejected })); setPicked([]); reload(); }
    catch (caught) { setNotice(caught instanceof Error ? caught.message : m.compare.failed); }
  }
  return <section><PageHeading title={m.pages.review[0]} description={m.pages.review[1]} help={contextHelp.review} /><p className="queue-guidance">{m.review.oldestHint}</p>
    <div className="category-tabs" role="tablist" aria-label={m.review.categories.label}>{tabs.map(([key, label]) => { const count = key ? counts?.[key] : total; return <button key={key || "all"} type="button" role="tab" aria-selected={category === key} className={category === key ? "active" : ""} onClick={() => { setCategory(key); setOffset(0); }}>{label}{count !== undefined && <span className="count">{count}</span>}</button>; })}</div>
    {category && <p className="hint">{(m.review.categories.hints as Record<string, string>)[category]}{category === "duplicate" && <> <Link to="/duplicates">{m.review.openDuplicates}</Link></>}</p>}<div className="filter-bar"><label className="grow">{m.filters.search}<input value={search} maxLength={64} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} /></label><DayRange range={range} changed={() => setOffset(0)} /><label>{m.filters.order}<select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="desc">{m.filters.newest}</option><option value="asc">{m.filters.oldest}</option></select></label></div>{copies && data && data.total > 0 && <div className="bulk-bar"><label className="check-all"><input type="checkbox" checked={data.items.length > 0 && data.items.every((item) => picked.includes(item.id))} onChange={(event) => setPicked(event.target.checked ? data.items.map((item) => item.id) : [])} /> {m.compare.selectAll}</label><button type="button" className="danger-button" disabled={picked.length === 0} onClick={() => void rejectCopies()}>{formatMessage(m.compare.rejectSelected, { count: picked.length })}</button></div>}{notice && <p className="status" role="status">{notice}</p>}<Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noReview} />{data && data.total > 0 && <div className="card-list">{data.items.map((item) => <article className="record-card review-row clickable" key={item.id}>{copies && <input type="checkbox" className="card-check" aria-label={m.compare.select} checked={picked.includes(item.id)} onChange={() => setPicked((now) => now.includes(item.id) ? now.filter((one) => one !== item.id) : [...now, item.id])} />}<div><h2><Link className="card-link" to={"/transactions/" + item.id}><Bidi>{item.reference_original || suggested(item, "reference_number") || m.review.needsReview}</Bidi></Link></h2><p className="subhead">{m.transactions.arrived} <Timestamp value={item.folder_at ?? item.created_at} locale={locale} /> · {item.device_name}</p><p><Amount amount={item.amount ?? suggested(item, "amount")} currency={item.currency ?? suggested(item, "currency") ?? "EGP"} locale={locale} />{!item.amount && suggested(item, "amount") && <span className="muted"> · {m.transactions.suggestedValue}</span>} <ReceiptFlags item={item} /></p></div><ExceptionSummary receipt={item} /></article>)}</div>}{data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}</section>;
}

export function ProcessingQueue({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const [status, setStatus] = useState(""); const [message, setMessage] = useState(""); const [messageError, setMessageError] = useState(false); const [offset, setOffset] = useState(0); const limit = 25;
  const { data, error, loading, reload } = useApi<{ items: Job[]; has_more: boolean }>("/processing/jobs" + query({ status: status || undefined, limit, offset }), token); const retryable = new Set(["OCR_TIMEOUT", "OCR_FAILED", "INTERNAL_ERROR"]);
  async function retry(id: string) { setMessage(""); setMessageError(false); try { await api("/processing/jobs/" + id + "/retry", token, { method: "POST" }); setMessage(m.processing.retryQueued); reload(); } catch { setMessage(m.processing.retryFailed); setMessageError(true); } }
  return <section><PageHeading title={m.pages.processing[0]} description={m.pages.processing[1]} help={contextHelp.processing} /><div className="filter-bar"><label>{m.filters.status}<select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="">{m.common.all}</option>{["PENDING", "PROCESSING", "SUCCEEDED", "NEEDS_REVIEW", "FAILED", "SKIPPED"].map((item) => <option value={item} key={item}>{(m.statuses as Record<string, string>)[item]}</option>)}</select></label></div>{message && <p className={"status" + (messageError ? " error" : "")} role={messageError ? "alert" : "status"}>{message}</p>}<Status loading={loading} error={error} empty={data?.items.length === 0} emptyMessage={m.state.noProcessing} />{data && data.items.length > 0 && <><div className="table-wrap"><table className="responsive-table"><thead><tr><th>{m.filters.status}</th><th>{m.processing.object}</th><th>{m.processing.failure}</th><th>{m.processing.attempts}</th><th>{m.processing.nextAttempt}</th><th>{m.processing.action}</th></tr></thead><tbody>{data.items.map((job) => { const allowed = job.status === "FAILED" && Boolean(job.failure_code) && retryable.has(job.failure_code || "") && job.attempt_count < 5; return <tr key={job.id}><td data-label={m.filters.status}><StatusBadge value={job.status} /></td><td data-label={m.processing.object}><Bidi>{job.object_id}</Bidi></td><td data-label={m.processing.failure}>{job.failure_code ? (m.failures as Record<string, string>)[job.failure_code] || m.failures.default : dash}</td><td data-label={m.processing.attempts}><Bidi>{job.attempt_count + " / " + job.max_attempts}</Bidi></td><td data-label={m.processing.nextAttempt}><Timestamp value={job.next_attempt_at} locale={locale} /></td><td data-label={m.processing.action}>{allowed ? <button type="button" onClick={() => void retry(job.id)}>{m.processing.retry}</button> : <span className="muted">{m.processing.noAction}</span>}</td></tr>; })}</tbody></table></div><nav className="pager" aria-label={m.common.page}><span><Bidi>{m.common.page + " " + (Math.floor(offset / limit) + 1)}</Bidi></span><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>{m.common.previous}</button><button type="button" disabled={!data.has_more} onClick={() => setOffset(offset + limit)}>{m.common.next}</button></nav></>}</section>;
}

export function Reports({ token }: { token: string }) {
  const { messages: m } = useI18n(); const initial = defaultDates(); const [dateFrom, setDateFrom] = useState(initial.dateFrom); const [dateTo, setDateTo] = useState(initial.dateTo); const [timeFrom, setTimeFrom] = useState(""); const [timeTo, setTimeTo] = useState(""); const [currency, setCurrency] = useState(""); const [message, setMessage] = useState(""); const [messageError, setMessageError] = useState(false);
  const range = { date_from: dateFrom, date_to: dateTo, time_from: timeFrom || undefined, time_to: timeTo || undefined };
  const path = query({ ...range, currency: currency || undefined }); const { data, error, loading } = useApi<DashboardData>("/reports/summary" + query(range), token);
  async function download() { setMessage(m.reports.preparing); setMessageError(false); try { await downloadExport("/reports/export.xlsx" + path, token); setMessage(m.reports.downloaded); } catch { setMessage(m.reports.failed); setMessageError(true); } }
  return <section><PageHeading title={m.pages.reports[0]} description={m.pages.reports[1]} help={contextHelp.reports} /><p className="hint">{m.reports.folderBasis}</p><div className="filter-bar"><DateFields {...{ dateFrom, dateTo, setDateFrom, setDateTo }} /><label>{m.reports.timeFrom}<input type="time" value={timeFrom} onChange={(e) => setTimeFrom(e.target.value)} /></label><label>{m.reports.timeTo}<input type="time" value={timeTo} onChange={(e) => setTimeTo(e.target.value)} /></label><label>{m.filters.currency}<input className="short" dir="ltr" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label><button type="button" onClick={() => void download()}>{m.reports.export}</button></div>{message && <p role={messageError ? "alert" : "status"} className={"status" + (messageError ? " error" : "")}>{message}</p>}<Status loading={loading} error={error} />{data && <article className="panel report-scope"><h2>{m.reports.scope}</h2><p className="scope-count"><Bidi>{formatMessage(m.reports.scopeLine, { transactions: data.final_transaction_count, duplicates: data.duplicate_group_count, review: data.needs_review_count })}</Bidi></p><p className="hint">{m.reports.contents}</p></article>}</section>;
}

function configurationLabel(key: string, m: Messages) {
  const value = key.toLowerCase(); if (value.includes("sms") && value.includes("rule")) return m.devices.smsRules; if (value.includes("image") && value.includes("rule")) return m.devices.imageRules; if (value.includes("source")) return m.devices.imageSources; if (value.includes("sms")) return m.devices.sms; if (value.includes("image")) return m.devices.images; return key.replaceAll("_", " ");
}
type SmsInfo = { event_id: string; device_id: string; amount: string; currency: string; occurred_at: string; in_window?: boolean };
type SmsVerification = { status: string; method: string | null; sms: SmsInfo | null; option_count: number; options: SmsInfo[] };
const smsTone = (value: string) => value === "VERIFIED" ? "success" : value === "AMBIGUOUS" || value === "WAITING" ? "warning" : value === "UNMATCHED" ? "danger" : "neutral";
function SmsBadge({ value }: { value: Json | undefined }) {
  const { messages: m } = useI18n(); const key = typeof value === "string" && value ? value : "NOT_CHECKED";
  return <span className={"badge " + smsTone(key)}><span aria-hidden="true" className="status-dot" />{(m.sms.states as Record<string, string>)[key] || key}</span>;
}

export function SmsVerificationPanel({ id, token }: { id: string; token: string }) {
  const { locale, messages: m } = useI18n(); const path = "/receipts/" + encodeURIComponent(id) + "/sms-verification";
  const { data, error, loading, reload } = useApi<SmsVerification>(path, token);
  const [view, setView] = useState<SmsVerification | null>(null); const [busy, setBusy] = useState(false); const [failed, setFailed] = useState(false);
  const current = view ?? data; const options = Array.isArray(current?.options) ? current.options : [];
  async function act(init: RequestInit, target = path) {
    setBusy(true); setFailed(false);
    try { if (target === path) setView(await api<SmsVerification>(path, token, init)); else { await api(target, token, init); setView(null); reload(); } }
    catch { setFailed(true); }
    finally { setBusy(false); }
  }
  if (loading && !current) return <article className="panel wide sms-panel"><p className="hint">{m.sms.loading}</p></article>;
  if (error || !current) return <article className="panel wide sms-panel"><p className="status error" role="alert">{m.sms.unavailable}</p></article>;
  return <article className="panel wide sms-panel" aria-labelledby="sms-title"><div className="section-heading"><div><h2 id="sms-title">{m.sms.title}</h2><p className="subhead">{m.sms.eyebrow}</p></div><SmsBadge value={current.status} /></div>
    <p className="hint">{(m.sms.explain as Record<string, string>)[String(current.status)] || ""}</p>
    {current.sms && <dl className="fact-list"><div><dt>{m.sms.amount}</dt><dd><Amount amount={current.sms.amount} currency={current.sms.currency} locale={locale} /></dd></div><div><dt>{m.sms.time}</dt><dd><Timestamp value={current.sms.occurred_at} locale={locale} /></dd></div><div><dt>{m.sms.method}</dt><dd>{current.method === "MANUAL" ? m.sms.manual : m.sms.auto}</dd></div></dl>}
    {options.length > 0 && <><h3>{m.sms.options}</h3><ul className="sms-options">{options.map((option) => <li key={option.event_id}><span><Amount amount={option.amount} currency={option.currency} locale={locale} /> · <Timestamp value={option.occurred_at} locale={locale} />{option.in_window === false && <span className="muted"> · {m.sms.outsideWindow}</span>}</span><button disabled={busy} onClick={() => void act({ method: "PUT", body: JSON.stringify({ sms_event_id: option.event_id }) })}>{m.sms.use}</button></li>)}</ul></>}
    <div className="sms-actions"><button className="quiet-button" disabled={busy} onClick={() => void act({ method: "POST" }, "/verification/sms/run")}>{m.sms.recheck}</button>{current.method && <button className="quiet-button" disabled={busy} onClick={() => void act({ method: "DELETE" })}>{m.sms.clear}</button>}</div>
    {failed && <p className="status error" role="alert">{m.sms.failed}</p>}
  </article>;
}

export function SmsSources({ deviceId, token, devices }: { deviceId: string; token: string; devices: { id: string; name: string }[] }) {
  const { messages: m } = useI18n(); const path = "/devices/" + encodeURIComponent(deviceId) + "/sms-sources";
  type Sources = { sms_device_ids: string[]; direction: string };
  const { data, error, loading } = useApi<Sources>(path, token);
  const [saved, setSaved] = useState<Sources | null>(null); const [busy, setBusy] = useState(false); const [failed, setFailed] = useState(false);
  const current = saved?.sms_device_ids ?? (Array.isArray(data?.sms_device_ids) ? data.sms_device_ids : []);
  const direction = saved?.direction ?? (data?.direction === "OUT" ? "OUT" : "IN"); const others = devices.filter((device) => device.id !== deviceId);
  async function save(next: string[], nextDirection: string) {
    setBusy(true); setFailed(false);
    try { setSaved(await api<Sources>(path, token, { method: "PUT", body: JSON.stringify({ sms_device_ids: next, direction: nextDirection }) })); }
    catch { setFailed(true); }
    finally { setBusy(false); }
  }
  const toggle = (id: string, on: boolean) => save(on ? [...current, id] : current.filter((value) => value !== id), direction);
  return <section className="training-consent"><h3>{m.sms.sourcesTitle}</h3>{loading ? <p className="hint">{m.sms.loading}</p> : error ? <p className="status error" role="alert">{m.sms.unavailable}</p> : others.length === 0 ? <p className="hint">{m.sms.noOtherDevices}</p> : <><label>{m.sms.directionLabel}<select value={direction} disabled={busy} onChange={(e) => void save(current, e.target.value)}><option value="IN">{m.sms.directionIn}</option><option value="OUT">{m.sms.directionOut}</option></select></label><p className="hint">{m.sms.sourcesHint}</p>{others.map((device) => <label className="toggle-row" key={device.id}><input type="checkbox" checked={current.includes(device.id)} disabled={busy} onChange={(e) => void toggle(device.id, e.target.checked)} />{device.name}</label>)}{failed && <p className="status error" role="alert">{m.sms.failed}</p>}</>}</section>;
}

type DeviceConfig = { version: number; schema_version: number; settings: Record<string, Json> };
const IMAGE_SOURCE = "receipts";
const AMOUNT_WORDS = ["EGP", "جنيه", "ج.م", "ج م", "L.E"];
function collectors(settings: Record<string, Json>): Record<string, Json> { const value = settings.collectors; return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, Json> : {}; }
function senderList(settings: Record<string, Json>): string { const sms = collectors(settings).sms as Record<string, Json> | undefined; const rules = Array.isArray(sms?.rules) ? sms.rules : []; return rules.map((rule) => (rule as { sender?: { value?: string } })?.sender?.value || "").filter(Boolean).join(", "); }
export function CollectionSettings({ deviceId, token }: { deviceId: string; token: string }) {
  const { messages: m } = useI18n(); const path = "/devices/" + encodeURIComponent(deviceId) + "/configuration";
  const { data, error, loading, reload } = useApi<DeviceConfig>(path, token);
  const [saved, setSaved] = useState<DeviceConfig | null>(null); const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [draft, setDraft] = useState<{ images: boolean; sms: boolean; senders: string } | null>(null);
  const current = saved ?? data; const settings = current?.settings ?? {}; const parts = collectors(settings);
  const stored = { images: (parts.images as { enabled?: boolean } | undefined)?.enabled === true, sms: (parts.sms as { enabled?: boolean } | undefined)?.enabled === true, senders: senderList(settings) };
  const form = draft ?? stored; const dirty = draft !== null && (draft.images !== stored.images || draft.sms !== stored.sms || draft.senders.trim() !== stored.senders);
  const change = (next: Partial<typeof form>) => { setDraft({ ...form, ...next }); setNotice(null); };
  async function save() {
    if (!current) return;
    const list = form.senders.split(/[,\n]/).map((value) => value.trim()).filter(Boolean).slice(0, 20);
    const next: Record<string, Json> = { ...parts };
    next.images = { enabled: form.images, sources: [{ id: IMAGE_SOURCE, type: "saf_tree" }], rules: [{ id: "all-images", enabled: true, source_id: IMAGE_SOURCE, mime_types: ["image/jpeg", "image/png", "image/webp"] }] };
    // No sender listed: accept any sender, but only messages that show an amount.
    next.sms = { enabled: form.sms, rules: list.length ? list.map((value, index) => ({ id: "sender-" + (index + 1), enabled: true, sender: { mode: "contains", value } })) : [{ id: "any-bank", enabled: true, body: { any_contains: AMOUNT_WORDS } }] };
    setBusy(true); setNotice(null);
    try { setSaved(await api<DeviceConfig>(path, token, { method: "PUT", body: JSON.stringify({ expected_version: current.version, schema_version: 1, settings: { ...settings, collectors: next } }) })); setDraft(null); setNotice({ text: m.devices.collectionSaved, error: false }); }
    catch (err) {
      const conflict = err instanceof ApiError && err.status === 409;
      setNotice({ text: conflict ? m.devices.collectionChanged : m.devices.collectionFailed + (err instanceof Error && err.message ? " (" + err.message + ")" : ""), error: true });
      if (conflict) { setSaved(null); setDraft(null); reload(); }
    }
    finally { setBusy(false); }
  }
  return <section className="training-consent"><h3>{m.devices.collectionTitle}</h3>{loading && !current ? <p className="hint">{m.devices.trainingLoading}</p> : error || !current ? <p className="status error" role="alert">{m.devices.collectionUnavailable}</p> : <>
    <label className="toggle-row"><input type="checkbox" checked={form.images} disabled={busy} onChange={(e) => change({ images: e.target.checked })} />{m.devices.imagesToggle}</label>
    <label className="toggle-row"><input type="checkbox" checked={form.sms} disabled={busy} onChange={(e) => change({ sms: e.target.checked })} />{m.devices.smsToggle}</label>
    {form.sms && <><label>{m.devices.sendersOptional}<input dir="ltr" value={form.senders} maxLength={600} placeholder="CIB, NBE, InstaPay" disabled={busy} onChange={(e) => change({ senders: e.target.value })} /></label>{!form.senders.trim() && <p className="hint">{m.devices.smsAnySender}</p>}</>}
    <div className="button-row"><button type="button" disabled={busy || !dirty} onClick={() => void save()}>{busy ? m.devices.collectionSaving : m.devices.collectionSave}</button>{dirty && !busy && <button type="button" className="quiet-button" onClick={() => { setDraft(null); setNotice(null); }}>{m.link.cancel}</button>}</div>
    {notice ? <p className={"status" + (notice.error ? " error" : "")} role={notice.error ? "alert" : "status"}>{notice.text}</p> : <p className="hint">{m.devices.collectionHint}</p>}</>}</section>;
}

function TrainingConsent({ deviceId, token }: { deviceId: string; token: string }) {
  const { messages: m } = useI18n(); const path = "/devices/" + encodeURIComponent(deviceId) + "/training-consent";
  const { data, error, loading } = useApi<{ enabled: boolean }>(path, token);
  const [enabled, setEnabled] = useState<boolean | null>(null); const [busy, setBusy] = useState(false); const [failed, setFailed] = useState(false);
  const current = enabled ?? data?.enabled ?? false;
  async function toggle(next: boolean) {
    setBusy(true); setFailed(false);
    try { const result = await api<{ enabled: boolean }>(path, token, { method: "PUT", body: JSON.stringify({ enabled: next }) }); setEnabled(result.enabled); }
    catch { setFailed(true); }
    finally { setBusy(false); }
  }
  return <section className="training-consent"><h3>{m.devices.training}</h3>{loading ? <p className="hint">{m.devices.trainingLoading}</p> : error ? <p className="status error" role="alert">{m.devices.trainingUnavailable}</p> : <><label className="toggle-row"><input type="checkbox" checked={current} disabled={busy} onChange={(e) => void toggle(e.target.checked)} />{m.devices.trainingToggle}</label><p className="hint">{current ? m.devices.trainingOn : m.devices.trainingOff}</p>{failed && <p className="status error" role="alert">{m.devices.trainingFailed}</p>}</>}</section>;
}

function SmsPhoneForAll({ token, devices, done }: { token: string; devices: { id: string; name: string }[]; done: () => void }) {
  const { messages: m } = useI18n(); const [chosen, setChosen] = useState(""); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const smsPhone = devices.find((device) => device.id === chosen);
  // Show the phone already in use: the one most receipt phones are linked to.
  const ids = devices.map((device) => device.id).join(",");
  useEffect(() => {
    let current = true;
    void Promise.all(devices.map((device) => api<{ sms_device_ids?: string[] }>("/devices/" + encodeURIComponent(device.id) + "/sms-sources", token).then((linked) => linked.sms_device_ids ?? [], () => [] as string[])))
      .then((lists) => {
        const counts = new Map<string, number>();
        for (const id of lists.flat()) counts.set(id, (counts.get(id) ?? 0) + 1);
        const best = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
        if (current && best) setChosen((value) => value || best);
      });
    return () => { current = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ids, token]);
  async function apply() {
    if (!smsPhone) return; setBusy(true); setNotice(null); let updated = 0; let failed = false;
    for (const device of devices.filter((item) => item.id !== smsPhone.id)) {
      const path = "/devices/" + encodeURIComponent(device.id) + "/sms-sources";
      try {
        const current = await api<{ sms_device_ids: string[]; direction: string }>(path, token);
        const ids = Array.isArray(current.sms_device_ids) ? current.sms_device_ids : [];
        if (!ids.includes(smsPhone.id)) await api(path, token, { method: "PUT", body: JSON.stringify({ sms_device_ids: [...ids, smsPhone.id], direction: current.direction === "OUT" ? "OUT" : "IN" }) });
        updated += 1;
      } catch { failed = true; }
    }
    setBusy(false); setNotice(failed ? { text: m.devices.bulkFailed, error: true } : { text: formatMessage(m.devices.bulkDone, { count: updated, name: smsPhone.name }), error: false }); done();
  }
  return <article className="panel sms-phone-panel"><h2>{m.devices.bulkTitle}</h2><p className="hint">{devices.length > 1 ? m.devices.bulkHint : m.devices.bulkNone}</p>{devices.length > 1 && <div className="filter-bar"><label>{m.devices.bulkPick}<select value={chosen} disabled={busy} onChange={(e) => { setChosen(e.target.value); setNotice(null); }}><option value="">—</option>{devices.map((device) => <option key={device.id} value={device.id}>{device.name}</option>)}</select></label><button type="button" disabled={busy || !smsPhone} onClick={() => void apply()}>{m.devices.bulkApply}</button></div>}{notice && <p className={"status" + (notice.error ? " error" : "")} role={notice.error ? "alert" : "status"}>{notice.text}</p>}</article>;
}

export function Devices({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const { data, error, loading, reload } = useApi<Page<Record<string, Json>>>("/operations/devices?limit=100", token);
  const [linking, setLinking] = useState(""); const [round, setRound] = useState(0);
  const list = data ? data.items.map((device) => ({ id: String(device.id), name: display(device.name) })) : [];
  return <section><PageHeading title={m.pages.devices[0]} description={m.pages.devices[1]} help={contextHelp.devices} /><div className="page-actions"><AddDevice token={token} added={(device) => { reload(); setLinking(device.id); }} /></div><Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noDevices} />
    {data && data.items.length > 0 && <SmsPhoneForAll token={token} devices={list} done={() => setRound((value) => value + 1)} />}
    {data && <div className="card-list">{data.items.map((item) => { const id = String(item.id); const name = display(item.name); const collects = (item.configuration && typeof item.configuration === "object" && !Array.isArray(item.configuration) ? item.configuration : {}) as Record<string, Json>; const config = Object.entries(collects); return <article className="record-card device-card" key={id} aria-label={name}>
      <header className="device-head"><div><h2>{name}</h2><p className="subhead"><StatusBadge value={String(item.status)} /> · {m.devices.lastSeen} <Timestamp value={item.last_seen_at as string | null} locale={locale} /></p>{phoneNotes(item.health as unknown as PhoneHealth | null | undefined, m.dashboard, locale).map((note) => <small key={note} className="health-note">{note}</small>)}<p>{m.devices.externalId} <Bidi>{display(item.external_device_id)}</Bidi> · {m.devices.appVersion} <Bidi>{display(item.app_version)}</Bidi></p></div>
        <dl><div><dt>{m.devices.configVersion}</dt><dd><Bidi>{display(item.configuration_version)}</Bidi></dd></div><div><dt>{m.devices.transactions}</dt><dd><Bidi>{display(item.transaction_count)}</Bidi></dd></div><div><dt>{m.devices.duplicates}</dt><dd><Bidi>{display(item.duplicate_count)}</Bidi></dd></div><div><dt>{m.devices.needsReview}</dt><dd><Bidi>{display(item.needs_review_count)}</Bidi></dd></div></dl></header>
      <DeviceActions id={id} name={name} status={String(item.status)} token={token} changed={reload} linkOpen={linking === id} setLinkOpen={(open) => setLinking(open ? id : "")} />
      <details className="device-settings" open><summary>{formatMessage(m.devices.settingsFor, { name })}</summary><div className="device-settings-grid">
        <section className="device-config settings-block"><h3>{m.devices.collection}</h3><dl>{config.map(([key, value]) => <div key={key}><dt>{configurationLabel(key, m)}</dt><dd>{typeof value === "boolean" ? value ? m.common.yes : m.common.no : <Bidi>{display(value)}</Bidi>}</dd></div>)}</dl></section>
        <div className="settings-block"><CollectionSettings deviceId={id} token={token} /></div>
        <div className="settings-block"><SmsSources key={id + ":" + round} deviceId={id} token={token} devices={list} /></div>
        {/* Training uses receipt images only; a phone that only collects bank SMS has nothing to train on. */}
        {!(collects.sms_enabled === true && collects.images_enabled !== true) && <div className="settings-block"><TrainingConsent deviceId={id} token={token} /></div>}
      </div></details>
    </article>; })}</div>}</section>;
}

export function HelpPage() {
  const { locale, messages: m } = useI18n(); const [search, setSearch] = useState(""); const topics = useMemo(() => getHelpTopics(locale), [locale]);
  const results = useMemo(() => { const needle = search.trim().toLocaleLowerCase(locale); return needle ? topics.filter((topic) => (topic.title + " " + topic.summary).toLocaleLowerCase(locale).includes(needle)) : topics; }, [locale, search, topics]);
  return <section><PageHeading title={m.pages.help[0]} description={m.pages.help[1]} help={contextHelp.help} /><label className="help-search">{m.helpUi.search}<input value={search} onChange={(e) => setSearch(e.target.value)} /></label><div className="help-cards">{results.map((topic) => <article id={topic.id} className="panel help-card" key={topic.id}><h2>{topic.title}</h2><p className="subhead"><Bidi>{topic.id}</Bidi></p><p>{topic.summary}</p><ol>{topic.steps.map((step) => <li key={step}>{step}</li>)}</ol></article>)}</div>{results.length === 0 && <p className="status empty">{m.helpUi.noMatch}</p>}</section>;
}
