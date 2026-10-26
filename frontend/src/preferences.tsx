import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type Theme = "system" | "light" | "dark";
const THEME_KEY = "relay.theme";
function savedTheme(): Theme { const value = localStorage.getItem(THEME_KEY); return value === "light" || value === "dark" || value === "system" ? value : "system"; }
function systemTheme(): "light" | "dark" { return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"; }

const ThemeContext = createContext<{ theme: Theme; setTheme: (theme: Theme) => void } | null>(null);
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(savedTheme);
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const apply = () => { document.documentElement.dataset.theme = theme === "system" ? systemTheme() : theme; };
    localStorage.setItem(THEME_KEY, theme); apply();
    if (theme === "system") media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  const value = useMemo(() => ({ theme, setTheme }), [theme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
export function useTheme() { const value = useContext(ThemeContext); if (!value) throw new Error("useTheme must be used within ThemeProvider"); return value; }
