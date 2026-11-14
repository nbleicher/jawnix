import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import {
  ANOMALY_ID,
  CONFIGURATION_ID,
  RECOMMENDATION_ID,
  SCRAPE_RUN_ID,
  mockAcquisition,
} from "./acquisition-fixtures";
import { mockActivity } from "./activity-fixtures";
import { mockInsights } from "./insights-fixtures";
import { mockAgencies, mockSetup } from "./setup-fixtures";
import {
  CUSTOMER_ACCOUNT_IDENTITY,
  LICENSED_STATE_ACCOUNT,
  mockLicensedStates,
} from "./customer-account-fixtures";
import { BILLED_CUSTOMER_WALLET } from "./customer-billing-fixtures";
import { mockCustomerAuth } from "./customer-auth-fixtures";
import { mockBatchRequests } from "./customer-requests-fixtures";
import { mockAdminCustomers } from "./admin-customers-fixtures";
import {
  CONFLICT_ID,
  PENDING_REQUEST_ID,
  mockFulfillment,
} from "./fulfillment-fixtures";
import { OPEN_REPORT_ID, mockLeadReports } from "./lead-report-fixtures";
import { WAITING_LIST_ID, mockInventory } from "./inventory-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import { BATCH_IDS, mockOperatorDesk } from "./today-fixtures";

/**
 * WCAG 2.2 AA foundations, verified in a real browser.
 *
 * The unit suite can only assert the markup contract; focus order, the native
 * dialog's focus containment, and computed contrast need a real engine.
 */

const WCAG_AA_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const ROUTES = [
  "./sign-in",
  "./sign-in?password=updated",
  "./accept-invitation",
  "./reset-password",
  "./reset-password?token=fixture-one-time-password-token",
  "./home",
  "./new-request",
  "./new-request?quantity=100000&states=FL&rows_per_file=10000&purchase=success",
  "./batches",
  "./batches/1042",
  "./batches/1038",
  "./batches/1017",
  "./batches/9999",
  "./account",
  "./account/states",
  "./account/billing",
  "./account/exclusion-lists",
  "./account/password",
  "./admin/today",
  `./admin/today?decision=approval%3A${BATCH_IDS[1042]}`,
  `./admin/today?decision=inventoryConflict%3A${CONFLICT_ID}`,
  `./admin/today?decision=scrapeAnomaly%3A${ANOMALY_ID}`,
  `./admin/today?decision=leadReport%3A${OPEN_REPORT_ID}`,
  "./admin/batches",
  "./admin/batches?batch=1042",
  "./admin/batches/1042",
  "./admin/batches/1040",
  "./admin/inventory",
  "./admin/inventory/lookup",
  "./admin/inventory/lookup?phone=6155550142",
  "./admin/inventory/lookup?phone=2125550100",
  "./admin/inventory/uploads",
  "./admin/inventory/exclusions",
  `./admin/inventory/exclusions?list=${WAITING_LIST_ID}`,
  "./admin/inventory/suppressed",
  "./admin/inventory/niche-assignment",
  "./admin/inventory/pool",
  `./admin/fulfillment/requests/${PENDING_REQUEST_ID}`,
  `./admin/fulfillment/conflicts/${CONFLICT_ID}`,
  `./admin/fulfillment/reports/${OPEN_REPORT_ID}`,
  "./admin/insights",
  "./admin/insights/sources",
  "./admin/setup",
  "./admin/setup?area=billing",
  "./admin/setup/configurations",
  `./admin/setup/configurations?recommendation=${RECOMMENDATION_ID}`,
  "./admin/setup/niche-mappings",
  "./admin/setup/niche-mappings?segment=TX%3A%3Aroofing%20contractor",
  `./admin/acquisition/configurations/${CONFIGURATION_ID}`,
  `./admin/acquisition/runs/${SCRAPE_RUN_ID}`,
  "./admin/acquisition/scraper",
  "./admin/acquisition/scraper/workspace",
  "./admin/acquisition/scraper/workspace/keywords",
  "./admin/acquisition/scraper/workspace/history",
  "./admin/acquisition/scraper/workspace/runtime",
  "./admin/acquisition/scraper/workspace/states",
  "./admin/acquisition/scraper/workspace/states/PA",
  "./admin/acquisition/scraper/workspace/database",
  "./admin/acquisition/scraper/workspace/database/states/OH",
  "./admin/customers",
  "./admin/customers?customer=7",
  "./admin/customers?status=deactivated",
  "./admin/activity",
  "./admin/customers/7",
  "./admin/customers/7/batches?batch=1041",
  "./admin/customers/7/billing",
  "./admin/customers/7/rules",
  "./admin/customers/7/reports",
  "./admin/customers/7/access",
  "./admin/customers/7/activity",
  "./admin/agencies",
  "./admin/agencies/4",
  "./admin/security",
  "./admin/mfa/enroll",
  "./admin/mfa/challenge",
  "./admin/mfa/recover",
];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockAcquisition(page);
  await mockCustomerAuth(page);
  await mockBatchRequests(page);
  await mockAdminCustomers(page, { pendingInvitation: true });
  await mockFulfillment(page);
  await mockLeadReports(page);
  await mockActivity(page);
  await mockOperatorDesk(page);
  await mockInventory(page);
  await mockInsights(page);
  await mockSetup(page);
  await mockAgencies(page);
});

