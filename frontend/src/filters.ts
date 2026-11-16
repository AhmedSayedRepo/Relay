import { useEffect, useState } from "react";

/** Filters (dates, times, search, sort, page) survive moving between pages
 * and reloading the tab, and start fresh after signing in or out. */
const PREFIX = "relay.filters.";

function read<T>(key: string, fallback: T | (() => T)): T {
  try {
    const raw = window.sessionStorage.getItem(PREFIX + key);
    if (raw !== null) return JSON.parse(raw) as T;
  } catch { /* storage off or bad value: use the default */ }
  return typeof fallback === "function" ? (fallback as () => T)() : fallback;
}

export function usePersisted<T>(key: string, fallback: T | (() => T)) {
  const [value, setValue] = useState<T>(() => read(key, fallback));
  useEffect(() => {
    try { window.sessionStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* storage off: keep in memory only */ }
  }, [key, value]);
  return [value, setValue] as const;
}

export function clearFilters(): void {
  try {
    for (const key of Object.keys(window.sessionStorage)) if (key.startsWith(PREFIX)) window.sessionStorage.removeItem(key);
  } catch { /* storage off: nothing kept */ }
}
