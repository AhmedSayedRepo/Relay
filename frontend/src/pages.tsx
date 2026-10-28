import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, downloadExport, query } from "./api";
import { DateFields, PageHeading, Pager, Status } from "./components/Layout";
import { Bidi, ReceiptJourney, StatusBadge } from "./components/Product";
import { ExceptionReview, ExceptionSummary } from "./components/ExceptionReview";
import { evidenceFirst } from "./evidence";
import { contextHelp, getHelpTopics } from "./help/topics";
import { defaultDates, useApi } from "./hooks";
import { formatMessage, useI18n, type Locale } from "./i18n";
import type { Messages } from "./i18n/en";
import type { DashboardData, DuplicateGroup, Job, Json, Page, Transaction } from "./types";

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

export function Dashboard({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const initial = defaultDates();
  const [dateFrom, setDateFrom] = useState(initial.dateFrom); const [dateTo, setDateTo] = useState(initial.dateTo);
  const { data, error, loading } = useApi<DashboardData>("/dashboard" + query({ date_from: dateFrom, date_to: dateTo }), token);
  const cards = data ? [[m.dashboard.finalTransactions, data.final_transaction_count, "money"], [m.dashboard.reviewDemand, data.needs_review_count, "review"], [m.dashboard.duplicateGroups, data.duplicate_group_count, "duplicate"], [m.dashboard.failedProcessing, data.processing_failure_count, "processing"]] as const : [];
  return <section><PageHeading title={m.pages.dashboard[0]} description={m.pages.dashboard[1]} help={contextHelp.dashboard} />
    <div className="filter-bar"><DateFields {...{ dateFrom, dateTo, setDateFrom, setDateTo }} /></div><p className="hint">{m.common.dateBasis}</p>
    <Status loading={loading} error={error} />{data && <><div className="metric-grid">{cards.map(([label, value, kind]) => <article className={"metric " + kind} key={label}><span>{label}</span><strong><Bidi>{value}</Bidi></strong></article>)}</div>
      <div className="two-column"><article className="panel money-panel"><div className="section-heading"><div><p className="eyebrow">{m.dashboard.moneyIntro}</p><h2>{m.dashboard.moneyTitle}</h2></div></div>{data.totals_by_currency.length ? <div className="table-wrap flush"><table><thead><tr><th>{m.dashboard.currency}</th><th>{m.dashboard.transactions}</th><th>{m.dashboard.total}</th></tr></thead><tbody>{data.totals_by_currency.map((item) => <tr key={item.currency}><td data-label={m.dashboard.currency}><Bidi>{item.currency}</Bidi></td><td data-label={m.dashboard.transactions}><Bidi>{item.transaction_count}</Bidi></td><td data-label={m.dashboard.total}><Amount amount={item.amount} currency={item.currency} locale={locale} /></td></tr>)}</tbody></table></div> : <p className="empty-inline">{m.dashboard.noTotals}</p>}</article>
      <article className="panel pulse-panel"><div className="section-heading"><div><p className="eyebrow">{m.dashboard.deviceHealth}</p><h2>{m.dashboard.activity}</h2></div><span className="pulse" aria-hidden="true" /></div><dl className="fact-list"><div><dt>{m.dashboard.receiptImages}</dt><dd><Bidi>{data.receipt_image_activity}</Bidi></dd></div><div><dt>{m.dashboard.smsEvents}</dt><dd><Bidi>{data.sms_activity}</Bidi></dd></div><div><dt>{m.dashboard.pendingProcessing}</dt><dd><Bidi>{data.pending_processing_count}</Bidi></dd></div><div><dt>{m.dashboard.reviewed}</dt><dd><Bidi>{data.reviewed_count}</Bidi></dd></div></dl><p className="quiet-signal">{m.dashboard.quiet}</p></article></div></>}
  </section>;
}

export function Transactions({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const initial = defaultDates();
  const [dateFrom, setDateFrom] = useState(initial.dateFrom); const [dateTo, setDateTo] = useState(initial.dateTo);
  const [search, setSearch] = useState(""); const [review, setReview] = useState(""); const [duplicate, setDuplicate] = useState("");
  const [currency, setCurrency] = useState(""); const [sort, setSort] = useState("created_at"); const [direction, setDirection] = useState("desc"); const [offset, setOffset] = useState(0); const limit = 25;
  const path = "/transactions" + query({ date_from: dateFrom, date_to: dateTo, search: search || undefined, review_status: review || undefined, duplicate_status: duplicate || undefined, currency: currency || undefined, sort_by: sort, sort_direction: direction, limit, offset });
  const { data, error, loading } = useApi<Page<Transaction>>(path, token);
  return <section><PageHeading title={m.pages.transactions[0]} description={m.pages.transactions[1]} help={contextHelp.transactions} />
    <div className="filter-bar"><label className="grow">{m.filters.search}<input placeholder={m.filters.searchTransactions} value={search} maxLength={64} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} /></label><DateFields {...{ dateFrom, dateTo, setDateFrom, setDateTo }} /><label>{m.filters.review}<select value={review} onChange={(e) => setReview(e.target.value)}><option value="">{m.common.all}</option><option value="NEEDS_REVIEW">{m.statuses.NEEDS_REVIEW}</option><option value="AUTO_ACCEPTED">{m.statuses.AUTO_ACCEPTED}</option><option value="REJECTED">{m.statuses.REJECTED}</option></select></label><label>{m.filters.duplicate}<select value={duplicate} onChange={(e) => setDuplicate(e.target.value)}><option value="">{m.common.all}</option><option value="DUPLICATE">{m.transactions.duplicateItem}</option><option value="CANONICAL">{m.transactions.canonical}</option><option value="UNIQUE">{m.transactions.unique}</option></select></label><label>{m.filters.currency}<input className="short" dir="ltr" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label><label>{m.filters.sort}<select value={sort} onChange={(e) => setSort(e.target.value)}><option value="created_at">{m.filters.relayEntry}</option><option value="transaction_occurred_at">{m.filters.transactionDate}</option><option value="amount">{m.filters.amount}</option><option value="reference">{m.filters.reference}</option><option value="review_status">{m.filters.review}</option></select></label><label>{m.filters.order}<select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="desc">{m.filters.newest}</option><option value="asc">{m.filters.oldest}</option></select></label></div>
    <p className="hint">{m.filters.applyHint}</p><Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noTransactions} />{data && data.total > 0 && <div className="table-wrap"><table className="responsive-table"><thead><tr><th>{m.transactions.relayEntry}</th><th>{m.transactions.reference}</th><th>{m.transactions.amount}</th><th>{m.transactions.parties}</th><th>{m.transactions.review}</th><th>{m.transactions.duplicate}</th><th>{m.sms.column}</th><th>{m.transactions.device}</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.id}><td data-label={m.transactions.relayEntry}><Timestamp value={item.created_at} locale={locale} /></td><td data-label={m.transactions.reference}><Link aria-label={m.transactions.openEvidence + " " + (item.reference_original || item.id)} to={"/transactions/" + item.id}><Bidi>{item.reference_original || m.transactions.noReference}</Bidi></Link></td><td data-label={m.transactions.amount}><Amount amount={item.amount} currency={item.currency} locale={locale} /></td><td data-label={m.transactions.parties}><span>{item.sender_name || dash}</span><br /><span className="muted">{m.transactions.to} {item.recipient_name || dash}</span></td><td data-label={m.transactions.review}><StatusBadge value={item.review_status} /></td><td data-label={m.transactions.duplicate}>{item.is_canonical ? m.transactions.canonical : item.duplicate_of_candidate_id ? m.transactions.duplicateItem : m.transactions.unique}</td><td data-label={m.sms.column}><SmsBadge value={item.sms_verification} /></td><td data-label={m.transactions.device}>{item.device_name}</td></tr>)}</tbody></table></div>}{data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}
  </section>;
}

