import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  OperatorNumbers,
  formatAge,
  formatAsOf,
  formatCreditsChange,
} from "./OperatorNumbers";
import type { OperatorNumbersData } from "./OperatorNumbers";

const NOW = new Date(2026, 10, 9, 9, 41);

function local(hours: number, minutes: number, day = 9, month = 10, year = 2026) {
  return new Date(year, month, day, hours, minutes).toISOString();
}

function operatorNumbers(
  overrides: Partial<OperatorNumbersData> = {},
): OperatorNumbersData {
  return {
    generatedAt: NOW.toISOString(),
    windowDays: 30,
    inventory: {
      db1: { usable: 9183402, asOf: local(6, 0) },
      db2: { usable: 1071288, asOf: local(6, 0) },
    },
    openBatches: { total: 4, awaitingApproval: 1, shortOfInventory: 1 },
    delivered: { leads: 2069666, batches: 33, customers: 13 },
    customers: { active: 14, withSetupProblems: 1 },
    creditBalance: { availableCents: 841250, billedCustomers: 11 },
    creditsUsed: { cents: 1034833, priorCents: 923958 },
    decisions: {
      waiting: 7,
      oldestAt: new Date(NOW.getTime() - 2 * 86_400_000).toISOString(),
      kinds: [],
    },
    ...overrides,
  };
}

function tile(label: string) {
  const term = screen.getByText(label, { selector: "dt" });
  return within(term.parentElement as HTMLElement);
}

describe("OperatorNumbers", () => {
  it("renders the eight tiles with full numbers, cents, and as-of times", () => {
    render(<OperatorNumbers numbers={operatorNumbers()} now={NOW} />);

    const row = screen.getByLabelText("Key numbers");
    expect(within(row).getAllByRole("term").map((item) => item.textContent)).toEqual([
      "DB1 usable",
      "DB2 usable",
      "Open batches",
      "Delivered · 30 days",
      "Active customers",
      "Credit balance",
      "Credits used · 30 days",
      "Decisions waiting",
    ]);
    expect(tile("DB1 usable").getByText((9183402).toLocaleString())).toBeInTheDocument();
    expect(tile("DB1 usable").getByText("Operator leads · as of 06:00")).toBeInTheDocument();
    expect(tile("DB2 usable").getByText("CSV uploads · as of 06:00")).toBeInTheDocument();
    expect(tile("Open batches").getByText("1 awaiting approval · 1 short")).toBeInTheDocument();
    expect(tile("Delivered · 30 days").getByText("33 batches to 13 customers")).toBeInTheDocument();
    expect(tile("Active customers").getByText("1 with setup problems")).toBeInTheDocument();
    expect(tile("Credit balance").getByText("$8,412.50")).toBeInTheDocument();
    expect(tile("Credit balance").getByText("11 billed customers")).toBeInTheDocument();
    expect(tile("Credits used · 30 days").getByText("$10,348.33")).toBeInTheDocument();
    expect(
      tile("Credits used · 30 days").getByText("Up 12% on the prior 30 days"),
    ).toBeInTheDocument();
    expect(tile("Decisions waiting").getByText("Oldest 2d")).toBeInTheDocument();
  });

  it("reserves red for waiting decisions only", () => {
    const { container, rerender } = render(
      <OperatorNumbers numbers={operatorNumbers()} now={NOW} />,
    );
    const alerts = container.querySelectorAll(".jx-kpi--alert");
    expect(alerts).toHaveLength(1);
    expect(alerts[0]).toHaveTextContent("Decisions waiting");

    rerender(
      <OperatorNumbers
        numbers={operatorNumbers({
          decisions: { waiting: 0, oldestAt: null, kinds: [] },
        })}
        now={NOW}
      />,
    );
    expect(container.querySelectorAll(".jx-kpi--alert")).toHaveLength(0);
    expect(tile("Decisions waiting").getByText("Nothing waiting")).toBeInTheDocument();
  });

  it("says a snapshot was never counted instead of showing zero", () => {
    render(
      <OperatorNumbers
        numbers={operatorNumbers({ inventory: { db1: null, db2: null } })}
        now={NOW}
      />,
    );
    expect(tile("DB1 usable").getByText("Not counted")).toBeInTheDocument();
    expect(tile("DB2 usable").getByText("CSV uploads · not counted yet")).toBeInTheDocument();
  });

  it("singularizes one batch to one customer", () => {
    render(
      <OperatorNumbers
        numbers={operatorNumbers({
          delivered: { leads: 50000, batches: 1, customers: 1 },
          creditBalance: { availableCents: 0, billedCustomers: 1 },
        })}
        now={NOW}
      />,
    );
    expect(tile("Delivered · 30 days").getByText("1 batch to 1 customer")).toBeInTheDocument();
    expect(tile("Credit balance").getByText("1 billed customer")).toBeInTheDocument();
  });
});

describe("number formatting", () => {
  it("shows the date only when the as-of is not today, and the year only when it differs", () => {
    expect(formatAsOf(local(6, 0), NOW)).toBe("06:00");
    expect(formatAsOf(local(23, 5, 8), NOW)).toBe("Nov 8 23:05");
    expect(formatAsOf(local(6, 0, 30, 11, 2025), NOW)).toBe("Dec 30, 2025 06:00");
  });

  it("formats ages as minutes, hours, then days", () => {
    const ago = (ms: number) => new Date(NOW.getTime() - ms).toISOString();
    expect(formatAge(ago(20 * 60_000), NOW)).toBe("20m");
    expect(formatAge(ago(2 * 3_600_000), NOW)).toBe("2h");
    expect(formatAge(ago(3 * 86_400_000), NOW)).toBe("3d");
  });

  it("compares credits used against the prior window", () => {
    expect(formatCreditsChange(1034833, 923958, 30)).toBe("Up 12% on the prior 30 days");
    expect(formatCreditsChange(900, 1000, 30)).toBe("Down 10% on the prior 30 days");
    expect(formatCreditsChange(1000, 1000, 30)).toBe("Level with the prior 30 days");
    expect(formatCreditsChange(500, 0, 30)).toBe("None used in the prior 30 days");
    expect(formatCreditsChange(0, 0, 30)).toBe("None used in the prior 30 days either");
  });
});
