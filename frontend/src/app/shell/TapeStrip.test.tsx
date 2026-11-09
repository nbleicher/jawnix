import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TAPE_ENTRIES, TapeStrip } from "./TapeStrip";

describe("TAPE_ENTRIES", () => {
  it("quotes no figures: the header shows the balance (V5, V6)", () => {
    expect(TAPE_ENTRIES.map((entry) => entry.label)).toEqual([
      "JAWNIX",
      "LEAD LEDGER",
      "SETTLEMENT DAILY",
      "EVERY ROW RECONCILES",
    ]);
    expect(TAPE_ENTRIES.some((entry) => entry.value)).toBe(false);
  });
});

describe("TapeStrip", () => {
  it("is wholly decorative: aria-hidden, with the block duplicated for the loop", () => {
    const { container } = render(<TapeStrip />);

    const tape = container.querySelector(".jx-tape");
    expect(tape).toHaveAttribute("aria-hidden", "true");
    // Two identical blocks make the marquee seamless; the strip's aria-hidden
    // keeps both out of the accessibility tree, so nothing is heard twice.
    expect(container.querySelectorAll(".jx-tape__block")).toHaveLength(2);
  });
});
