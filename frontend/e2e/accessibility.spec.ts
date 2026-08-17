import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import {
  CONFIGURATION_ID,
  SCRAPE_RUN_ID,
  mockAcquisition,
} from "./acquisition-fixtures";
import { mockActivity } from "./activity-fixtures";
import {
  CUSTOMER_ACCOUNT_IDENTITY,
  LICENSED_STATE_ACCOUNT,
} from "./customer-account-fixtures";
import { BILLED_CUSTOMER_WALLET } from "./customer-billing-fixtures";
import { mockFeedback } from "./customer-feedback-fixtures";
import { mockCustomerAuth } from "./customer-auth-fixtures";
import {
  BATCH_REQUEST_WORKSPACE,
  DELIVERED_REQUEST,
  WAITING_REQUEST,
  mockBatchRequests,
} from "./customer-requests-fixtures";
import { mockAdminCustomers } from "./admin-customers-fixtures";
import {
  CONFLICT_ID,
  PENDING_REQUEST_ID,
  mockFulfillment,
} from "./fulfillment-fixtures";
import { OPEN_REPORT_ID, mockLeadReports } from "./lead-report-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

/**
 * WCAG 2.2 AA foundations, verified in a real browser.
 *
 * The unit suite can only assert the markup contract; focus order, the native
 * dialog's focus containment, and computed contrast need a real engine.
 */

const WCAG_AA_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

const ROUTES = [
  "./sign-in",
  "./accept-invitation",
  "./overview",
  "./requests",
  `./requests?request=${WAITING_REQUEST.id}`,
  `./requests?request=${DELIVERED_REQUEST.id}`,
  "./feedback",
  "./account",
  "./admin/overview",
  "./admin/fulfillment",
  `./admin/fulfillment/requests/${PENDING_REQUEST_ID}`,
  `./admin/fulfillment/conflicts/${CONFLICT_ID}`,
  `./admin/fulfillment/reports/${OPEN_REPORT_ID}`,
  "./admin/acquisition",
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
  "./admin/activity",
  "./admin/customers/7",
  "./admin/agencies",
  "./admin/agencies/4",
  "./admin/security",
  "./admin/mfa/enroll",
  "./admin/mfa/challenge",
  "./admin/mfa/recover",
  "./design-system",
];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockAcquisition(page);
  await mockFeedback(page);
  await mockCustomerAuth(page);
  await mockBatchRequests(page, {
    workspace: {
      ...BATCH_REQUEST_WORKSPACE,
      requests: [...BATCH_REQUEST_WORKSPACE.requests, DELIVERED_REQUEST],
    },
  });
  await mockAdminCustomers(page, { pendingInvitation: true });
  await mockFulfillment(page);
  await mockLeadReports(page);
  await mockActivity(page);
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

  test("the design-system gallery is clean in the dark Match scheme", async ({ page }) => {
    await page.goto("./design-system");
    await page.getByRole("button", { name: "Switch to dark desk" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "match");
    await expect(page.locator("html")).toHaveAttribute("data-scheme", "dark");

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
      page.getByRole("heading", {
        name: "Customer mapping needs confirmation",
      }),
    ).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(WCAG_AA_TAGS)
      .analyze();

    expect(results.violations).toEqual([]);
  });

  test("Billed Customer Account with Credit Wallet has no accessibility violations", async ({
    page,
  }) => {
    await mockCustomerAuth(page, {
      billing: { wallet: BILLED_CUSTOMER_WALLET },
    });
    await page.goto("./account");
    await expect(
      page.getByRole("heading", { level: 2, name: "Credit Ledger" }),
    ).toBeVisible();
    await expect(page.getByLabel("Credit Wallet summary")).toBeVisible();

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
    await page.goto("./overview");
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
    await page.goto("./overview");

    await expect(page.getByRole("banner")).toHaveCount(1);
    await expect(page.getByRole("navigation")).toHaveCount(1);
    await expect(page.getByRole("main")).toHaveCount(1);
    await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  });
});

