import { render as renderBase, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { api } from "../auth/adminMFA";
import {
  CustomerBillingSection,
  CustomerCooldownSection,
  CustomerDatabaseSection,
  CustomerNichePolicySection,
  formatLeadRate,
  formatUsdCents,
  parseRatePerThousand,
} from "./AdminCustomerControls";
import type {
  CustomerBillingView,
  NichePolicyView,
} from "./AdminCustomerControls";

vi.mock("../auth/adminMFA", () => ({ api: vi.fn() }));

/** The ledger links each entry to its batch, so these render in a router. */
function render(element: ReactElement) {
  return renderBase(<MemoryRouter>{element}</MemoryRouter>);
}

const BILLING: CustomerBillingView = {
  customerId: 7,
  billingEnabled: false,
  leadRateCentsPerThousand: null,
  balanceCents: 2500,
  activeHoldsCents: 500,
  availableBalanceCents: 2000,
  purchases: [],
  ledger: [
    {
      id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
      kind: "batch_charge",
      amountCents: -25_000,
      reason: "Batch #1033 delivered",
      actor: "system:allocation",
      actorLabel: "System",
      batchRequestId: "10331033-1033-4033-8033-103310331033",
      batchNumber: 1033,
      createdAt: "2026-07-10T12:00:00Z",
    },
    {
      id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
      kind: "admin_adjustment",
      amountCents: 2500,
      reason: "Opening credit",
      actor: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      actorLabel: "Operator",
      batchRequestId: null,
      batchNumber: null,
      createdAt: "2026-07-01T12:00:00Z",
    },
  ],
};

const POLICY: NichePolicyView = {
  rows: [{ state: null, mode: "exclude", niches: ["roofing"] }],
};

describe("admin billing and allocation money formatting", () => {
  it("formats wallet cents and Lead Rates for operators", () => {
    expect(formatUsdCents(2500)).toBe("$25.00");
    expect(formatUsdCents(-1050)).toBe("−$10.50");
    expect(formatUsdCents(123_200)).toBe("$1,232.00");
    expect(formatLeadRate(null)).toBe("Not set");
    expect(formatLeadRate(500)).toBe("$5.00 per 1,000");
    expect(formatLeadRate(150)).toBe("$1.50 per 1,000");
    expect(parseRatePerThousand("5")).toBe(500);
    expect(parseRatePerThousand("5.25")).toBe(525);
    expect(parseRatePerThousand("5.255")).toBeNull();
    expect(parseRatePerThousand("")).toBeNull();
  });
});

describe("CustomerBillingSection", () => {
  it("requires a Lead Rate and reason when enabling billing", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(api).mockResolvedValue({});

    render(
      <CustomerBillingSection
        billing={BILLING}
        customerId={7}
        onChanged={onChanged}
      />,
    );

    expect(screen.getByText("Billing off")).toBeVisible();
    expect(screen.getByText("Opening credit")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Configure billing" }));
    const dialog = screen.getByRole("dialog", { name: "Configure billing" });
    const save = within(dialog).getByRole("button", { name: "Save billing" });
    expect(save).toBeDisabled();

    await user.click(within(dialog).getByLabelText("Billing enabled"));
    await user.type(within(dialog).getByLabelText(/Rate per 1,000 leads/), "10");
    expect(save).toBeDisabled();
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Contract start",
    );
    await user.click(save);

    expect(api).toHaveBeenCalledWith(
      "/api/admin/customers/7/billing",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          billing_enabled: true,
          lead_rate_cents_per_thousand: 1000,
          reason: "Contract start",
        }),
      }),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("previews the rate per 1,000 as it changes", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);

    render(
      <CustomerBillingSection
        billing={BILLING}
        customerId={7}
        onChanged={onChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Configure billing" }));
    const dialog = screen.getByRole("dialog", { name: "Configure billing" });
    const range = "$1.00 to $20.00 per 1,000 leads, to the cent.";
    expect(within(dialog).getByText(range)).toBeVisible();

    await user.click(within(dialog).getByLabelText("Billing enabled"));
    const rate = within(dialog).getByLabelText(/Rate per 1,000 leads/);
    await user.type(rate, "5");
    expect(
      within(dialog).getByText("$5.00 per 1,000. Applies to future batches."),
    ).toBeVisible();

    await user.clear(rate);
    await user.type(rate, "25");
    expect(within(dialog).getByText(range)).toBeVisible();

    await user.clear(rate);
    expect(within(dialog).getByText(range)).toBeVisible();
  });

  it("names who posted each ledger entry and links its batch", () => {
    render(<CustomerBillingSection billing={BILLING} customerId={7} onChanged={vi.fn()} />);

    const ledger = screen.getByRole("table", { name: "Credit ledger" });
    const [, charge, adjustment] = within(ledger).getAllByRole("row");
    expect(within(charge!).getByText("Batch charge")).toBeVisible();
    expect(within(charge!).getByText("−$250.00")).toBeVisible();
    expect(within(charge!).getByText("System")).toBeVisible();
    expect(within(charge!).getByRole("link", { name: "#1033" })).toHaveAttribute("href", "/admin/batches/1033");
    expect(within(adjustment!).getByText("Operator")).toBeVisible();
    expect(screen.getByRole("table", { name: "Purchases" })).toHaveTextContent("No card purchases yet.");
  });

  it("bills any customer, whichever database they draw from", () => {
    render(<CustomerBillingSection billing={{ ...BILLING, billingEnabled: true, leadRateCentsPerThousand: 150 }} customerId={7} onChanged={vi.fn()} />);
    expect(screen.getByRole("button", { name:"Configure billing" })).toBeEnabled();
    expect(screen.getByText("Billing on")).toBeVisible();
    expect(screen.getByText("$1.50 per 1,000")).toBeVisible();
    expect(screen.queryByText(/DB1|DB2|database/i)).toBeNull();
  });

  it("posts a Credit Wallet adjustment with a required reason", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(api).mockResolvedValue({});

    render(
      <CustomerBillingSection
        billing={BILLING}
        customerId={7}
        onChanged={onChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Post adjustment" }));
    const dialog = screen.getByRole("dialog", {
      name: "Post Credit Wallet adjustment",
    });
    const post = within(dialog).getByRole("button", {
      name: "Post adjustment",
    });
    expect(post).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Amount/), "10.5");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Reconcile",
    );
    await user.click(post);

    expect(api).toHaveBeenCalledWith(
      "/api/admin/customers/7/billing/adjustments",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          amount_cents: 1050,
          reason: "Reconcile",
        }),
      }),
    );
  });
});

