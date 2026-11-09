import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import {
  CUSTOMER_ACCOUNT_IDENTITY,
  LICENSED_STATE_ACCOUNT,
  mockLicensedStates,
} from "./customer-account-fixtures";
import { mockCustomerAuth } from "./customer-auth-fixtures";
import type { CustomerAuthMockOptions } from "./customer-auth-fixtures";
import { BILLED_CUSTOMER_WALLET } from "./customer-billing-fixtures";
import { CUSTOMER_HOME } from "./customer-home-fixtures";
import { MAPPING_BLOCKED_BATCH_REQUEST_WORKSPACE } from "./customer-requests-fixtures";

async function openAccount(
  page: Page,
  path = "./account",
  options: Parameters<typeof mockLicensedStates>[1] = {},
  auth: CustomerAuthMockOptions = {},
) {
  const state = await mockLicensedStates(page, options);
  const provider = await mockCustomerAuth(page, {
    overview: () => ({ ...CUSTOMER_HOME }),
    batchRequests: () => state.requestWorkspace,
    licensedStates: () => {
      state.accountReads += 1;
      return state.account;
    },
    ...auth,
  });
  await page.goto(path);
  await expect(
    page.getByRole("heading", { level: 1, name: "Account" }),
  ).toBeVisible();
  return { state, provider };
}

function tabs(page: Page) {
  return page.getByRole("navigation", { name: "Account" });
}

test.describe("tabs in the URL", () => {
  test("each tab has its own address and one is current", async ({ page }) => {
    await openAccount(page, "./account", {}, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });

    await expect(page.getByText("River Morgan · Northstar Insurance")).toBeVisible();
    await expect(tabs(page).getByRole("link", { name: "Profile" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    for (const [label, path] of [
      ["Appointed states", /\/app\/account\/states$/],
      ["Billing", /\/app\/account\/billing$/],
      ["Exclusion lists", /\/app\/account\/exclusion-lists$/],
      ["Password", /\/app\/account\/password$/],
      ["Profile", /\/app\/account$/],
    ] as const) {
      await tabs(page).getByRole("link", { name: label }).click();
      await expect(page).toHaveURL(path);
      await expect(tabs(page).getByRole("link", { name: label })).toHaveAttribute(
        "aria-current",
        "page",
      );
    }
    await expect(page.getByRole("heading", { name: "Your details" })).toBeVisible();
  });

  test("an unknown tab returns to Profile", async ({ page }) => {
    await openAccount(page, "./account/nowhere");

    await expect(page).toHaveURL(/\/app\/account$/);
    await expect(page.getByRole("heading", { name: "Your details" })).toBeVisible();
  });

  test("Customers who are not billed have no Billing tab", async ({ page }) => {
    await openAccount(page);

    await expect(tabs(page).getByRole("link")).toHaveText([
      "Profile",
      "Appointed states",
      "Exclusion lists",
      "Password",
    ]);
  });
});

test.describe("profile", () => {
  test("names what stands between the Customer and a first batch", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      profile: {
        ...CUSTOMER_ACCOUNT_IDENTITY,
        customer_id: null,
        mapping_confirmed_at: null,
      },
      licensedStates: { ...LICENSED_STATE_ACCOUNT, states: [] },
    });
    await page.goto("./account");

    const details = page.getByRole("region", { name: "Your details" });
    await expect(details.getByRole("textbox", { name: "First name" })).toHaveValue("River");
    await expect(details.getByRole("textbox", { name: "Last name" })).toHaveValue("Morgan");
    await expect(details.getByRole("textbox", { name: "Sign-in email" })).toHaveValue(
      "river@northstar.example",
    );

    const setup = page.getByRole("region", { name: "Before you can request batches" });
    await expect(setup.getByText("Your account is being set up")).toBeVisible();
    await expect(setup.getByText("No appointed states")).toBeVisible();
    await setup.getByRole("link", { name: "Appointed states" }).click();
    await expect(page).toHaveURL(/\/app\/account\/states$/);
  });

  test("the Requests blocker deep-links to its visible Account problem", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      profile: {
        ...CUSTOMER_ACCOUNT_IDENTITY,
        customer_id: null,
        mapping_confirmed_at: null,
      },
      batchRequests: MAPPING_BLOCKED_BATCH_REQUEST_WORKSPACE,
    });
    await page.goto("./requests");

    await page.getByRole("link", { name: "Review Account" }).click();

    await expect(page).toHaveURL(/\/app\/account$/);
    await expect(
      page
        .getByRole("region", { name: "Before you can request batches" })
        .getByText("Your account is being set up"),
    ).toBeVisible();
  });

  test("saves name and phone", async ({ page }) => {
    await openAccount(page);
    let saved: unknown = null;
    await page.route(/\/api\/me\/profile$/, (route) => {
      if (route.request().method() !== "PATCH") return route.fallback();
      saved = route.request().postDataJSON();
      return route.fulfill({
        json: { ...CUSTOMER_ACCOUNT_IDENTITY, phone: "(215) 555-0199" },
      });
    });

    const details = page.getByRole("region", { name: "Your details" });
    await expect(details.getByRole("button", { name: "Save profile" })).toBeDisabled();
    await details.getByRole("textbox", { name: "Phone" }).fill("(215) 555-0199");
    await details.getByRole("button", { name: "Save profile" }).click();

    await expect(details.getByRole("status")).toHaveText("Profile saved.");
    expect(saved).toEqual({
      first_name: "River",
      last_name: "Morgan",
      phone: "(215) 555-0199",
      licensed_states: ["FL", "TX"],
    });
  });
});

