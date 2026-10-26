import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, query } from "../api";
import type { EvidenceFirst, FieldResolution } from "../evidence";
import { evidenceFirst } from "../evidence";
import { useI18n } from "../i18n";
import type { Page, Transaction } from "../types";
import { Bidi, StatusBadge } from "./Product";

export function ExceptionSummary({ receipt }: { receipt: Transaction }) {
  const { messages: m } = useI18n(); const evidence = evidenceFirst(receipt);
  if (!evidence) return <StatusBadge value="NEEDS_REVIEW" />;
  return <div><StatusBadge value={evidence.state} /><p className="hint">{m.exceptions.count.replace("{count}", String(evidence.blocking_fields.length))}</p>{evidence.blocking_fields.map((name) => <span className="badge warning" key={name}>{(m.exceptions.fields as Record<string, string>)[name] || name}</span>)}</div>;
}

function FieldCard({ resolution, field, busy, submit, focus }: { resolution?: FieldResolution; field: string; busy: boolean; submit: (field: string, action: "CONFIRM" | "CORRECT" | "UNAVAILABLE", value: string | null) => void; focus: (polygon: number[][]) => void }) {
  const { messages: m } = useI18n(); const [selected, setSelected] = useState(""); const [correcting, setCorrecting] = useState(false); const [value, setValue] = useState("");
  const candidates = resolution?.candidates || []; const unique = [...new Set(candidates.map((candidate) => candidate.normalized_value || candidate.value))]; const suggested = unique.length === 1 ? unique[0] : selected;
  const label = (m.exceptions.fields as Record<string, string>)[field] || field;
  return <article className="exception-field"><h3>{label}</h3><p className="hint">{resolution?.reason || m.exceptions.missing}</p>
    {candidates.length > 0 && <fieldset disabled={busy}><legend>{m.exceptions.candidates}</legend>{candidates.map((candidate, index) => <div className="candidate-evidence" key={index}>
      {unique.length > 1 ? <label><input type="radio" name={field} value={candidate.normalized_value || candidate.value} checked={selected === (candidate.normalized_value || candidate.value)} onChange={() => setSelected(candidate.normalized_value || candidate.value)} /><Bidi>{candidate.value}</Bidi></label> : <Bidi>{candidate.value}</Bidi>}
      <small>{candidate.source} · {candidate.adapter} {candidate.adapter_version} · <Bidi>{candidate.source_event_id || "—"}</Bidi></small><p>{candidate.original_text}</p>{candidate.original_polygon && <button type="button" onClick={() => focus(candidate.original_polygon || [])}>{m.exceptions.region}</button>}
    </div>)}</fieldset>}
    <div className="exception-actions"><button type="button" disabled={busy || !suggested} onClick={() => submit(field, "CONFIRM", suggested)}>{m.exceptions.confirm}</button><button type="button" disabled={busy} onClick={() => setCorrecting(!correcting)}>{m.detail.correct}</button><button type="button" disabled={busy} onClick={() => submit(field, "UNAVAILABLE", null)}>{m.exceptions.unavailable}</button></div>
    {correcting && <form onSubmit={(event) => { event.preventDefault(); if (value.trim()) submit(field, "CORRECT", value.trim()); }}><label>{m.exceptions.corrected} {label}<input value={value} maxLength={160} onChange={(event) => setValue(event.target.value)} required /></label><button disabled={busy || !value.trim()} type="submit">{m.exceptions.save}</button></form>}
  </article>;
}

export function ExceptionReview({ receipt, evidence, token, reload, focus }: { receipt: Transaction; evidence: EvidenceFirst; token: string; reload: () => void; focus: (polygon: number[][]) => void }) {
  const { messages: m } = useI18n(); const navigate = useNavigate(); const [busy, setBusy] = useState(false); const [message, setMessage] = useState("");
  const pending = useRef<{ signature: string; id: string } | null>(null); const inFlight = useRef(false);
  async function submit(field: string | undefined, action: "CONFIRM" | "CORRECT" | "UNAVAILABLE" | "REJECT", value: string | null) {
    if (inFlight.current || typeof receipt.updated_at !== "string") return;
    inFlight.current = true; setBusy(true); setMessage("");
    const payload = { expected_updated_at: receipt.updated_at, field, action, value }; const signature = JSON.stringify(payload);
    if (pending.current?.signature !== signature) pending.current = { signature, id: crypto.randomUUID() };
    try { const saved = await api<Transaction>(`/receipts/${receipt.id}/exceptions`, token, { method: "POST", body: JSON.stringify({ command_id: pending.current.id, ...payload }) }); pending.current = null; if (evidenceFirst(saved)?.state === "REVIEW_FINALIZED" || action === "REJECT") { const nextReceipt = await api<Transaction | null>("/receipts/next-exception", token); if (nextReceipt && nextReceipt.id !== receipt.id) navigate(`/transactions/${nextReceipt.id}`); else { reload(); setMessage(m.exceptions.noNext); } } else reload(); }
    catch { inFlight.current = false; setBusy(false); setMessage(m.exceptions.failed); }
  }
  async function next() {
    if (inFlight.current) return; inFlight.current = true; setBusy(true); setMessage(""); pending.current = null;
    try { const queue = await api<Page<Transaction>>("/review-queue" + query({ sort_direction: "asc", limit: 100 }), token); const index = queue.items.findIndex((item) => item.id === receipt.id); const nextReceipt = queue.items.slice(index + 1).find((item) => item.id !== receipt.id) || queue.items.find((item) => item.id !== receipt.id); if (nextReceipt) navigate(`/transactions/${nextReceipt.id}`); else { setMessage(m.exceptions.noNext); inFlight.current = false; setBusy(false); } }
    catch { setMessage(m.exceptions.failed); inFlight.current = false; setBusy(false); }
  }
  const resolved = Object.entries(evidence.fields).filter(([, field]) => field.state === "RESOLVED");
  return <article className="panel wide exception-review"><h2>{m.exceptions.title}</h2><StatusBadge value={evidence.state} /><p className="hint">{m.exceptions.safety}</p>{message && <p role="alert" className="status error">{message}</p>}
    {typeof receipt.updated_at !== "string" && <p role="alert">{m.exceptions.failed}</p>}
    {evidence.blocking_fields.length ? evidence.blocking_fields.map((field) => <FieldCard key={field} field={field} resolution={evidence.fields[field]} busy={busy || typeof receipt.updated_at !== "string"} submit={(name, action, value) => void submit(name, action, value)} focus={focus} />) : <p role="status">{m.exceptions.complete}</p>}
    <details><summary>{m.exceptions.resolved}</summary><dl className="fact-list">{resolved.map(([name, field]) => <div key={name}><dt>{(m.exceptions.fields as Record<string, string>)[name] || name}</dt><dd><Bidi>{field.value || "—"}</Bidi></dd></div>)}</dl></details>
    <div className="exception-actions"><button type="button" disabled={busy || typeof receipt.updated_at !== "string"} onClick={() => void submit(undefined, "REJECT", null)}>{m.detail.reject}</button><button type="button" disabled={busy} onClick={() => void next()}>{m.common.next}</button></div>
  </article>;
}
