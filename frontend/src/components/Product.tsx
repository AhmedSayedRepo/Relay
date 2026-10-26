import type { ReactNode } from "react";
import { useI18n } from "../i18n";
import type { Transaction } from "../types";
import { evidenceFirst, evidenceFirstPresent } from "../evidence";

export function Bidi({ children, className = "" }: { children: ReactNode; className?: string }) { return <bdi dir="ltr" className={`data-text ${className}`.trim()}>{children}</bdi>; }

export function StatusBadge({ value, tone }: { value: string; tone?: "warning" | "danger" | "success" | "neutral" }) {
  const { messages: m } = useI18n();
  const label = (m.statuses as Record<string, string>)[value] || value.replaceAll("_", " ");
  const derived = tone || (["FAILED", "REJECTED", "PROCESSING_FAILED", "TRANSFER_FAILED"].includes(value) ? "danger" : value === "NEEDS_REVIEW" || value === "UNCERTAIN" ? "warning" : ["SUCCEEDED", "AUTO_ACCEPTED", "AUTO_FINALIZED", "REVIEW_FINALIZED", "ACTIVE"].includes(value) ? "success" : "neutral");
  return <span className={`badge ${derived}`}><span aria-hidden="true" className="status-dot" />{label}</span>;
}

export function ReceiptJourney({ transaction }: { transaction: Transaction }) {
  const { messages: m } = useI18n();
  const processed = ["SUCCEEDED", "NEEDS_REVIEW", "FAILED", "SKIPPED"].includes(transaction.processing_status);
  const evidence = evidenceFirst(transaction); const final = evidence ? ["AUTO_FINALIZED", "REVIEW_FINALIZED", "TRANSFER_FAILED"].includes(evidence.state) : ["AUTO_ACCEPTED", "ACCEPTED", "CORRECTED", "REJECTED"].includes(transaction.review_status);
  const duplicateChecked = evidenceFirstPresent(transaction) ? Boolean(evidence?.trusted_reference && (transaction.is_canonical || transaction.duplicate_group_id || transaction.duplicate_of_candidate_id)) : Boolean(transaction.duplicate_group_id || transaction.is_canonical || processed);
  const stages = [[m.journey.collected, true], [m.journey.uploaded, Boolean(transaction.object_id)], [m.journey.processed, processed], [m.journey.ocr, Boolean(transaction.ocr_mean_confidence !== null || transaction.reference_original || transaction.amount)], [m.journey.duplicate, duplicateChecked], [evidence ? m.journey.resolved : m.journey.reviewed, evidence ? evidence.blocking_fields.length === 0 && final : transaction.review_status !== "NEEDS_REVIEW"], [m.journey.final, final]] as const;
  const firstIncomplete = stages.findIndex(([, complete]) => !complete);
  return <section className="journey" aria-labelledby="journey-title"><div><h2 id="journey-title">{m.journey.title}</h2><p className="hint">{m.journey.description}</p></div><ol>{stages.map(([label, complete], index) => { const blocked = transaction.processing_status === "FAILED" && index >= firstIncomplete; const state = complete ? m.journey.complete : blocked ? m.journey.blocked : index === firstIncomplete ? m.journey.current : m.journey.pending; return <li className={complete ? "complete" : blocked ? "blocked" : "pending"} key={label}><span aria-hidden="true" /><div><strong>{label}</strong><small>{state}</small></div></li>; })}</ol></section>;
}
