import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ar } from "./ar";
import { en, type Messages } from "./en";

export type Locale = "en" | "ar";
const LOCALE_KEY = "relay.locale";
export const localeCatalogs: Record<Locale, Messages> = { en, ar };

function savedLocale(): Locale {
  const value = localStorage.getItem(LOCALE_KEY);
  // Arabic first; tests keep English so their labels stay readable.
  return value === "ar" || value === "en" ? value : import.meta.env.MODE === "test" ? "en" : "ar";
}

type I18nValue = { locale: Locale; setLocale: (locale: Locale) => void; messages: Messages; dir: "ltr" | "rtl" };
const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(savedLocale);
  useEffect(() => {
    localStorage.setItem(LOCALE_KEY, locale);
    document.documentElement.lang = locale;
    document.documentElement.dir = localeCatalogs[locale].meta.direction;
  }, [locale]);
  const value = useMemo<I18nValue>(() => ({ locale, setLocale, messages: localeCatalogs[locale], dir: locale === "ar" ? "rtl" : "ltr" }), [locale]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const value = useContext(I18nContext);
  if (!value) throw new Error("useI18n must be used within I18nProvider");
  return value;
}

export function formatMessage(template: string, values: Record<string, string | number>) {
  return template.replace(/\{(\w+)\}/g, (_, key: string) => String(values[key] ?? `{${key}}`));
}
