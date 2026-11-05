import { useEffect, useRef } from "react";

/** Seconds since epoch when the JWT expires, or 0 if it can't be read. */
export function tokenExpiry(token: string): number {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))) as { exp?: number };
    return typeof payload.exp === "number" ? payload.exp : 0;
  } catch {
    return 0;
  }
}

const RENEW_BEFORE_SECONDS = 5 * 60;
const IDLE_LIMIT_MS = 30 * 60 * 1000;

/** Keep an active operator signed in: renew the short-lived token while
 * they are using the portal; let it lapse after 30 minutes of no activity. */
export function useSessionKeepAlive(token: string, setToken: (value: string) => void) {
  const lastActive = useRef(0);
  useEffect(() => {
    const mark = () => { lastActive.current = Date.now(); };
    mark();
    const events = ["pointerdown", "keydown", "scroll", "touchstart"] as const;
    events.forEach((name) => window.addEventListener(name, mark, { passive: true }));
    return () => events.forEach((name) => window.removeEventListener(name, mark));
  }, []);
  useEffect(() => {
    if (!token) return;
    let stopped = false;
    async function tick() {
      const left = tokenExpiry(token) - Date.now() / 1000;
      if (stopped || left > RENEW_BEFORE_SECONDS || Date.now() - lastActive.current > IDLE_LIMIT_MS) return;
      try {
        const response = await fetch("/api/v1/auth/refresh", { method: "POST", headers: { Accept: "application/json", Authorization: `Bearer ${token}` } });
        if (response.ok) { const body = (await response.json()) as { access_token?: string }; if (!stopped && body.access_token) setToken(body.access_token); }
        else if (response.status === 401) setToken("");
      } catch {
        // Network blip: try again on the next tick while the token is still valid.
      }
    }
    const timer = window.setInterval(() => void tick(), 60 * 1000);
    void tick();
    return () => { stopped = true; window.clearInterval(timer); };
  }, [token, setToken]);
}
