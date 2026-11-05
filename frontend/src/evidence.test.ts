import { describe, expect, it } from "vitest";
import { evidenceFirst } from "./evidence";
import type { Transaction } from "./types";

const field = (name: string) => ({ field: name, value: null, state: "UNRESOLVED", reason: "operator_review_required", candidates: [] });

describe("evidenceFirst", () => {
  it("drops malformed non-blocking fields instead of hiding the review", () => {
    const receipt = { id: "r1", resolution_result: { schema_version: 2, state: "NEEDS_REVIEW", blocking_fields: ["amount"], fields: { amount: field("amount"), transaction_date: { field: "transaction_date", value: "2026-10-27" } } } } as unknown as Transaction;
    const parsed = evidenceFirst(receipt);
    expect(parsed?.fields.amount.state).toBe("UNRESOLVED");
    expect(parsed?.fields.transaction_date).toBeUndefined();
  });

  it("still rejects a malformed blocking field", () => {
    const receipt = { id: "r2", resolution_result: { schema_version: 2, state: "NEEDS_REVIEW", blocking_fields: ["amount"], fields: { amount: { field: "amount", value: 5 } } } } as unknown as Transaction;
    expect(evidenceFirst(receipt)).toBeNull();
  });
});
