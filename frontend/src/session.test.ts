import { describe, expect, it } from "vitest";
import { tokenExpiry } from "./session";

describe("tokenExpiry", () => {
  it("reads exp from a JWT and returns 0 for junk", () => {
    const payload = btoa(JSON.stringify({ sub: "u", exp: 1900000000 })).replace(/=+$/, "");
    expect(tokenExpiry(`h.${payload}.s`)).toBe(1900000000);
    expect(tokenExpiry("not-a-token")).toBe(0);
  });
});
