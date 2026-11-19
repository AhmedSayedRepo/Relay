import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";

/** How often "live" lists refresh themselves while the tab is visible. */
export const LIVE_REFRESH_MS = 30_000;

/**
 * Loads `path`. With `live`, it also refreshes quietly every 30 seconds and
 * when the tab comes back into view: the page keeps showing what it has (no
 * spinner, no error flash) and only swaps in newer data. Pages with forms
 * stay non-live so a refresh never resets what someone is typing.
 */
export function useApi<T>(path: string, token: string, live = false) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(() => {
    let active = true;
    setLoading(true);
    setError("");
    api<T>(path, token)
      .then((value) => active && setData(value))
      .catch((reason: unknown) => {
        if (!active) return;
        if (reason instanceof ApiError && (reason.status === 401 || reason.status === 403)) setError("AUTH");
        else if ((reason instanceof ApiError && reason.status >= 500) || reason instanceof TypeError) setError("UNAVAILABLE");
        else setError("GENERIC");
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [path, token]);
  // Fetch state is intentionally reset whenever the authenticated query changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => reload(), [reload]);
  useEffect(() => {
    if (!live) return;
    let active = true;
    const quiet = () => {
      if (document.visibilityState !== "visible") return;
      api<T>(path, token).then((value) => active && setData(value)).catch(() => undefined);
    };
    const timer = window.setInterval(quiet, LIVE_REFRESH_MS);
    document.addEventListener("visibilitychange", quiet);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", quiet);
    };
  }, [live, path, token]);
  return { data, error, loading, reload };
}

export function defaultDates() {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 29);
  return {
    dateFrom: start.toISOString().slice(0, 10),
    dateTo: end.toISOString().slice(0, 10),
  };
}

/** Calendar date in Cairo (receipts' local time), `daysAgo` days back, as YYYY-MM-DD. */
export function cairoDate(daysAgo = 0) {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const day = new Date(parts + "T12:00:00Z");
  day.setUTCDate(day.getUTCDate() - daysAgo);
  return day.toISOString().slice(0, 10);
}