describe("CustomerDatabaseSection", () => {
  it("changes the customer's database with a reason", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(api).mockReset().mockResolvedValue({ customerId: 7, leadDatabase: "db2", changed: true });

    render(<CustomerDatabaseSection customerId={7} database="db1" onChanged={onChanged} />);

    expect(screen.getByText("DB1 · Operator leads")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Change database" }));
    const dialog = screen.getByRole("dialog", { name: "Change database" });
    expect(dialog).toHaveTextContent("Billing and the rate stay as they are.");
    await user.type(within(dialog).getByLabelText("Reason (required)"), "Uploads only");
    await user.click(within(dialog).getByRole("button", { name: "Use DB2" }));

    expect(api).toHaveBeenCalledWith("/api/admin/customers/7/lead-database", {
      method: "PUT",
      body: JSON.stringify({ lead_database: "db2", reason: "Uploads only" }),
    });
    expect(onChanged).toHaveBeenCalledOnce();
  });
});

describe("CustomerCooldownSection", () => {
  it("saves a Cooldown Window with an audit reason", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(api).mockResolvedValue({});

    render(
      <CustomerCooldownSection
        cooldown={{ days: 7 }}
        customerId={7}
        onChanged={onChanged}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Change Cooldown Window" }),
    );
    const dialog = screen.getByRole("dialog", {
      name: "Change Cooldown Window",
    });
    await user.clear(within(dialog).getByLabelText("Days (required)"));
    await user.type(within(dialog).getByLabelText("Days (required)"), "3");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Shorter window",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Save Cooldown Window" }),
    );

    expect(api).toHaveBeenCalledWith(
      "/api/admin/customers/7/cooldown-window",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ days: 3, reason: "Shorter window" }),
      }),
    );
  });
});

