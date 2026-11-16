import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../i18n";
import { WhatsNew } from "./WhatsNew";

const notes = [{ version: "9.9.9", date: "2026-11-16", en: ["Shiny thing"], ar: ["حاجة جديدة"] }];

describe("WhatsNew", () => {
  it("shows the newest notes once, then remembers", async () => {
    window.localStorage.clear();
    const view = render(<I18nProvider><WhatsNew notes={notes} enabled /></I18nProvider>);
    expect(await screen.findByText("Shiny thing")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Got it" }));
    expect(screen.queryByText("Shiny thing")).toBeNull();
    view.unmount();
    render(<I18nProvider><WhatsNew notes={notes} enabled /></I18nProvider>);
    expect(screen.queryByText("Shiny thing")).toBeNull();
  });
});
