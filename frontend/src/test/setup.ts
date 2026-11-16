import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";

// Saved filters must not leak from one test into the next.
beforeEach(() => window.sessionStorage.clear());

if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => undefined, removeEventListener: () => undefined,
      addListener: () => undefined, removeListener: () => undefined,
      dispatchEvent: () => true,
    }),
  });
}
