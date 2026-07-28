import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
});

/*
 * jsdom (26.x) implements <dialog> markup but none of its behaviour: no
 * `showModal`, `close`, `cancel`, top layer, or focus containment.
 *
 * This shim supplies just enough for the parts of Dialog that *we* own —
 * open/close state, the `close` and `cancel` events, and the `returnValue`
 * contract. It deliberately does NOT emulate focus trapping or inertness,
 * because faking those would let a broken trap pass a green test. Real trapping
 * is asserted against a real browser in e2e/accessibility.spec.ts.
 */
if (typeof HTMLDialogElement !== "undefined" && !HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(this: HTMLDialogElement) {
    this.open = true;
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.show = function show(this: HTMLDialogElement) {
    this.open = true;
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function close(this: HTMLDialogElement, returnValue?: string) {
    if (!this.open) return;
    this.open = false;
    this.removeAttribute("open");
    if (returnValue !== undefined) this.returnValue = returnValue;
    this.dispatchEvent(new Event("close"));
  };
}

// jsdom ships no matchMedia. Nothing in src/ calls it today — responsive
// behaviour is pure CSS — but React and Testing Library probe it, and an absent
// global surfaces as an unrelated TypeError.
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}
