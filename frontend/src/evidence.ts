import type { Json, Transaction } from "./types";

export type EvidenceCandidate = { value: string; normalized_value: string; original_text: string; source: string; source_event_id?: string; confidence?: number | null; adapter?: string | null; adapter_version?: string | null; layout_profile?: string | null; original_polygon?: number[][] };
export type FieldResolution = { field: string; value: string | null; state: "RESOLVED" | "UNRESOLVED"; trust?: string; reason: string; candidates: EvidenceCandidate[] };
export type EvidenceFirst = { schema_version: 1 | 2; state: string; classification_resolved: boolean; supported_layout: boolean; blocking_fields: string[]; fields: Record<string, FieldResolution>; trusted_reference: string | null };
function object(value: Json | undefined): Record<string, Json> | null { return value && typeof value === "object" && !Array.isArray(value) ? value : null; }
function validField(value: Json | undefined): boolean {
  const field = object(value);
  if (!field || !["RESOLVED", "UNRESOLVED"].includes(String(field.state)) || typeof field.reason !== "string" || !Array.isArray(field.candidates) || !(field.value == null || typeof field.value === "string")) return false;
  return field.candidates.every((item) => {
    const candidate = object(item);
    if (!candidate || typeof candidate.value !== "string" || typeof candidate.normalized_value !== "string" || typeof candidate.original_text !== "string" || typeof candidate.source !== "string") return false;
    return candidate.original_polygon == null || (Array.isArray(candidate.original_polygon) && candidate.original_polygon.every((point) => Array.isArray(point) && point.length === 2 && point.every((coordinate) => typeof coordinate === "number" && Number.isFinite(coordinate))));
  });
}
export function evidenceFirst(receipt: Transaction): EvidenceFirst | null {
  const reviewed = object(receipt.reviewed_result); const machine = object(receipt.machine_result);
  const raw = object(object(receipt.resolution_result) ?? reviewed?.evidence_first ?? machine?.evidence_first);
  const fields = object(raw?.fields);
  if (!raw || ![1, 2].includes(Number(raw.schema_version)) || typeof raw.state !== "string" || !Array.isArray(raw.blocking_fields) || !raw.blocking_fields.every((field) => typeof field === "string") || !fields) return null;
  // A malformed field that blocks review makes the record unusable; any other
  // malformed field (e.g. a legacy parser extra) is left out, not fatal.
  const kept: Record<string, Json> = {};
  for (const [name, value] of Object.entries(fields)) {
    if (validField(value)) kept[name] = value;
    else if (raw.blocking_fields.includes(name)) return null;
  }
  return { ...raw, fields: kept } as unknown as EvidenceFirst;
}
export function evidenceFirstPresent(receipt: Transaction): boolean { const value = receipt.resolution_result ?? object(receipt.reviewed_result)?.evidence_first ?? object(receipt.machine_result)?.evidence_first; return value !== undefined && value !== null; }
