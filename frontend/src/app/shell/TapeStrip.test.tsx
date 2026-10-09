import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CreditWallet } from "../billing/wallet";
import { buildTapeEntries, TapeStrip } from "./TapeStrip";

const BILLED_WALLET: CreditWallet = {
  customerId: 7,
  billingEnabled: true,
  leadRateCentsPerThousand: 500,
  balanceCents: 10_000,
  activeHoldsCents: 375,
  availableBalanceCents: 9_625,
  purchases: [],
  ledger: [],
};

describe("buildTapeEntries", () => {
  it("serves the generic set when there is no Billed wallet", () => {
    expect(buildTapeEntries(null).map((entry) => entry.label)).toEqual([
      "JAWNIX",
      "LEAD LEDGER",
      "SETTLEMENT DAILY",
      "EVERY ROW RECONCILES",
    ]);
  });

  it("quotes the real wallet figures for a Billed Customer", () => {
    const entries = buildTapeEntries(BILLED_WALLET);
    expect(entries.map((entry) => entry.label)).toEqual([
      "JAWNIX",
      "AVAIL",
      "RATE",
      "LEAD LEDGER",
      "SETTLEMENT DAILY",
      "EVERY ROW RECONCILES",
    ]);
    expect(entries[1]?.value).toBe("$96.25");
    expect(entries[2]?.value).toBe("$0.005");
  });

  it("omits the rate when the wallet has none rather than inventing one", () => {
    const entries = buildTapeEntries({
      ...BILLED_WALLET,
      leadRateCentsPerThousand: null,
    });
    expect(entries.map((entry) => entry.label)).not.toContain("RATE");
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
