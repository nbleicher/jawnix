import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { DisclosureSection } from "./layout";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("DisclosureSection", () => {
  it("uses a heading and opens the section targeted by a deep link", () => {
    window.history.replaceState(null, "", "/#billing");
    render(
      <DisclosureSection id="billing" title="Billing">
        Wallet controls
      </DisclosureSection>,
    );

    expect(
      screen.getByRole("heading", { level: 2, name: "Billing" }),
    ).toBeVisible();
    expect(document.querySelector("details#billing")).toHaveAttribute("open");
    // A heading is flow content, so its wrapper cannot be phrasing-only.
    expect(
      document.querySelector(".jx-disclosure-section__copy")?.tagName,
    ).toBe("DIV");
  });

  it("keeps an operator-opened section open when the page re-renders", async () => {
    const user = userEvent.setup();
    function Host() {
      const [tick, setTick] = useState(0);
      return (
        <>
          <button onClick={() => setTick(tick + 1)}>Re-render</button>
          <DisclosureSection id="exclusions" title={`Exclusions ${tick}`}>
            Uploaded lists
          </DisclosureSection>
        </>
      );
    }
    window.history.replaceState(null, "", "/#somewhere-else");
    render(<Host />);

    const details = document.querySelector("details#exclusions");
    expect(details).not.toHaveAttribute("open");

    await user.click(screen.getByText("Exclusions 0"));
    expect(details).toHaveAttribute("open");

    await user.click(screen.getByRole("button", { name: "Re-render" }));
    expect(details).toHaveAttribute("open");
  });
});