test.describe("Automated WCAG 2.2 AA sweep", () => {
  /*
   * Colour tokens transition, and routes that own a theme swap it in an effect
   * after mount. The <h1> becomes visible before that transition settles, so an
   * axe run started at that moment samples *interpolated* colours — values in
   * neither theme's palette — and reports contrast failures that no user can
   * ever see. It fails or passes purely on machine speed, which is how it
   * survived on main: CI's usual ordering happened to be slow enough.
   *
   * Collapsing motion is the product's own guard for this (reset.css), and a
   * reduced-motion user must pass the same sweep anyway, so opting in costs no
   * coverage and makes the sampled colours the settled ones.
   */
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const reduced = await page.evaluate(
      () => matchMedia("(prefers-reduced-motion: reduce)").matches,
    );
    expect(reduced, "reduced-motion emulation did not apply").toBe(true);

    /*
     * Reduced motion alone only shrinks transitions to 0.01ms — it narrows the
     * window rather than closing it, which showed up as roughly one failure in
     * five full-suite runs. Removing transitions outright makes the computed
     * colour jump straight to its settled value, so the result no longer
     * depends on how fast the machine is.
     */
    await page.addInitScript(() => {
      const kill = () => {
        const style = document.createElement("style");
        style.textContent =
          "*,*::before,*::after{transition:none !important;animation:none !important}";
        document.head.appendChild(style);
      };
      if (document.head) kill();
      else document.addEventListener("DOMContentLoaded", kill, { once: true });
    });
  });

  for (const route of ROUTES) {
    test(`${route} has no accessibility violations`, async ({ page }) => {
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

      const results = await new AxeBuilder({ page }).withTags(WCAG_AA_TAGS).analyze();

      expect(results.violations).toEqual([]);
    });
  }

  test("the open global search results have no accessibility violations", async ({ page }) => {
    await page.goto("./admin/today");
    await page.getByRole("combobox", { name: "Search customers, batches, uploads, and phones" }).fill("north");
    await expect(page.getByRole("listbox", { name: "Search results" }).getByRole("option")).toHaveCount(3);

    const results = await new AxeBuilder({ page }).withTags(WCAG_AA_TAGS).analyze();

    expect(results.violations).toEqual([]);
  });

  test("Today with a side panel is clean in the dark scheme", async ({ page }) => {
    await page.goto(`./admin/today?decision=approval%3A${BATCH_IDS[1042]}`);
    await expect(page.getByRole("complementary", { name: "#1042 · Northstar Insurance" })).toBeVisible();
    await page.getByRole("button", { name: "Switch to dark desk" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-scheme", "dark");

    const results = await new AxeBuilder({ page }).withTags(WCAG_AA_TAGS).analyze();

    expect(results.violations).toEqual([]);
  });

  test("Activity with an opened entry has no accessibility violations", async ({ page }) => {
    await page.goto("./admin/activity");
    await page.getByRole("button", { name: /Lead database changed: show what changed/ }).click();
    await expect(page.getByText("lead_database: db2")).toBeVisible();
    // Opening scrolls the row to the viewport edge, where the phone's fixed
    // bottom navigation overlaps whichever link happens to sit there; check
    // from the same resting position as the page sweep.
    await page.evaluate(() => window.scrollTo(0, 0));

    const results = await new AxeBuilder({ page }).withTags(WCAG_AA_TAGS).analyze();

    expect(results.violations).toEqual([]);
  });

  test("the Account Setup Problems state has no accessibility violations", async ({
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
    await expect(
      page.getByRole("heading", { name: "Before you can request batches" }),
    ).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();

    expect(results.violations).toEqual([]);
  });

  test("Billed Customer Account Billing has no accessibility violations", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await page.goto("./account/billing");
    await expect(
      page.getByRole("table", { name: "Billing history" }),
    ).toBeVisible();
    await expect(page.getByLabel("Credit Wallet summary")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();

    expect(results.violations).toEqual([]);
  });

  test("the appointed-states review dialog has no accessibility violations", async ({
    page,
  }) => {
    await mockLicensedStates(page);
    await page.goto("./account/states");
    await page.getByRole("checkbox", { name: "Texas" }).uncheck();
    await page.getByRole("button", { name: "Save" }).click();
    await expect(
      page.getByRole("dialog", { name: "Confirm appointed states" }),
    ).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();

    expect(results.violations).toEqual([]);
  });

  test("New request short on credits has no accessibility violations", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await mockBatchRequests(page);
    await page.goto("./new-request");
    await page.getByRole("textbox", { name: "How many leads?" }).fill("100000");
    await page.getByText(/^Options/).click();
    await expect(page.getByText("Short by")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();

    expect(results.violations).toEqual([]);
  });

  test("the batch page's report form with an error has no accessibility violations", async ({
    page,
  }) => {
    await page.goto("./batches/1017");
    const form = page.getByRole("region", { name: "Report a bad lead" });
    await form.getByRole("button", { name: "Send report" }).click();
    await expect(form.getByText("Enter the lead's 10-digit phone number.")).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();

    expect(results.violations).toEqual([]);
  });

  test("Buy credits dialog has no accessibility violations", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await page.goto("./home");
    await page.getByRole("button", { name: "Buy credits" }).click();
    await expect(
      page.getByRole("dialog", { name: "Buy credits" }),
    ).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();

    expect(results.violations).toEqual([]);
  });
});

