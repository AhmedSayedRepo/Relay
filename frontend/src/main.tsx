import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { I18nProvider } from "./i18n";
import { ThemeProvider } from "./preferences";
import "./styles.css";

// Inside the Relay Operator phone app: app-like chrome (no browser assumptions).
if (navigator.userAgent.includes("RelayOperator")) document.documentElement.classList.add("in-app");

createRoot(document.getElementById("root")!).render(<StrictMode><ThemeProvider><I18nProvider><App /></I18nProvider></ThemeProvider></StrictMode>);
