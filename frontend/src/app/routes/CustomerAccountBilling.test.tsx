import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CustomerBilling } from "../customer/billing";
import { CustomerAccountBilling } from "./CustomerAccountBilling";

const NOW = new Date(2026, 10, 9, 10, 0);
const at = (day: number, hours: number, minutes: number) =>
  new Date(2026, 10, day, hours, minutes).toISOString();
const batch = (id: string, number: number) => ({ id, number, href: `/app/batches/${id}` });
const HELD = batch("42424242-4242-4242-8242-424242424242", 1042);

const BILLING: CustomerBilling = {
  available_balance_cents: 41_250,
  held_cents: 12_500,
  held_for: [HELD],
  lead_rate_cents_per_thousand: 500,
  spent_since: at(10, 9, 0),
  spent_cents: 100_000,
  spent_batches: 3,
  history: [
    { id: "hold", kind: "hold", occurred_at: at(9, 9, 41), amount_cents: -12_500, balance_after_cents: 41_250, batch: HELD, note: null },
    { id: "buy", kind: "purchase", occurred_at: at(9, 9, 30), amount_cents: 50_000, balance_after_cents: 53_750, batch: null, note: null },
    { id: "charge", kind: "charge", occurred_at: at(7, 8, 12), amount_cents: -25_000, balance_after_cents: 3_750, batch: batch("38383838-3838-4838-8838-383838383838", 1038), note: null },
    { id: "fix", kind: "adjustment", occurred_at: at(6, 12, 0), amount_cents: 375, balance_after_cents: 28_750, batch: null, note: "Refund for a bad lead" },
  ],
  history_complete: false,
};

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function show(answer: () => Response) {
  vi.stubGlobal("fetch", vi.fn(async () => answer()));
  render(
    <MemoryRouter>
      <CustomerAccountBilling now={NOW} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Account Billing tab", () => {
  it("shows the four numbers in full with cents (V5)", async () => {
    show(() => respond(BILLING));

    const numbers = await screen.findByLabelText("Billing");
    expect(numbers).toHaveTextContent("Balance$412.50Available for new requests");
    expect(numbers).toHaveTextContent("Held$125.00For #1042 until it is delivered");
    expect(numbers).toHaveTextContent("Rate$5.00Per 1,000 leads");
    expect(numbers).toHaveTextContent("Spent · 30 days$1,000.00");
    expect(numbers).toHaveTextContent("3 batches");
  });

  it("lists history in Customer words, linking every batch", async () => {
    show(() => respond(BILLING));

    const history = await screen.findByRole("table", { name: "Billing history" });
    const rows = within(history).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      "Nov 9 09:41Held for batch #1042−$125.00$412.50",
      "Nov 9 09:30Credits bought+$500.00$537.50",
      "Nov 7 08:12Charged for batch #1038−$250.00$37.50",
      "Nov 6 12:00Adjustment · Refund for a bad lead+$3.75$287.50",
    ]);
    expect(within(history).getByRole("link", { name: "batch #1042" })).toHaveAttribute(
      "href",
      "/app/batches/42424242-4242-4242-8242-424242424242",
    );
    expect(screen.getByText("Showing the latest 4 entries.")).toBeVisible();
  });

  it("names several held batches", async () => {
    show(() =>
      respond({
        ...BILLING,
        held_for: [HELD, batch("43434343-4343-4343-8343-434343434343", 1043)],
      }),
    );

    expect(await screen.findByText("For #1042 and #1043 until they are delivered")).toBeVisible();
  });

  it("says so when the account is not billed", async () => {
    show(() => respond({ detail: "Billing is not set up for this account." }, 404));

    expect(await screen.findByText(/not billed per lead/)).toBeVisible();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("offers another try when billing cannot be read", async () => {
    const user = userEvent.setup();
    let calls = 0;
    show(() => {
      calls += 1;
      return calls === 1 ? respond({}, 503) : respond(BILLING);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent("Billing could not be loaded.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("table", { name: "Billing history" })).toBeVisible();
  });
});
