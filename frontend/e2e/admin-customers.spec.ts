import { expect, test } from "@playwright/test";

import { CONFLICT_HREF, mockAdminCustomers } from "./admin-customers-fixtures";
import { mockEmptySearch } from "./today-fixtures";

test.beforeEach(async ({ page }) => {
  await mockEmptySearch(page);
});

test.describe("Customers", () => {
  test("lists customers by what they need, with totals, and narrows through the URL", async ({
    page,
  }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers");

    await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
    await expect(
      page.getByText("9 active, sorted by what each one needs. Select a row to see it beside the list."),
    ).toBeVisible();

    const table = page.getByRole("table", { name: "Customers" });
    const rows = table.locator("tbody tr");
    await expect(rows).toHaveCount(9);
    await expect(rows.first()).toContainText("Northstar Insurance");
    await expect(rows.first()).toContainText("Approve #1042");
    await expect(table.getByRole("row", { name: /Harbor Insurance/ })).toContainText(
      "Conflict with Lakeside Brokers",
    );
    await expect(table.getByRole("row", { name: /Meridian Health Brokers/ })).toContainText("Not billed");
    const totals = table.locator("tfoot tr");
    await expect(totals).toContainText("9 customers");
    await expect(totals).toContainText("6 need something");
    await expect(totals).toContainText("$5,997.50");
    await expect(totals).toContainText("1,569,666");

    // Exact: the header's global search is labelled "Search customers, …".
    await page.getByLabel("Search", { exact: true }).fill("harbor");
    await expect(page).toHaveURL(/q=harbor/);
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("Harbor Insurance");
    expect(
      state.directoryRequests.some((url) => new URL(url).searchParams.get("q") === "harbor"),
    ).toBe(true);

    await page.getByLabel("Search", { exact: true }).fill("");
    await expect(rows).toHaveCount(9);
    await page.getByRole("combobox", { name: "Database" }).selectOption("db2");
    await expect(page).toHaveURL(/database=db2/);
    await expect(rows).toHaveCount(2);
    await page.getByRole("combobox", { name: "Database" }).selectOption("");
    await page.getByRole("checkbox", { name: "Needs only" }).check();
    await expect(page).toHaveURL(/needs_only=true/);
    await expect(rows).toHaveCount(6);
    await page.getByRole("combobox", { name: "Billing" }).selectOption("not_billed");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("Lakeside Brokers");
  });

  test("opens a customer beside the list with quick actions and a full-page link", async ({
    page,
  }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers");

    await page.getByRole("link", { name: "Harbor Insurance" }).click();
    await expect(page).toHaveURL(/customer=7/);
    const panel = page.getByRole("complementary", { name: "Harbor Insurance" });
    await expect(panel.getByText("Customer · Gulf Coast Agency")).toBeVisible();
    await expect(panel.getByText("$1,232.00")).toBeVisible();
    await expect(panel.getByText("$250.00 held for #1041")).toBeVisible();
    await expect(panel.getByText("412,880")).toBeVisible();
    await expect(panel.getByText("#1041 · 50,000 leads · generating")).toBeVisible();
    await expect(panel.getByText("$5.00 per 1,000")).toBeVisible();
    await expect(panel.getByRole("link", { name: "Conflict with Lakeside Brokers" })).toHaveAttribute(
      "href",
      CONFLICT_HREF,
    );
    // Opening a row is not a new search: the table is not read again.
    expect(state.directoryRequests).toHaveLength(1);

    await panel.getByRole("button", { name: "Change database" }).click();
    const dialog = page.getByRole("dialog", { name: "Change database" });
    await dialog.getByLabel("Reason (required)").fill("Moving to uploaded leads");
    await dialog.getByRole("button", { name: "Use DB2" }).click();
    await expect(dialog).toBeHidden();
    expect(state.databaseRequests).toEqual([
      {
        url: expect.stringMatching(/\/api\/admin\/customers\/7\/lead-database$/),
        body: { lead_database: "db2", reason: "Moving to uploaded leads" },
      },
    ]);

    await panel.getByRole("button", { name: "Post adjustment" }).click();
    const adjust = page.getByRole("dialog", { name: "Post Credit Wallet adjustment" });
    await adjust.getByLabel(/Amount/).fill("10.50");
    await adjust.getByLabel("Reason (required)").fill("Goodwill credit");
    await adjust.getByRole("button", { name: "Post adjustment" }).click();
    await expect(adjust).toBeHidden();
    expect(state.adjustmentRequests).toEqual([{ amount_cents: 1050, reason: "Goodwill credit" }]);

    await panel.getByRole("button", { name: "Close panel" }).click();
    await expect(page).not.toHaveURL(/customer=7/);
    await page.getByRole("link", { name: "Harbor Insurance" }).click();
    await page.getByRole("complementary", { name: "Harbor Insurance" })
      .getByRole("link", { name: "Open full page" })
      .click();
    await expect(page).toHaveURL(/\/app\/admin\/customers\/7$/);
    await expect(page.getByRole("heading", { level: 1, name: "Harbor Insurance" })).toBeVisible();
  });

  test("reaches deactivated customers, registrations, and agencies from the list", async ({ page }) => {
    await mockAdminCustomers(page, {
      registrations: [
        {
          id: "reg-1",
          firstName: "River",
          lastName: "Morgan",
          email: "river@example.test",
          phone: "6155550100",
          states: ["TN"],
          matches: [{ id: 19, name: "River Morgan", slug: "river-morgan", email: "old@example.test" }],
        },
      ],
    });
    await page.goto("./admin/customers");

    await page.getByRole("link", { name: "Registrations (1 waiting)" }).click();
    const registrations = page.getByRole("region", { name: "Registrations awaiting review" });
    await expect(registrations).toBeInViewport();
    await expect(registrations.getByRole("button", { name: "Approve as new Customer" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Agencies (2)" })).toHaveAttribute("href", "/app/admin/agencies");

    await page.getByRole("link", { name: "Deactivated customers (1)" }).click();
    await expect(page).toHaveURL(/status=deactivated/);
    await expect(page.getByRole("heading", { level: 1, name: "Deactivated customers" })).toBeVisible();
    const rows = page.getByRole("table", { name: "Deactivated customers" }).locator("tbody tr");
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toContainText("Keystone Medicare");
    await page.getByRole("link", { name: "Active customers (9)" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
  });

  test("keeps the Create Customer action keyboard operable and never asks for a password", async ({
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
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
  });
});

test.describe("Customer page", () => {
  test("leads with six numbers and keeps each tab in the URL", async ({ page }) => {
    await mockAdminCustomers(page);
    await page.goto("./admin/customers/7");

    await expect(page.getByRole("heading", { level: 1, name: "Harbor Insurance" })).toBeVisible();
    await expect(page.getByText(/^Gulf Coast Agency · FL, TX · DB1 · customer since /)).toBeVisible();
    const numbers = page.locator('dl[aria-label="Customer numbers"]');
    await expect(numbers.locator(".jx-kpi")).toHaveCount(6);
    await expect(numbers).toContainText("$1,232.00");
    await expect(numbers).toContainText("$250.00 held for #1041");
    await expect(numbers).toContainText("412,880");
    await expect(numbers).toContainText("#1041 generating");
    await expect(numbers).toContainText("300,000");
    await expect(numbers).toContainText("Conflict with Lakeside Brokers");

    const tabs = page.getByRole("navigation", { name: "Customer sections" });
    await expect(tabs.getByRole("link", { name: "Summary" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("region", { name: "Permanent history" })).toContainText("240");

    await tabs.getByRole("link", { name: "Billing" }).click();
    await expect(page).toHaveURL(/\/app\/admin\/customers\/7\/billing$/);
    await expect(tabs.getByRole("link", { name: "Billing" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("table", { name: "Credit ledger" })).toContainText("Opening credit");

    await page.goBack();
    await expect(page).toHaveURL(/\/app\/admin\/customers\/7$/);
    await expect(page.getByRole("region", { name: "Permanent history" })).toBeVisible();

    await page.goto("./admin/customers/7/access");
    await expect(tabs.getByRole("link", { name: "Access" })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("table", { name: "Former user accounts" })).toContainText(
      "previous@harbor.example",
    );
  });

  test("opens a batch beside the customer's batches with its progress and actions", async ({ page }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7/batches");

    const table = page.getByRole("table", { name: "Batches" });
    await expect(table.locator("tbody tr")).toHaveCount(5);
    const first = table.getByRole("row", { name: /#1041/ });
    await expect(first).toContainText("50,000");
    await expect(first).toContainText("Generating");
    await expect(first).toContainText("$250.00 held");
    await expect(table.getByRole("row", { name: /#1036/ })).toContainText("Conflict");
    expect(new URL(state.customerBatchRequests[0]!).searchParams.get("customer")).toBe("7");

    await page.getByRole("link", { name: "#1041" }).click();
    await expect(page).toHaveURL(/\/customers\/7\/batches\?batch=1041$/);
    const panel = page.getByRole("complementary", { name: "#1041 · 50,000 leads" });
    await expect(panel.getByText("Batch · Generating")).toBeVisible();
    await expect(panel.getByText("50,000 of 50,000")).toBeVisible();
    await expect(panel.getByText("$250.00 held at $5.00 per 1,000")).toBeVisible();
    await expect(panel.getByText("owner@harbor.example")).toBeVisible();
    await expect(panel.getByRole("list", { name: "Batch #1041 progress" }).locator("li")).toHaveCount(5);
    await expect(panel.getByRole("button", { name: "Cancel batch" })).toBeVisible();
    await expect(panel.getByRole("link", { name: "Open full page" })).toHaveAttribute(
      "href",
      "/app/admin/batches/1041",
    );
    // The page did not read the Customer again to open a batch.
    expect(state.detailsRequests).toHaveLength(1);
  });

  test("states that the current User Account stays active while an invitation is pending", async ({
    page,
  }) => {
    await mockAdminCustomers(page, { pendingInvitation: true });
    await page.goto("./admin/customers/7/access");

    const pending = page.getByRole("article", { name: "Pending invitation" });
    await expect(pending.getByText("newowner@harbor.example", { exact: false })).toBeVisible();
    await expect(
      pending.getByText(
        /The current User Account stays active until this invitation is accepted/,
      ),
    ).toBeVisible();

    const current = page.getByRole("article", { name: "Current User Account" });
    await expect(current.getByText("owner@harbor.example")).toBeVisible();
    await expect(current.getByText("Active", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /Replace user account/i })).toBeHidden();
  });

  test("replaces the User Account by invitation from the header", async ({ page }) => {
    const state = await mockAdminCustomers(page);
    await page.goto("./admin/customers/7");

    const replace = page.getByRole("button", { name: "Replace user account" });
    await replace.focus();
    await page.keyboard.press("Enter");
    const invite = page.getByRole("dialog", { name: "Replace User Account" });
    await expect(
      invite.getByText(/The current User Account stays active until this invitation is accepted/),
    ).toBeVisible();
    await expect(page.locator('input[type="password"]')).toHaveCount(0);
    await invite.getByLabel("Email (required)").fill("newowner@harbor.example");
    await invite.getByRole("button", { name: "Send invitation" }).click();
    await expect(invite).toBeHidden();
    expect(state.invitationRequests).toEqual([
      expect.objectContaining({ email: "newowner@harbor.example" }),
    ]);
  });

  test("lists the customer's lead reports and exclusion lists", async ({ page }) => {
    await mockAdminCustomers(page);
    await page.goto("./admin/customers/7/reports");

    const reports = page.getByRole("table", { name: "Lead reports" });
    await expect(reports).toContainText("Wrong business or title");
    await expect(reports).toContainText("(813) 555-0142 · Bayside Roofing");
    await expect(reports.getByRole("link", { name: "#1027" })).toHaveAttribute("href", "/app/admin/batches/1027");

    await page.getByRole("navigation", { name: "Customer sections" }).getByRole("link", { name: "Rules" }).click();
    const exclusions = page.getByRole("table", { name: "Exclusion lists" });
    await expect(exclusions).toContainText("harbor-dnc.csv");
    await expect(exclusions).toContainText("Customer-scoped");
  });
});