export function TransactionDetail({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const { id = "" } = useParams();
  const { data, error, loading, reload } = useApi<Transaction & Record<string, Json>>("/transactions/" + id, token);
  const [region, setRegion] = useState<{ receiptId: string; polygon: number[][] } | null>(null); const evidence = data ? evidenceFirst(data) : null;
  const facts = data ? [[m.detail.reference, <Bidi>{data.reference_original || dash}</Bidi>], [m.detail.amount, <Amount amount={data.amount} currency={data.currency} locale={locale} />], [m.detail.transactionTime, <Timestamp value={data.transaction_occurred_at} locale={locale} />], [m.detail.relayEntry, <Timestamp value={data.created_at} locale={locale} />], [m.detail.review, <StatusBadge value={data.review_status} />], [m.detail.processing, <StatusBadge value={data.processing_status} />], [m.detail.device, data.device_name], [m.detail.source, data.source_type], [m.detail.confidence, <Bidi>{data.ocr_mean_confidence === null ? dash : Math.round(data.ocr_mean_confidence * 100) + "%"}</Bidi>]] : [];
  return <section><PageHeading title={m.pages.transactionDetail[0]} description={m.pages.transactionDetail[1]} help={contextHelp.transactionDetail} /><Status loading={loading} error={error} />{data && data.id === id && <><ReceiptJourney transaction={data} /><div className="detail-grid">
    <article className="panel receipt-panel"><h2>{m.detail.receiptImage}</h2><ProtectedImage key={data.id} path={data.image_url} token={token} polygon={region?.receiptId === data.id ? region.polygon : []} alt={formatMessage(m.detail.receiptAlt, { reference: data.reference_original || data.id })} /></article>
    <article className="panel final-panel"><p className="eyebrow">{m.detail.reviewedPriority}</p><h2>{m.detail.reviewedTitle}</h2><dl className="fact-list">{facts.map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>{data.duplicate_group_id && <Link className="button-link" to={"/duplicates/" + data.duplicate_group_id}>{m.detail.openDuplicate}</Link>}</article>
    {evidence ? <ExceptionReview key={data.id + String(data.updated_at)} receipt={data} evidence={evidence} token={token} reload={reload} focus={(polygon) => setRegion({ receiptId: data.id, polygon })} /> : <p className="panel wide status error" role="alert">{m.exceptions.failed}</p>}
    <SmsVerificationPanel key={"sms-" + data.id + String(data.updated_at)} id={data.id} token={token} />
    <details className="panel wide evidence-details"><summary>{m.detail.showEvidence}</summary><h2>{m.detail.machine}</h2><p className="hint">{m.detail.machineHint}</p><h3>{m.detail.provenance}</h3><pre dir="ltr">{display(data.ocr)}</pre><h3>{m.detail.history}</h3><pre dir="ltr">{display({ review_history: data.review_history, duplicate_resolution_history: data.duplicate_resolution_history })}</pre></details>
  </div></>}</section>;
}

export function Duplicates({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const initial = defaultDates(); const [dateFrom, setDateFrom] = useState(initial.dateFrom); const [dateTo, setDateTo] = useState(initial.dateTo); const [search, setSearch] = useState(""); const [offset, setOffset] = useState(0); const limit = 25;
  const { data, error, loading } = useApi<Page<DuplicateGroup>>("/duplicate-groups" + query({ date_from: dateFrom, date_to: dateTo, search: search || undefined, limit, offset }), token);
  return <section><PageHeading title={m.pages.duplicates[0]} description={m.pages.duplicates[1]} help={contextHelp.duplicates} /><div className="filter-bar"><label className="grow">{m.filters.searchReferences}<input value={search} onChange={(e) => setSearch(e.target.value)} /></label><DateFields {...{ dateFrom, dateTo, setDateFrom, setDateTo }} /></div><Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noDuplicates} />{data && data.total > 0 && <div className="card-list">{data.items.map((group) => <article className="record-card duplicate-row" key={group.group_id}><div><p className="eyebrow">{formatMessage(m.duplicates.groupSize, { count: group.group_size })} · {group.device_name}</p><h2><Link to={"/duplicates/" + group.group_id}><Bidi>{group.reference_original || m.transactions.noReference}</Bidi></Link></h2><p><Amount amount={group.amount} currency={group.currency} locale={locale} /> · {m.duplicates.canonical} <Bidi>{group.canonical_candidate_id}</Bidi></p></div><div><StatusBadge value="DUPLICATE" tone="warning" /><p className="hint">{m.duplicates.evidencePreserved}</p></div></article>)}</div>}{data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}</section>;
}

export function DuplicateDetail({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const { id = "" } = useParams(); const [offset, setOffset] = useState(0); const limit = 20; const [message, setMessage] = useState(""); const [messageError, setMessageError] = useState(false);
  const { data, error, loading, reload } = useApi<{ group_id: string; group_size: number; members: Transaction[] }>("/duplicate-groups/" + id + query({ limit, offset }), token);
  async function decide(candidateId: string, form: FormData) { setMessage(""); setMessageError(false); try { await api("/duplicate-groups/" + id + "/members/" + candidateId + "/resolution", token, { method: "POST", body: JSON.stringify({ decision: form.get("decision"), note: form.get("note") || null }) }); setMessage(m.duplicates.recorded); reload(); } catch { setMessage(m.duplicates.failed); setMessageError(true); } }
  const canonical = data?.members.find((item) => item.is_canonical) || data?.members[0];
  function difference(value: string | null, base: string | null) { return value === base ? m.duplicates.matches : m.duplicates.differs; }
  return <section><PageHeading title={m.pages.duplicateDetail[0]} description={m.pages.duplicateDetail[1]} help={contextHelp.duplicateDetail} /><div className="reason-banner"><strong>{m.duplicates.reason}</strong><p>{m.duplicates.referenceReason}</p><span>{m.duplicates.evidencePreserved}</span></div><Status loading={loading} error={error} />{message && <p className={"status" + (messageError ? " error" : "")} role={messageError ? "alert" : "status"}>{message}</p>}{data && <><div className="compare-grid">{data.members.map((item) => <article className={"panel comparison-card" + (item.is_canonical ? " canonical" : "")} key={item.id}><p className="eyebrow">{item.is_canonical ? m.duplicates.canonical : m.duplicates.candidate}</p><h2><Bidi>{item.reference_original || m.transactions.noReference}</Bidi></h2><ProtectedImage path={item.image_url} token={token} alt={formatMessage(m.detail.receiptAlt, { reference: item.reference_original || item.id })} /><dl className="fact-list compare-facts"><div><dt>{m.detail.amount}</dt><dd><Amount amount={item.amount} currency={item.currency} locale={locale} />{!item.is_canonical && <small className={difference(item.amount, canonical?.amount || null) === m.duplicates.differs ? "difference" : "match"}>{difference(item.amount, canonical?.amount || null)}</small>}</dd></div><div><dt>{m.detail.transactionTime}</dt><dd><Timestamp value={item.transaction_occurred_at} locale={locale} /></dd></div><div><dt>{m.duplicates.received}</dt><dd><Timestamp value={item.received_at} locale={locale} /></dd></div><div><dt>{m.duplicates.sender}</dt><dd>{item.sender_name || dash}{!item.is_canonical && <small className={difference(item.sender_name, canonical?.sender_name || null) === m.duplicates.differs ? "difference" : "match"}>{difference(item.sender_name, canonical?.sender_name || null)}</small>}</dd></div><div><dt>{m.duplicates.recipient}</dt><dd>{item.recipient_name || dash}</dd></div><div><dt>{m.detail.confidence}</dt><dd><Bidi>{item.ocr_mean_confidence === null ? dash : Math.round(item.ocr_mean_confidence * 100) + "%"}</Bidi></dd></div></dl><Link to={"/transactions/" + item.id}>{m.duplicates.openFull}</Link><form className="decision-form" action={(form) => void decide(item.id, form)}><label>{m.duplicates.operatorDecision}<select name="decision" defaultValue="UNRESOLVED"><option value="UNRESOLVED">{m.duplicates.unresolved}</option><option value="CONFIRMED_DUPLICATE">{m.duplicates.confirmed}</option><option value="NOT_DUPLICATE">{m.duplicates.notDuplicate}</option></select></label><label>{m.duplicates.note}<textarea name="note" maxLength={500} /></label><button type="submit">{m.duplicates.record}</button></form></article>)}</div><Pager total={data.group_size} limit={limit} offset={offset} setOffset={setOffset} /></>}</section>;
}

export function ReviewQueue({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const initial = defaultDates(); const [dateFrom, setDateFrom] = useState(initial.dateFrom); const [dateTo, setDateTo] = useState(initial.dateTo); const [search, setSearch] = useState(""); const [direction, setDirection] = useState("asc"); const [offset, setOffset] = useState(0); const limit = 25;
  const { data, error, loading } = useApi<Page<Transaction>>("/review-queue" + query({ date_from: dateFrom, date_to: dateTo, search: search || undefined, sort_direction: direction, limit, offset }), token);
  return <section><PageHeading title={m.pages.review[0]} description={m.pages.review[1]} help={contextHelp.review} /><p className="queue-guidance">{m.review.oldestHint}</p><div className="filter-bar"><label className="grow">{m.filters.search}<input value={search} maxLength={64} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} /></label><DateFields {...{ dateFrom, dateTo, setDateFrom, setDateTo }} /><label>{m.filters.order}<select value={direction} onChange={(e) => setDirection(e.target.value)}><option value="asc">{m.filters.oldest}</option><option value="desc">{m.filters.newest}</option></select></label></div><Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noReview} />{data && data.total > 0 && <div className="card-list">{data.items.map((item) => <article className="record-card review-row" key={item.id}><div><p className="eyebrow"><Timestamp value={item.created_at} locale={locale} /> · {item.device_name}</p><h2><Link to={"/transactions/" + item.id}><Bidi>{item.reference_original || m.review.needsReview}</Bidi></Link></h2><p><Amount amount={item.amount} currency={item.currency} locale={locale} /> · {item.receipt_type}</p></div><ExceptionSummary receipt={item} /></article>)}</div>}{data && <Pager total={data.total} limit={limit} offset={offset} setOffset={setOffset} />}</section>;
}

export function ProcessingQueue({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const [status, setStatus] = useState(""); const [message, setMessage] = useState(""); const [messageError, setMessageError] = useState(false); const [offset, setOffset] = useState(0); const limit = 25;
  const { data, error, loading, reload } = useApi<{ items: Job[]; has_more: boolean }>("/processing/jobs" + query({ status: status || undefined, limit, offset }), token); const retryable = new Set(["OCR_TIMEOUT", "OCR_FAILED", "INTERNAL_ERROR"]);
  async function retry(id: string) { setMessage(""); setMessageError(false); try { await api("/processing/jobs/" + id + "/retry", token, { method: "POST" }); setMessage(m.processing.retryQueued); reload(); } catch { setMessage(m.processing.retryFailed); setMessageError(true); } }
  return <section><PageHeading title={m.pages.processing[0]} description={m.pages.processing[1]} help={contextHelp.processing} /><div className="filter-bar"><label>{m.filters.status}<select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="">{m.common.all}</option>{["PENDING", "PROCESSING", "SUCCEEDED", "NEEDS_REVIEW", "FAILED", "SKIPPED"].map((item) => <option value={item} key={item}>{(m.statuses as Record<string, string>)[item]}</option>)}</select></label></div>{message && <p className={"status" + (messageError ? " error" : "")} role={messageError ? "alert" : "status"}>{message}</p>}<Status loading={loading} error={error} empty={data?.items.length === 0} emptyMessage={m.state.noProcessing} />{data && data.items.length > 0 && <><div className="table-wrap"><table className="responsive-table"><thead><tr><th>{m.filters.status}</th><th>{m.processing.object}</th><th>{m.processing.failure}</th><th>{m.processing.attempts}</th><th>{m.processing.nextAttempt}</th><th>{m.processing.action}</th></tr></thead><tbody>{data.items.map((job) => { const allowed = job.status === "FAILED" && Boolean(job.failure_code) && retryable.has(job.failure_code || "") && job.attempt_count < 5; return <tr key={job.id}><td data-label={m.filters.status}><StatusBadge value={job.status} /></td><td data-label={m.processing.object}><Bidi>{job.object_id}</Bidi></td><td data-label={m.processing.failure}>{job.failure_code ? (m.failures as Record<string, string>)[job.failure_code] || m.failures.default : dash}</td><td data-label={m.processing.attempts}><Bidi>{job.attempt_count + " / " + job.max_attempts}</Bidi></td><td data-label={m.processing.nextAttempt}><Timestamp value={job.next_attempt_at} locale={locale} /></td><td data-label={m.processing.action}>{allowed ? <button type="button" onClick={() => void retry(job.id)}>{m.processing.retry}</button> : <span className="muted">{m.processing.noAction}</span>}</td></tr>; })}</tbody></table></div><nav className="pager" aria-label={m.common.page}><span><Bidi>{m.common.page + " " + (Math.floor(offset / limit) + 1)}</Bidi></span><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>{m.common.previous}</button><button type="button" disabled={!data.has_more} onClick={() => setOffset(offset + limit)}>{m.common.next}</button></nav></>}</section>;
}

export function Reports({ token }: { token: string }) {
  const { messages: m } = useI18n(); const initial = defaultDates(); const [dateFrom, setDateFrom] = useState(initial.dateFrom); const [dateTo, setDateTo] = useState(initial.dateTo); const [currency, setCurrency] = useState(""); const [message, setMessage] = useState(""); const [messageError, setMessageError] = useState(false);
  const path = query({ date_from: dateFrom, date_to: dateTo, currency: currency || undefined }); const { data, error, loading } = useApi<DashboardData>("/reports/summary" + query({ date_from: dateFrom, date_to: dateTo }), token);
  async function download() { setMessage(m.reports.preparing); setMessageError(false); try { await downloadExport("/reports/export.xlsx" + path, token); setMessage(m.reports.downloaded); } catch { setMessage(m.reports.failed); setMessageError(true); } }
  return <section><PageHeading title={m.pages.reports[0]} description={m.pages.reports[1]} help={contextHelp.reports} /><div className="filter-bar"><DateFields {...{ dateFrom, dateTo, setDateFrom, setDateTo }} /><label>{m.filters.currency}<input className="short" dir="ltr" maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} /></label><button type="button" onClick={() => void download()}>{m.reports.export}</button></div>{message && <p role={messageError ? "alert" : "status"} className={"status" + (messageError ? " error" : "")}>{message}</p>}<Status loading={loading} error={error} />{data && <article className="panel report-scope"><h2>{m.reports.scope}</h2><p className="scope-count"><Bidi>{formatMessage(m.reports.scopeLine, { transactions: data.final_transaction_count, duplicates: data.duplicate_group_count, review: data.needs_review_count })}</Bidi></p><p className="hint">{m.reports.contents}</p></article>}</section>;
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
  return <article className="panel wide sms-panel" aria-labelledby="sms-title"><div className="section-heading"><div><p className="eyebrow">{m.sms.eyebrow}</p><h2 id="sms-title">{m.sms.title}</h2></div><SmsBadge value={current.status} /></div>
    <p className="hint">{(m.sms.explain as Record<string, string>)[String(current.status)] || ""}</p>
    {current.sms && <dl className="fact-list"><div><dt>{m.sms.amount}</dt><dd><Amount amount={current.sms.amount} currency={current.sms.currency} locale={locale} /></dd></div><div><dt>{m.sms.time}</dt><dd><Timestamp value={current.sms.occurred_at} locale={locale} /></dd></div><div><dt>{m.sms.method}</dt><dd>{current.method === "MANUAL" ? m.sms.manual : m.sms.auto}</dd></div></dl>}
    {options.length > 0 && <><h3>{m.sms.options}</h3><ul className="sms-options">{options.map((option) => <li key={option.event_id}><span><Amount amount={option.amount} currency={option.currency} locale={locale} /> · <Timestamp value={option.occurred_at} locale={locale} />{option.in_window === false && <span className="muted"> · {m.sms.outsideWindow}</span>}</span><button disabled={busy} onClick={() => void act({ method: "PUT", body: JSON.stringify({ sms_event_id: option.event_id }) })}>{m.sms.use}</button></li>)}</ul></>}
    <div className="sms-actions"><button className="quiet-button" disabled={busy} onClick={() => void act({ method: "POST" }, "/verification/sms/run")}>{m.sms.recheck}</button>{current.method && <button className="quiet-button" disabled={busy} onClick={() => void act({ method: "DELETE" })}>{m.sms.clear}</button>}</div>
    {failed && <p className="status error" role="alert">{m.sms.failed}</p>}
  </article>;
}

export function SmsSources({ deviceId, token, devices }: { deviceId: string; token: string; devices: { id: string; name: string }[] }) {
  const { messages: m } = useI18n(); const path = "/devices/" + encodeURIComponent(deviceId) + "/sms-sources";
  const { data, error, loading } = useApi<{ sms_device_ids: string[] }>(path, token);
  const [chosen, setChosen] = useState<string[] | null>(null); const [busy, setBusy] = useState(false); const [failed, setFailed] = useState(false);
  const current = chosen ?? (Array.isArray(data?.sms_device_ids) ? data.sms_device_ids : []); const others = devices.filter((device) => device.id !== deviceId);
  async function toggle(id: string, on: boolean) {
    const next = on ? [...current, id] : current.filter((value) => value !== id);
    setBusy(true); setFailed(false);
    try { const result = await api<{ sms_device_ids: string[] }>(path, token, { method: "PUT", body: JSON.stringify({ sms_device_ids: next }) }); setChosen(result.sms_device_ids); }
    catch { setFailed(true); }
    finally { setBusy(false); }
  }
  return <section className="training-consent"><h3>{m.sms.sourcesTitle}</h3>{loading ? <p className="hint">{m.sms.loading}</p> : error ? <p className="status error" role="alert">{m.sms.unavailable}</p> : others.length === 0 ? <p className="hint">{m.sms.noOtherDevices}</p> : <><p className="hint">{m.sms.sourcesHint}</p>{others.map((device) => <label className="toggle-row" key={device.id}><input type="checkbox" checked={current.includes(device.id)} disabled={busy} onChange={(e) => void toggle(device.id, e.target.checked)} />{device.name}</label>)}{failed && <p className="status error" role="alert">{m.sms.failed}</p>}</>}</section>;
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

export function Devices({ token }: { token: string }) {
  const { locale, messages: m } = useI18n(); const { data, error, loading } = useApi<Page<Record<string, Json>>>("/operations/devices?limit=100", token);
  return <section><PageHeading title={m.pages.devices[0]} description={m.pages.devices[1]} help={contextHelp.devices} /><Status loading={loading} error={error} empty={data?.total === 0} emptyMessage={m.state.noDevices} />{data && <div className="card-list">{data.items.map((item) => { const config = item.configuration && typeof item.configuration === "object" && !Array.isArray(item.configuration) ? Object.entries(item.configuration) : []; return <article className="record-card device-card" key={String(item.id)}><div><p className="eyebrow"><StatusBadge value={String(item.status)} /> · {m.devices.lastSeen} <Timestamp value={item.last_seen_at as string | null} locale={locale} /></p><h2>{display(item.name)}</h2><p>{m.devices.externalId} <Bidi>{display(item.external_device_id)}</Bidi> · {m.devices.appVersion} <Bidi>{display(item.app_version)}</Bidi></p></div><dl><div><dt>{m.devices.configVersion}</dt><dd><Bidi>{display(item.configuration_version)}</Bidi></dd></div><div><dt>{m.devices.transactions}</dt><dd><Bidi>{display(item.transaction_count)}</Bidi></dd></div><div><dt>{m.devices.duplicates}</dt><dd><Bidi>{display(item.duplicate_count)}</Bidi></dd></div><div><dt>{m.devices.needsReview}</dt><dd><Bidi>{display(item.needs_review_count)}</Bidi></dd></div></dl><section className="device-config"><h3>{m.devices.collection}</h3><dl>{config.map(([key, value]) => <div key={key}><dt>{configurationLabel(key, m)}</dt><dd>{typeof value === "boolean" ? value ? m.common.yes : m.common.no : <Bidi>{display(value)}</Bidi>}</dd></div>)}</dl></section><TrainingConsent deviceId={String(item.id)} token={token} /><SmsSources deviceId={String(item.id)} token={token} devices={data.items.map((device) => ({ id: String(device.id), name: display(device.name) }))} /></article>; })}</div>}</section>;
}

export function HelpPage() {
  const { locale, messages: m } = useI18n(); const [search, setSearch] = useState(""); const topics = useMemo(() => getHelpTopics(locale), [locale]);
  const results = useMemo(() => { const needle = search.trim().toLocaleLowerCase(locale); return needle ? topics.filter((topic) => (topic.title + " " + topic.summary).toLocaleLowerCase(locale).includes(needle)) : topics; }, [locale, search, topics]);
  return <section><PageHeading title={m.pages.help[0]} description={m.pages.help[1]} help={contextHelp.help} /><label className="help-search">{m.helpUi.search}<input value={search} onChange={(e) => setSearch(e.target.value)} /></label><div className="help-cards">{results.map((topic) => <article id={topic.id} className="panel help-card" key={topic.id}><p className="eyebrow"><Bidi>{topic.id}</Bidi></p><h2>{topic.title}</h2><p>{topic.summary}</p><ol>{topic.steps.map((step) => <li key={step}>{step}</li>)}</ol></article>)}</div>{results.length === 0 && <p className="status empty">{m.helpUi.noMatch}</p>}</section>;
}