describe("CustomerNichePolicySection", () => {
  it("does not silently drop a row whose required Niches field is empty", async () => {
    const user = userEvent.setup();
    render(
      <CustomerNichePolicySection
        policy={{ rows: [{ state: "TX", mode: "exclude", niches: ["solar"] }] }}
        customerId={7}
        onChanged={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit Niche Policy" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Niche Policy" });
    const preview = within(dialog).getByRole("button", {
      name: "Preview projected availability",
    });
    expect(preview).toBeEnabled();

    // A blank row beside a filled one is a half-finished edit, not a clear.
    await user.click(within(dialog).getByRole("button", { name: "Add row" }));
    expect(preview).toBeDisabled();
    expect(
      within(dialog).getByText(/Every row needs at least one Niche/),
    ).toBeVisible();

    await user.type(
      within(dialog).getAllByLabelText("Niches (required)")[1]!,
      "roofing",
    );
    expect(preview).toBeEnabled();
  });

  it("clears a Niche Policy when every row is emptied", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(api)
      .mockResolvedValueOnce({ available: 1200 })
      .mockResolvedValueOnce({ rows: [] });

    render(
      <CustomerNichePolicySection
        policy={{ rows: [{ state: null, mode: "exclude", niches: ["solar"] }] }}
        customerId={7}
        onChanged={onChanged}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Edit Niche Policy" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Niche Policy" });
    await user.clear(within(dialog).getByLabelText("Niches (required)"));
    expect(
      within(dialog).getByText(/saving removes this Customer's Niche Policy/),
    ).toBeVisible();

    await user.click(
      within(dialog).getByRole("button", {
        name: "Preview projected availability",
      }),
    );
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Customer took every Niche",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Save Niche Policy" }),
    );

    expect(api).toHaveBeenCalledWith(
      "/api/admin/customers/7/niche-policy",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          rows: [],
          reason: "Customer took every Niche",
        }),
      }),
    );
  });

  it("requires a projected-availability preview before save", async () => {
    const user = userEvent.setup();
    const onChanged = vi.fn().mockResolvedValue(undefined);
    vi.mocked(api)
      .mockResolvedValueOnce({ available: 42 })
      .mockResolvedValueOnce({ rows: [] });

    render(
      <CustomerNichePolicySection
        policy={POLICY}
        customerId={7}
        onChanged={onChanged}
      />,
    );

    const current = screen.getByRole("table", { name: "Current Niche Policy" });
    expect(within(current).getByText("All states")).toBeVisible();
    expect(within(current).getByText("Exclude")).toBeVisible();
    expect(within(current).getByText("roofing")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit Niche Policy" }));
    const dialog = screen.getByRole("dialog", { name: "Edit Niche Policy" });
    const save = within(dialog).getByRole("button", {
      name: "Save Niche Policy",
    });
    expect(save).toBeDisabled();

    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Tighten TX",
    );
    expect(save).toBeDisabled();

    await user.click(
      within(dialog).getByRole("button", {
        name: "Preview projected availability",
      }),
    );
    expect(
      await within(dialog).findByText("Projected available Leads: 42"),
    ).toBeVisible();
    expect(api).toHaveBeenCalledWith(
      "/api/admin/customers/7/niche-policy/projected-availability",
      expect.objectContaining({ method: "POST" }),
    );

    await user.click(save);
    expect(api).toHaveBeenCalledWith(
      "/api/admin/customers/7/niche-policy",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({
          rows: [{ state: null, mode: "exclude", niches: ["roofing"] }],
          reason: "Tighten TX",
        }),
      }),
    );
  });
});