test.describe("appointed states (V10)", () => {
  test("fills the grid down each column, with District of Columbia apart", async ({
    page,
    isMobile,
  }) => {
    await openAccount(page, "./account/states");

    const grid = page.locator(".appointed-states__grid");
    const columns = await grid.evaluate(
      (element) => getComputedStyle(element).gridTemplateColumns.split(" ").length,
    );
    expect(columns).toBe(isMobile ? 2 : 5);
    const states = grid.getByRole("checkbox");
    await expect(states).toHaveCount(50);
    // Down each column: the first column ends where the second begins.
    const rows = isMobile ? 25 : 10;
    const first = await states.nth(0).boundingBox();
    const lastOfColumn = await states.nth(rows - 1).boundingBox();
    const nextColumn = await states.nth(rows).boundingBox();
    expect(first!.x).toBe(lastOfColumn!.x);
    expect(nextColumn!.y).toBe(first!.y);
    expect(nextColumn!.x).toBeGreaterThan(first!.x);
    await expect(states.nth(rows - 1)).toHaveAccessibleName(isMobile ? "Missouri" : "Georgia");

    await expect(grid.getByRole("checkbox", { name: /District of Columbia/ })).toHaveCount(0);
    await expect(
      page.getByRole("checkbox", { name: "Also appointed in District of Columbia" }),
    ).not.toBeChecked();
    await expect(page.getByText("2 selected · Florida, Texas")).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Florida" })).toBeChecked();
    await expect(page.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  test("finds states by name or code and stays keyboard operable", async ({ page }) => {
    await openAccount(page, "./account/states");

    const search = page.getByRole("searchbox", { name: "Find a state" });
    await search.fill("carolina");
    await expect(page.getByRole("checkbox", { name: "North Carolina" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "South Carolina" })).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "Texas" })).toHaveCount(0);

    await search.fill("TX");
    const texas = page.getByRole("checkbox", { name: "Texas" });
    await texas.focus();
    await page.keyboard.press("Space");
    await expect(texas).not.toBeChecked();
    await expect(page.getByText("1 selected · Florida")).toBeVisible();

    // Enter picks the one state left, and never submits the form.
    // "ca" still leaves both Carolinas, so Enter waits for one match.
    await search.fill("ca");
    await search.press("Enter");
    await expect(page.getByRole("checkbox", { name: "California" })).not.toBeChecked();
    await search.fill("calif");
    await search.press("Enter");
    await expect(page.getByRole("checkbox", { name: "California" })).toBeChecked();
    await expect(search).toHaveValue("");
    await expect(page.getByText("2 selected · California, Florida")).toBeVisible();

    await search.fill("zz");
    await expect(page.getByRole("status").filter({ hasText: "No state matches" })).toBeVisible();
  });

  test("adding a state saves without the review dialog", async ({ page }) => {
    const { state } = await openAccount(page, "./account/states");

    await page.getByRole("checkbox", { name: "California" }).check();
    await page.getByRole("checkbox", { name: "Also appointed in District of Columbia" }).check();
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByRole("status").filter({ hasText: "Appointed states saved" })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(state.previews).toEqual([
      { states: ["CA", "DC", "FL", "TX"], expected_version: "2026-07-29T12:00:00+00:00" },
    ]);
    expect(state.applies).toBe(1);
    await expect(page.getByText("4 selected · California, District of Columbia, Florida, Texas")).toBeVisible();
    await expect(page.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  test("removing a state a batch in progress needs asks first", async ({ page }) => {
    const { state } = await openAccount(page, "./account/states");

    await page.getByRole("checkbox", { name: "California" }).check();
    await page.getByRole("checkbox", { name: "Texas" }).uncheck();
    await page.getByRole("button", { name: "Save" }).click();

    const dialog = page.getByRole("dialog", { name: "Confirm appointed states" });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleDescription(/Nothing changes until you confirm/);
    await expect(dialog.getByText("Batch #1042")).toBeVisible();
    await expect(dialog.getByText("Batch #1043")).toBeVisible();
    await expect(dialog.getByText("Will be narrowed")).toBeVisible();
    await expect(dialog.getByText("Will be canceled")).toBeVisible();
    await expect(dialog.getByText(/None of its states would remain/)).toBeVisible();
    await expect(dialog.getByText(/New states apply to new requests/)).toBeVisible();
    expect(state.applies).toBe(0);

    await dialog.getByRole("button", { name: "Keep editing" }).click();
    expect(state.applies).toBe(0);
    await expect(page.getByRole("checkbox", { name: "Texas" })).not.toBeChecked();

    await page.getByRole("button", { name: "Save" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Confirm and save" }).click();

    await expect(page.getByRole("status").filter({ hasText: "Appointed states saved" })).toBeVisible();
    expect(state.applies).toBe(1);
    expect(state.previews[0]).toEqual({
      states: ["CA", "FL"],
      expected_version: "2026-07-29T12:00:00+00:00",
    });

    // What was saved is what Account reads back after leaving and returning.
    await page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Home" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
    await page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Account" }).click();
    await tabs(page).getByRole("link", { name: "Appointed states" }).click();
    await expect(page.getByText("2 selected · California, Florida")).toBeVisible();
  });

  test("refuses a concurrent update and reloads the latest selection", async ({ page }) => {
    const { state } = await openAccount(page, "./account/states", { conflictOnFirstApply: true });

    await page.getByRole("checkbox", { name: "Texas" }).uncheck();
    await page.getByRole("button", { name: "Save" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Confirm and save" }).click();

    await expect(page.getByRole("alert")).toContainText("changed in another session");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("checkbox", { name: "California" })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: "Texas" })).toBeChecked();
    expect(state.applies).toBe(1);
    expect(state.accountReads).toBeGreaterThanOrEqual(2);
  });
});

test.describe("exclusion lists", () => {
  test("live on Account with the upload form and every list", async ({ page }) => {
    await openAccount(page, "./account/exclusion-lists", {}, {
      exclusionLists: [
        {
          id: "11111111-1111-4111-8111-111111111111",
          type: "dnc",
          filename: "northstar-dnc.csv",
          status: "confirmed",
          totalRows: 1_200,
          acceptedRows: 1_150,
          invalidRows: 30,
          duplicateRows: 20,
          poolImpact: 4,
          global: false,
          error: "",
          createdAt: "2026-10-02T12:00:00Z",
          ingestedAt: "2026-10-02T12:01:00Z",
          decidedAt: null,
        },
      ],
    });

    await expect(page.getByRole("button", { name: "Upload list" })).toBeVisible();
    const lists = page.getByRole("table", { name: "Your exclusion lists" });
    await expect(lists).toContainText("northstar-dnc.csv");
    await expect(lists).toContainText("Protecting your batches");
    await expect(lists).toContainText("1,150");
  });
});

test.describe("password", () => {
  test("changes the password at the provider with the current one", async ({ page }) => {
    const { provider } = await openAccount(page, "./account/password");

    await page.getByLabel("Current password").fill("old-password-test-only");
    await page.getByLabel("New password", { exact: true }).fill("new-password-test-only");
    await page.getByLabel("Confirm new password").fill("new-password-test-only");
    await page.getByRole("button", { name: "Change password" }).click();

    await expect(page.getByRole("status").filter({ hasText: "Password changed" })).toBeVisible();
    const change = provider.providerRequests.find((item) => item.path.endsWith("/change-password"));
    expect(change?.body).toEqual({
      currentPassword: "old-password-test-only",
      newPassword: "new-password-test-only",
      revokeOtherSessions: true,
    });
    await expect(page.getByLabel("Current password")).toHaveValue("");
  });

  test("checks the new password before asking the provider", async ({ page }) => {
    const { provider } = await openAccount(page, "./account/password");

    await page.getByLabel("Current password").fill("old-password-test-only");
    await page.getByLabel("New password", { exact: true }).fill("short");
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByText("Use at least 8 characters.")).toBeVisible();

    await page.getByLabel("New password", { exact: true }).fill("new-password-test-only");
    await page.getByLabel("Confirm new password").fill("something-else");
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByText("The two new passwords do not match.")).toBeVisible();
    expect(provider.providerRequests.some((item) => item.path.endsWith("/change-password"))).toBe(false);
  });

  test("a wrong current password is named on its field", async ({ page }) => {
    await openAccount(page, "./account/password", {}, { passwordChange: "wrong_password" });

    await page.getByLabel("Current password").fill("not-it-test-only");
    await page.getByLabel("New password", { exact: true }).fill("new-password-test-only");
    await page.getByLabel("Confirm new password").fill("new-password-test-only");
    await page.getByRole("button", { name: "Change password" }).click();

    await expect(page.getByLabel("Current password")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("Your current password is not correct.")).toBeVisible();
  });

  test("an old sign-in or a forgotten password gets a reset link by email", async ({ page }) => {
    const { provider } = await openAccount(page, "./account/password", {}, { passwordChange: "expired" });

    await page.getByLabel("Current password").fill("old-password-test-only");
    await page.getByLabel("New password", { exact: true }).fill("new-password-test-only");
    await page.getByLabel("Confirm new password").fill("new-password-test-only");
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByRole("alert")).toContainText("Use a reset link instead");

    await page.getByRole("button", { name: "Email me a reset link" }).click();
    await expect(
      page.getByRole("status").filter({ hasText: "We sent a link to river@northstar.example" }),
    ).toBeVisible();
    const reset = provider.providerRequests.find((item) => item.path.endsWith("/request-password-reset"));
    expect(reset?.body).toMatchObject({ email: "river@northstar.example" });
  });
});
