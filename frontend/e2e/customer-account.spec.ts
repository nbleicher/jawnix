import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import {
  CUSTOMER_ACCOUNT_IDENTITY,
  LICENSED_STATE_ACCOUNT,
  mockLicensedStates,
} from "./customer-account-fixtures";
import { mockCustomerAuth } from "./customer-auth-fixtures";
import { CUSTOMER_OVERVIEW } from "./customer-overview-fixtures";
import { MAPPING_BLOCKED_BATCH_REQUEST_WORKSPACE } from "./customer-requests-fixtures";

async function openAccount(
  page: Page,
  options: Parameters<typeof mockLicensedStates>[1] = {},
) {
  const state = await mockLicensedStates(page, options);
  await mockCustomerAuth(page, {
    overview: () => ({
      ...CUSTOMER_OVERVIEW,
    }),
    batchRequests: () => state.requestWorkspace,
    licensedStates: () => {
      state.accountReads += 1;
      return state.account;
    },
  });
  await page.goto("./account");
  await expect(
    page.getByRole("heading", { level: 1, name: "Account" }),
  ).toBeVisible();
  return state;
}

test.describe("identity and setup status", () => {
  test("names every Setup Problem with what happens next and who acts", async ({
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

    const identity = page.getByRole("region", { name: "Identity" });
    await expect(identity.getByText("River Morgan")).toBeVisible();
    await expect(identity.getByText("river@northstar.example")).toBeVisible();

    const status = page.getByRole("region", { name: "Setup status" });
    await expect(
      status.getByRole("heading", {
        name: "Customer mapping needs confirmation",
      }),
    ).toBeVisible();
    await expect(
      status.getByText(/Jawnix will confirm which Customer record/),
    ).toBeVisible();
    await expect(
      status.getByRole("heading", { name: "No Licensed States" }),
    ).toBeVisible();
    await expect(
      status.getByText(/Add at least one Licensed State below/),
    ).toBeVisible();
    await expect(status.getByText("What happens next")).toHaveCount(2);
    await expect(status.getByText("Who acts")).toHaveCount(2);
    await expect(status.getByText("Jawnix", { exact: true })).toBeVisible();
    await expect(status.getByText("You", { exact: true })).toBeVisible();
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
    const setupStatus = page.getByRole("region", { name: "Setup status" });
    await expect(
      setupStatus.getByRole("heading", {
        name: "Customer mapping needs confirmation",
      }),
    ).toBeVisible();
    await expect(setupStatus.getByText("Jawnix", { exact: true })).toBeVisible();
  });
});

test.describe("safe Licensed State management", () => {
  test("searches by name and remains keyboard operable", async ({ page }) => {
    await openAccount(page);

    await expect(
      page.getByRole("button", { name: "Review changes" }),
    ).toBeDisabled();
    const search = page.getByRole("searchbox", { name: "Search states" });
    await search.fill("carolina");
    await expect(
      page.getByRole("checkbox", { name: "North Carolina NC" }),
    ).toBeVisible();
    await expect(
      page.getByRole("checkbox", { name: "South Carolina SC" }),
    ).toBeVisible();
    await expect(
      page.getByRole("checkbox", { name: "Texas TX" }),
    ).toHaveCount(0);

    await search.fill("TX");
    const texas = page.getByRole("checkbox", { name: "Texas TX" });
    await texas.focus();
    await expect(texas).toBeFocused();
    await page.keyboard.press("Space");
    await expect(texas).not.toBeChecked();
    await expect(
      page.getByRole("button", { name: "Review changes" }),
    ).toBeEnabled();
  });

  test("previews every narrowed and canceled request before applying", async ({
    page,
  }) => {
    const state = await openAccount(page);

    await page.getByRole("checkbox", { name: "California CA" }).check();
    await page.getByRole("checkbox", { name: "Texas TX" }).uncheck();
    await page.getByRole("button", { name: "Review changes" }).click();

    const dialog = page.getByRole("dialog", {
      name: "Review Licensed State changes",
    });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleDescription(
      /Nothing changes until you confirm/,
    );
    await expect(
      dialog.getByText("750-lead Batch Request"),
    ).toBeVisible();
    await expect(
      dialog.getByText("300-lead Batch Request"),
    ).toBeVisible();
    await expect(dialog.getByText("Will be narrowed")).toBeVisible();
    await expect(dialog.getByText("Will be canceled")).toBeVisible();
    await expect(
      dialog.getByText(/final requested state will cancel/),
    ).toBeVisible();
    await expect(
      dialog.getByText(/Added states apply only to future Batch Requests/),
    ).toBeVisible();
    expect(state.applies).toBe(0);

    await dialog.getByRole("button", { name: "Keep editing" }).click();
    expect(state.applies).toBe(0);
    await expect(
      page.getByRole("checkbox", { name: "Texas TX" }),
    ).not.toBeChecked();

    await page.getByRole("button", { name: "Review changes" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm and save" })
      .click();

    await expect(page.locator(".licensed-state-page__message--success")).toContainText(
      "Overview and Batch Requests are up to date",
    );
    expect(state.applies).toBe(1);
    expect(state.previews).toHaveLength(2);
    expect(state.previews[0]).toEqual({
      states: ["CA", "FL"],
      expected_version: "2026-07-29T12:00:00+00:00",
    });

    await page
      .getByRole("navigation", { name: "Customer" })
      .getByRole("link", { name: "Overview" })
      .click();
    const licensedStates = page.getByRole("region", {
      name: "Licensed States",
    });
    await expect(licensedStates.getByText("CA", { exact: true })).toBeVisible();
    await expect(licensedStates.getByText("FL", { exact: true })).toBeVisible();
    await expect(licensedStates.getByText("TX", { exact: true })).toHaveCount(0);

    await page
      .getByRole("navigation", { name: "Customer" })
      .getByRole("link", { name: "Requests" })
      .click();
    await page
      .getByRole("spinbutton", { name: /How many leads/ })
      .fill("1");
    await page.getByRole("button", { name: "Continue" }).click();
    await expect(
      page.getByRole("radio", { name: /All my Licensed States \(CA, FL\)/ }),
    ).toBeVisible();
  });

  test("explains that additions affect only future requests", async ({
    page,
  }) => {
    await openAccount(page);

    await expect(
      page.getByRole("heading", {
        name: "Additions affect future requests only",
      }),
    ).toBeVisible();
    await page.getByRole("checkbox", { name: "California CA" }).check();
    await page.getByRole("button", { name: "Review changes" }).click();

    const dialog = page.getByRole("dialog");
    await expect(
      dialog.getByText(
        "No unallocated Batch Requests will be narrowed or canceled.",
      ),
    ).toBeVisible();
    await expect(
      dialog.getByText(/Existing requests will not be expanded/),
    ).toBeVisible();
  });

  test("refuses a concurrent update and reloads the latest selection", async ({
    page,
  }) => {
    const state = await openAccount(page, { conflictOnFirstApply: true });

    await page.getByRole("checkbox", { name: "Texas TX" }).uncheck();
    await page.getByRole("button", { name: "Review changes" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm and save" })
      .click();

    await expect(page.getByRole("alert")).toContainText(
      "changed in another session",
    );
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(
      page.getByRole("checkbox", { name: "California CA" }),
    ).toBeChecked();
    await expect(
      page.getByRole("checkbox", { name: "Texas TX" }),
    ).toBeChecked();
    expect(state.applies).toBe(1);
    expect(state.accountReads).toBeGreaterThanOrEqual(2);
  });
});