test.describe("Keyboard operation", () => {
  test("the skip link is the first stop and moves focus to main", async ({ page }) => {
    await page.goto("./overview");
    await page.locator("body").press("Tab");

    const skipLink = page.getByRole("link", { name: "Skip to main content" });
    await expect(skipLink).toBeFocused();
    // It must become visible on focus, not merely exist off-screen.
    await expect(skipLink).toBeInViewport();

    await page.keyboard.press("Enter");
    await expect(page.locator("main#jx-main")).toBeFocused();
  });

  test("every primary destination is reachable by keyboard alone", async ({ page }) => {
    await page.goto("./overview");

    const requests = page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Requests" });
    await requests.focus();
    await expect(requests).toBeFocused();
    await page.keyboard.press("Enter");

    await expect(page.getByRole("heading", { level: 1, name: "Requests" })).toBeVisible();
  });

  test("focus is visible on interactive controls", async ({ page }) => {
    await page.goto("./design-system");

    const button = page.getByRole("button", { name: "Primary" });
    await button.focus();

    const outlineWidth = await button.evaluate((element) => getComputedStyle(element).outlineWidth);
    expect(parseFloat(outlineWidth)).toBeGreaterThanOrEqual(2);
  });
});

test.describe("Dialog", () => {
  test("traps focus, closes on Escape, and restores focus to its opener", async ({ page }) => {
    await page.goto("./design-system");

    const opener = page.getByRole("button", { name: "Open dialog" });
    await opener.click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAccessibleName("Batch Request details");

    // The page behind a modal must be inert: tabbing repeatedly may pass through
    // the document body (native <dialog> cycling does that) but must never reach
    // an interactive control outside the dialog.
    const escapees: string[] = [];
    for (let index = 0; index < 12; index += 1) {
      await page.keyboard.press("Tab");
      const escapee = await page.evaluate(() => {
        const active = document.activeElement as HTMLElement | null;
        const openDialog = document.querySelector("dialog[open]");
        if (!active || !openDialog) return null;
        if (openDialog.contains(active)) return null;
        if (!active.matches("a[href], button, input, select, textarea, [tabindex]")) return null;
        return `${active.tagName}:${active.textContent?.trim().slice(0, 30) ?? ""}`;
      });
      if (escapee) escapees.push(escapee);
    }
    expect(escapees).toEqual([]);

    await page.keyboard.press("Escape");
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
  });

  test("a destructive confirmation states its consequence and survives a backdrop click", async ({ page }) => {
    await page.goto("./design-system");

    await page.getByRole("button", { name: "Open confirmation" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveAccessibleDescription(/Cancelling is permanent/);

    // Click the top-left corner, which is backdrop rather than panel.
    await page.mouse.click(4, 4);
    await expect(dialog).toBeVisible();
  });
});

test.describe("Touch targets", () => {
  /** Measures every visible control matching `selector`, naming any that fall
   *  below the 44px rule in either dimension. */
  async function undersizedTargets(page: Page, selector: string) {
    const controls = page.locator(selector);
    const count = await controls.count();
    expect(count, `expected to find controls matching ${selector}`).toBeGreaterThan(0);

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

  test("gallery controls meet the 44px minimum", async ({ page }) => {
    await page.goto("./design-system");

    expect(await undersizedTargets(page, "button")).toEqual([]);
  });

  test("navigation links meet the 44px minimum", async ({ page }) => {
    // The real mobile targets: measured on a shell route, not the gallery.
    await page.goto("./overview");

    expect(await undersizedTargets(page, "nav a")).toEqual([]);
  });

  test("the skip link meets the 44px minimum once focused", async ({ page }) => {
    await page.goto("./overview");
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

  test("progress and loading remain understandable without animation", async ({ page }) => {
    await page.goto("./design-system");

    // The loading state keeps its announcement and its reserved space; only the
    // movement is dropped.
    const status = page.getByRole("status").first();
    await expect(status).toBeAttached();

    const animation = await page
      .locator(".jx-loading__spinner")
      .first()
      .evaluate((element) => getComputedStyle(element).animationName);
    expect(animation).toBe("none");
  });

  test("the shell is still fully navigable", async ({ page }) => {
    await page.goto("./overview");

    await page.getByRole("navigation", { name: "Customer" }).getByRole("link", { name: "Account" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
  });
});