test.describe("Landmarks", () => {
  test("exposes banner, navigation, and main exactly once", async ({ page }) => {
    await page.goto("./home");

    await expect(page.getByRole("banner")).toHaveCount(1);
    await expect(page.getByRole("navigation")).toHaveCount(1);
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  });
});

test.describe("Keyboard operation", () => {
  test("the skip link is the first stop and moves focus to main", async ({ page }) => {
    await page.goto("./home");
    await page.locator("body").press("Tab");

    const skipLink = page.getByRole("link", { name: "Skip to main content" });
    await expect(skipLink).toBeFocused();
    // It must become visible on focus, not merely exist off-screen.
    await expect(skipLink).toBeInViewport();

    await page.keyboard.press("Enter");
    await expect(page.locator("main#jx-main")).toBeFocused();
  });

  test("every primary destination is reachable by keyboard alone", async ({ page }) => {
    await page.goto("./home");

    const requests = page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "New request" });
    await requests.focus();
    await expect(requests).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(page.getByRole("heading", { level: 1, name: "New request" })).toBeVisible();
  });
});

test.describe("Touch targets", () => {
  /** Measures every visible control matching `selector`, naming any that fall
   *  below the 44px rule in either dimension. Waits for the first render:
   *  `goto` resolves before the shell's loader-gated first paint. */
  async function undersizedTargets(page: Page, selector: string) {
    const controls = page.locator(selector);
    await expect
      .poll(() => controls.count(), { message: `expected to find controls matching ${selector}` })
      .toBeGreaterThan(0);
    const count = await controls.count();

    const undersized: string[] = [];
    for (let index = 0; index < count; index += 1) {
      const control = controls.nth(index);
      if (!(await control.isVisible())) continue;
      const box = await control.boundingBox();
      if (!box) continue;
      if (box.height < 44 || box.width < 44) {
        const label = (await control.textContent())?.trim().slice(0, 30) || `#${index}`;
        undersized.push(`${label} (${Math.round(box.width)}x${Math.round(box.height)})`);
      }
    }
    return undersized;
  }

  test("navigation links meet the 44px minimum", async ({ page }) => {
    // The real mobile targets: measured on a shell route, not the gallery.
    await page.goto("./home");

    expect(await undersizedTargets(page, "nav a")).toEqual([]);
  });

  test("the skip link meets the 44px minimum once focused", async ({ page }) => {
    await page.goto("./home");
    await page.getByRole("link", { name: "Skip to main content" }).focus();

    const box = await page.getByRole("link", { name: "Skip to main content" }).boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  });
});

test.describe("Reduced motion", () => {
  // Emulated per test rather than via `test.use`: the fixture-level option did
  // not reach the page here, and a silently unemulated run would make these
  // assertions pass for the wrong reason.
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect
      .poll(() => page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches))
      .toBe(true);
  });

  test("the shell is still fully navigable", async ({ page }) => {
    await page.goto("./home");

    await page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Account" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
  });
});
