import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { api } from "../auth/adminMFA";
import {
  CustomerBillingSection,
  CustomerCooldownSection,
  CustomerNichePolicySection,
  formatLeadRate,
  formatUsdCents,
} from "./AdminCustomerControls";
import type {
  CustomerBillingView,
  NichePolicyView,
} from "./AdminCustomerControls";

vi.mock("../auth/adminMFA", () => ({ api: vi.fn() }));

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
      id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
      kind: "admin_adjustment",
      amountCents: 2500,
      reason: "Opening credit",
      actor: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      batchRequestId: null,
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
    expect(formatLeadRate(null)).toBe("Not set");
    expect(formatLeadRate(1000)).toBe(
      "1000¢ per 1,000 leads ($0.010/lead)",
    );
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
    await user.type(within(dialog).getByLabelText(/Lead Rate/), "1000");
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

  it("previews the per-lead price as the Lead Rate changes", async () => {
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
    expect(
      within(dialog).getByText(
        "Integer from 100 to 2000. Example: 1000 = $0.010 per lead.",
      ),
    ).toBeVisible();

    await user.click(within(dialog).getByLabelText("Billing enabled"));
    const rate = within(dialog).getByLabelText(/Lead Rate/);
    await user.type(rate, "1000");
    expect(
      within(dialog).getByText(
        "Integer from 100 to 2000. 1000 = $0.010 per lead.",
      ),
    ).toBeVisible();

    await user.clear(rate);
    await user.type(rate, "5000");
    expect(
      within(dialog).getByText(
        "Integer from 100 to 2000. 5000 = $0.050 per lead.",
      ),
    ).toBeVisible();

    await user.clear(rate);
    expect(
      within(dialog).getByText(
        "Integer from 100 to 2000. Example: 1000 = $0.010 per lead.",
      ),
    ).toBeVisible();
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

    expect(screen.getByText(/All states: exclude roofing/)).toBeVisible();
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
