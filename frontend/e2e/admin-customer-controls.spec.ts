import { expect, test } from "@playwright/test";

import { mockAdminCustomers } from "./admin-customers-fixtures";

test.describe("Customer billing and allocation controls", () => {
  test("configures billing with a Lead Rate and posts a Credit Wallet adjustment", async ({
    page,
  }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7");

    const billing = page.getByRole("region", { name: "Billing" });
    await expect(billing.getByText("Billing off", { exact: true })).toBeVisible();
    await expect(billing.getByText("Opening credit")).toBeVisible();
    await expect(
      billing.getByRole("cell", { name: "$25.00" }),
    ).toBeVisible();

    await billing.getByRole("button", { name: "Configure billing" }).click();
    const configure = page.getByRole("dialog", { name: "Configure billing" });
    await configure.getByLabel("Billing enabled").check();
    await configure.getByLabel(/Lead Rate/).fill("1000");
    await configure.getByLabel("Reason (required)").fill("Contract start");
    await configure.getByRole("button", { name: "Save billing" }).click();
    await expect(configure).toBeHidden();

    expect(state.billingPutRequests).toEqual([
      {
        billing_enabled: true,
        lead_rate_cents_per_thousand: 1000,
        reason: "Contract start",
      },
    ]);
    await expect(billing.getByText("Billing on", { exact: true })).toBeVisible();    await expect(
      billing.getByText(/1000¢ per 1,000 leads \(\$0\.010\/lead\)/),
    ).toBeVisible();

    await billing.getByRole("button", { name: "Post adjustment" }).click();
    const adjust = page.getByRole("dialog", {
      name: "Post Credit Wallet adjustment",
    });
    await adjust.getByLabel(/Amount/).fill("10.50");
    await adjust.getByLabel("Reason (required)").fill("Stripe refund reconcile");
    await adjust.getByRole("button", { name: "Post adjustment" }).click();
    await expect(adjust).toBeHidden();

    expect(state.adjustmentRequests).toEqual([
      { amount_cents: 1050, reason: "Stripe refund reconcile" },
    ]);
    await expect(billing.getByText("Stripe refund reconcile")).toBeVisible();
  });

  test("updates the Cooldown Window with an audit reason", async ({ page }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7");

    const cooldown = page.getByRole("region", { name: "Cooldown Window" });
    await expect(cooldown.getByText("7 days")).toBeVisible();
    await cooldown
      .getByRole("button", { name: "Change Cooldown Window" })
      .click();

    const dialog = page.getByRole("dialog", {
      name: "Change Cooldown Window",
    });
    await dialog.getByLabel("Days (required)").fill("14");
    await dialog.getByLabel("Reason (required)").fill("Longer exclusivity");
    await dialog.getByRole("button", { name: "Save Cooldown Window" }).click();
    await expect(dialog).toBeHidden();

    expect(state.cooldownPutRequests).toEqual([
      { days: 14, reason: "Longer exclusivity" },
    ]);
    await expect(cooldown.getByText("14 days")).toBeVisible();
  });

  test("previews Niche Policy availability before saving", async ({ page }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7");

    const policy = page.getByRole("region", { name: "Niche Policy" });
    await expect(policy.getByText(/All states: exclude roofing/)).toBeVisible();
    await policy.getByRole("button", { name: "Edit Niche Policy" }).click();

    const dialog = page.getByRole("dialog", { name: "Edit Niche Policy" });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel("State", { exact: true }).selectOption("TX");
    await dialog.getByLabel("Mode (required)").selectOption("only");
    await dialog.getByLabel("Niches (required)").fill("solar, hvac");

    await expect(
      dialog.getByRole("button", { name: "Save Niche Policy" }),
    ).toBeDisabled();

    await dialog
      .getByRole("button", { name: "Preview projected availability" })
      .click();
    await expect(
      dialog.getByText("Projected available Leads: 128"),
    ).toBeVisible();

    await dialog.getByLabel("Reason (required)").fill("TX solar focus");
    await dialog.getByRole("button", { name: "Save Niche Policy" }).click();
    await expect(dialog).toBeHidden();

    expect(state.nichePolicyPreviewRequests).toEqual([
      {
        rows: [{ state: "TX", mode: "only", niches: ["solar", "hvac"] }],
      },
    ]);
    expect(state.nichePolicyPutRequests).toEqual([
      {
        rows: [{ state: "TX", mode: "only", niches: ["solar", "hvac"] }],
        reason: "TX solar focus",
      },
    ]);
    await expect(policy.getByText(/TX: only solar, hvac/)).toBeVisible();
  });
});
