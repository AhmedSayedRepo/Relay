import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export function useApi<T>(path: string, token: string) {
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
