import { describe, expect, it } from "vitest";

import {
  batchCostCents,
  formatBalanceRefusal,
  formatCents,
  formatLeadRate,
} from "./money";

describe("formatCents", () => {
  it("renders whole dollars and fractional cents", () => {
    expect(formatCents(2500)).toBe("$25.00");
    expect(formatCents(1)).toBe("$0.01");
    expect(formatCents(0)).toBe("$0.00");
  });
});

describe("batchCostCents", () => {
  it("rounds half-up to the nearest cent like the backend", () => {
    // 750 leads × $0.005/lead = $3.75
    expect(batchCostCents(750, 500)).toBe(375);
    // 1 lead × $0.0015/lead = $0.0015 → $0.00 with half-up at .5 of a mill?
    // 1 * 150 + 500 = 650 // 1000 = 0
    expect(batchCostCents(1, 150)).toBe(0);
    // 667 * 150 + 500 = 100_550 // 1000 = 100
    expect(batchCostCents(667, 150)).toBe(100);
  });
});

describe("formatLeadRate", () => {
  it("names the per-lead price from cents-per-thousand", () => {
    expect(formatLeadRate(500)).toBe("$0.005 per lead");
    expect(formatLeadRate(100)).toBe("$0.001 per lead");
  });
});

describe("formatBalanceRefusal", () => {
  it("rewrites the backend cents template into dollars", () => {
    expect(
      formatBalanceRefusal(
        "The Credit Wallet does not have enough available balance for this Batch Hold. Required 1000 cents; available 999 cents.",
      ),
    ).toBe(
      "The Credit Wallet does not have enough available balance for this Batch Hold. Required $10.00; available $9.99.",
    );
  });

  it("passes through unrecognized messages", () => {
    expect(formatBalanceRefusal("Something else.")).toBe("Something else.");
  });
});
