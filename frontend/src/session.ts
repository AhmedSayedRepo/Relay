import { useEffect, useRef } from "react";

/** Seconds since epoch when the JWT expires, or 0 if it can't be read. */
export function tokenExpiry(token: string): number {
  return claim(token, "exp");
}

function claim(token: string, name: "exp" | "iat"): number {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))) as Record<string, unknown>;
    const value = payload[name];
    return typeof value === "number" ? value : 0;
  } catch {
    return 0;
  }
}

const RENEW_BEFORE_SECONDS = 5 * 60;
const IDLE_LIMIT_MS = 12 * 60 * 60 * 1000;

/** Ask the server for a fresh access token from the httpOnly session cookie.
 * Returns "" when there is no live session. */
export async function resumeSession(): Promise<string> {
  try {
    const response = await fetch("/api/v1/auth/session", { method: "POST", credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) return "";
    const body = (await response.json()) as { access_token?: unknown };
    return typeof body.access_token === "string" ? body.access_token : "";
  } catch {
    return "";
  }
}

export function endSession(): void {
  void fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" }).catch(() => undefined);
}

/** Keep an active operator signed in. The access token stays in memory only;
 * the httpOnly session cookie renews it before it expires, after a reload,
 * and when a phone wakes the browser up again. */
export function useSessionKeepAlive(token: string, setToken: (value: string) => void) {
  const lastActive = useRef(0);
  const skew = useRef(0);
  useEffect(() => {
    const mark = () => { lastActive.current = Date.now(); };
    mark();
    const events = ["pointerdown", "keydown", "scroll", "touchstart"] as const;
    events.forEach((name) => window.addEventListener(name, mark, { passive: true }));
    return () => events.forEach((name) => window.removeEventListener(name, mark));
  }, []);
  useEffect(() => {
    // The server's clock decides expiry; correct for a phone whose clock is off.
    const issued = claim(token, "iat");
    skew.current = issued ? issued - Date.now() / 1000 : 0;
  }, [token]);
  useEffect(() => {
    if (!token) return;
    let stopped = false;
    async function tick(force = false) {
      const left = tokenExpiry(token) - (Date.now() / 1000 + skew.current);
      if (stopped || Date.now() - lastActive.current > IDLE_LIMIT_MS) return;
      if (!force && left > RENEW_BEFORE_SECONDS) return;
      const next = await resumeSession();
      if (stopped) return;
      if (next) setToken(next);
      else if (left <= 0) setToken("");
    }
    const visible = () => { if (document.visibilityState === "visible") { lastActive.current = Date.now(); void tick(); } };
    const timer = window.setInterval(() => void tick(), 60 * 1000);
    document.addEventListener("visibilitychange", visible);
    void tick();
    return () => { stopped = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [token, setToken]);
}
