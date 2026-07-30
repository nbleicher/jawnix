import { expect, test } from "@playwright/test";

import { mockAdminCustomers } from "./admin-customers-fixtures";

test.describe("Customer administration", () => {
  test("narrows the directory through the URL and keeps both standings visible", async ({
    page,
  }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers");

    await expect(
      page.getByRole("heading", { level: 1, name: "Customers" }),
    ).toBeVisible();
    await expect(
      page.getByRole("article", { name: "Harbor Insurance" }),
    ).toBeVisible();
    await expect(
      page.getByRole("article", { name: "Lakeside Brokers" }),
    ).toBeVisible();

    const lakeside = page.getByRole("article", { name: "Lakeside Brokers" });
    // The durable standing and the replaceable access standing are labelled
    // separately on every row.
    await expect(lakeside.getByText("Customer", { exact: true })).toBeVisible();
    await expect(
      lakeside.getByText("User Account", { exact: true }),
    ).toBeVisible();
    await expect(lakeside.getByText("Active", { exact: true })).toBeVisible();
    await expect(
      lakeside.getByText("Invitation sent", { exact: true }),
    ).toBeVisible();
    await expect(
      lakeside.getByText("Invitation has not been accepted yet"),
    ).toBeVisible();

    await page.getByLabel("Search").fill("harbor");
    await page.getByRole("button", { name: "Search" }).click();

    await expect(page).toHaveURL(/q=harbor/);
    await expect(
      page.getByRole("article", { name: "Lakeside Brokers" }),
    ).toBeHidden();
    await expect(
      page.getByRole("article", { name: "Harbor Insurance" }),
    ).toBeVisible();
    expect(
      state.directoryRequests.some((url) =>
        new URL(url).searchParams.get("q")?.includes("harbor"),
      ),
    ).toBe(true);
  });

  test("opens a Customer and separates identity from permanent history", async ({
    page,
  }) => {
    await mockAdminCustomers(page);
    await page.goto("./admin/customers");

    await page.getByRole("link", { name: "Harbor Insurance" }).click();

    await expect(page).toHaveURL(/\/app\/admin\/customers\/7$/);
    await expect(
      page.getByRole("heading", { level: 1, name: "Harbor Insurance" }),
    ).toBeVisible();

    const identity = page.getByRole("region", { name: "Customer" });
    await expect(identity.getByText("harbor-insurance")).toBeVisible();
    await expect(identity.getByText("Gulf Coast Agency")).toBeVisible();

    const permanent = page.getByRole("region", { name: "Permanent history" });
    await expect(permanent.getByText("240")).toBeVisible();
    await expect(
      permanent.getByText(/Replacing a User Account never resets any of it/),
    ).toBeVisible();

    const former = page.getByRole("region", { name: "Former User Accounts" });
    await expect(former.getByText("previous@harbor.example")).toBeVisible();
  });

  test("states that the current User Account stays active while an invitation is pending", async ({
    page,
  }) => {
    await mockAdminCustomers(page, { pendingInvitation: true });
    await page.goto("./admin/customers/7");

    const pending = page.getByRole("article", { name: "Pending invitation" });
    await expect(pending.getByText("newowner@harbor.example")).toBeVisible();
    await expect(
      pending.getByText(
        /The current User Account stays active until this invitation is accepted/,
      ),
    ).toBeVisible();

    // The account being replaced is still shown as the active one.
    const current = page.getByRole("article", { name: "Current User Account" });
    await expect(current.getByText("owner@harbor.example")).toBeVisible();
    await expect(current.getByText("Active", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Replace User Account" }),
    ).toBeHidden();
  });

  test("keeps the primary action on each screen keyboard operable", async ({
    page,
  }) => {
    await mockAdminCustomers(page);
    await page.goto("./admin/customers");

    const create = page.getByRole("button", { name: "Create Customer" });
    await create.focus();
    await expect(create).toBeFocused();
    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleName("Create Customer");
    await expect(dialog).toHaveAccessibleDescription(
      /Customer sets Licensed States from Account after accepting; administrators never set or see a password/i,
    );
    await expect(dialog.getByLabel("Licensed States")).toHaveCount(0);
    await expect(dialog.getByLabel("Reason")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();

    await page.goto("./admin/customers/7");
    const replace = page.getByRole("button", { name: "Replace User Account" });
    await replace.focus();
    await expect(replace).toBeFocused();
    await page.keyboard.press("Enter");

    const invite = page.getByRole("dialog");
    await expect(invite).toBeVisible();
    await expect(
      invite.getByText(
        /The current User Account stays active until this invitation is accepted/,
      ),
    ).toBeVisible();
  });

  test("never renders a password field on either screen", async ({ page }) => {
    await mockAdminCustomers(page, { pendingInvitation: true });

    await page.goto("./admin/customers");
    await expect(
      page.getByRole("heading", { level: 1, name: "Customers" }),
    ).toBeVisible();
    await expect(page.locator('input[type="password"]')).toHaveCount(0);

    await page.getByRole("button", { name: "Create Customer" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.locator('input[type="password"]')).toHaveCount(0);

    await page.goto("./admin/customers/7");
    await expect(
      page.getByRole("heading", { level: 1, name: "Harbor Insurance" }),
    ).toBeVisible();
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
  });
});
