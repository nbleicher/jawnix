import { expect, test } from "@playwright/test";

import { mockAdminCustomers } from "./admin-customers-fixtures";
import { mockEmptySearch } from "./today-fixtures";

test.beforeEach(async ({ page }) => {
  await mockEmptySearch(page);
});

test.describe("Customer billing and allocation controls", () => {
  test("changes the rate per 1,000 and posts a Credit Wallet adjustment on Billing", async ({
    page,
  }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7/billing");

    const wallet = page.getByRole("region", { name: "Credit wallet" });
    await expect(wallet.getByText("Billing on", { exact: true })).toBeVisible();
    await expect(wallet.getByText("$5.00 per 1,000")).toBeVisible();
    await expect(wallet.getByText("$1,232.00")).toBeVisible();

    const ledger = page.getByRole("table", { name: "Credit ledger" });
    const charge = ledger.getByRole("row", { name: /Batch #1033 delivered/ });
    await expect(charge).toContainText("−$250.00");
    await expect(charge).toContainText("System");
    await expect(charge.getByRole("link", { name: "#1033" })).toHaveAttribute(
      "href",
      "/app/admin/batches/1033",
    );
    await expect(ledger.getByRole("row", { name: /Opening credit/ })).toContainText("Operator");
    await expect(page.getByRole("table", { name: "Purchases" })).toContainText("$1,707.00");

    await wallet.getByRole("button", { name: "Configure billing" }).click();
    const configure = page.getByRole("dialog", { name: "Configure billing" });
    await configure.getByLabel(/Rate per 1,000 leads/).fill("6");
    await configure.getByLabel("Reason (required)").fill("Contract renewal");
    await configure.getByRole("button", { name: "Save billing" }).click();
    await expect(configure).toBeHidden();

    expect(state.billingPutRequests).toEqual([
      {
        billing_enabled: true,
        lead_rate_cents_per_thousand: 600,
        reason: "Contract renewal",
      },
    ]);
    await expect(wallet.getByText("$6.00 per 1,000")).toBeVisible();

    await wallet.getByRole("button", { name: "Post adjustment" }).click();
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
    await expect(ledger.getByText("Stripe refund reconcile")).toBeVisible();
  });

  test("updates the Cooldown Window with an audit reason on Rules", async ({ page }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7/rules");

    const cooldown = page.getByRole("region", { name: "Cooldown window" });
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
    await page.goto("./admin/customers/7/rules");

    const policy = page.getByRole("region", { name: "Niche policy" });
    const current = policy.getByRole("table", { name: "Current Niche Policy" });
    await expect(current).toContainText("All states");
    await expect(current).toContainText("roofing");
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
    await expect(current).toContainText("solar, hvac");
  });

  test("changes the database on Rules with a reason", async ({ page }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7/rules");

    const database = page.getByRole("region", { name: "Database" });
    await expect(database).toContainText("DB1 · Operator leads");
    await database.getByRole("button", { name: "Change database" }).click();
    const dialog = page.getByRole("dialog", { name: "Change database" });
    await dialog.getByLabel("Reason (required)").fill("Customer asked for uploads only");
    await dialog.getByRole("button", { name: "Use DB2" }).click();
    await expect(dialog).toBeHidden();
    expect(state.databaseRequests.map((request) => request.body)).toEqual([
      { lead_database: "db2", reason: "Customer asked for uploads only" },
    ]);
  });
});
