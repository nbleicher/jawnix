import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { mockCustomerAuth } from "./customer-auth-fixtures";
import {
  CUSTOMER_HOME,
  EXPIRED_BATCH_ID,
  EXPIRING_BATCH_ID,
  IN_PROGRESS_BATCH_ID,
  NEW_CUSTOMER_HOME,
  NORTHSTAR_WALLET,
  READY_BATCH_ID,
  RENDER_DAY,
  SETUP_INCOMPLETE_HOME,
  homeAt,
} from "./customer-home-fixtures";
import { mockBatchRequests } from "./customer-requests-fixtures";

const WCAG_AA_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

test.describe("Customer Home", () => {
  test("answers what the Customer came for, in Customer words", async ({ page }) => {
    await mockCustomerAuth(page, { billing: { wallet: NORTHSTAR_WALLET } });
    await page.goto("./home");

    await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
    await expect(page.getByText("Northstar Insurance")).toBeVisible();
    await expect(page.locator("main").getByRole("link", { name: "New request" }).first())
      .toHaveAttribute("href", "/app/requests");

    // The header balance and Home agree.
    const header = page.getByRole("banner");
    await expect(header.getByText("$412.50")).toBeVisible();
    await expect(header.getByRole("button", { name: "Buy credits" })).toBeVisible();

    const alerts = page.getByRole("list", { name: "Needs your attention" });
    await expect(alerts).toContainText("File expires in 3 days.");
    await expect(alerts).toContainText("Your 50,000-lead batch from");
    await expect(alerts.getByRole("link", { name: "Download Batch #1024" }))
      .toHaveAttribute("href", `/api/me/batch-requests/${EXPIRING_BATCH_ID}/artifact`);

    const numbers = page.getByLabel("Account summary");
    await expect(numbers).toContainText("Covers 82,500 leads at $5.00 per 1,000");
    await expect(numbers).toContainText("FL, TX");
    await expect(numbers).toContainText("150,000");
    await expect(numbers).toContainText("2 batches");
    await expect(numbers).toContainText("$750.00");
    await expect(numbers).toContainText("$125.00 held for #1042");
    await expect(numbers.getByRole("link", { name: "Edit appointed states" }))
      .toHaveAttribute("href", "/app/account/states");

    const table = page.getByRole("table", { name: "Recent batches" });
    await expect(table.getByRole("row")).toHaveCount(5);
    await expect(table.getByRole("link", { name: "Download Batch #1038" }))
      .toHaveAttribute("href", `/api/me/batch-requests/${READY_BATCH_ID}/artifact`);
    await expect(table.getByRole("link", { name: "View Batch #1017, file expired" }))
      .toHaveAttribute("href", `/app/requests?request=${EXPIRED_BATCH_ID}`);
    await expect(page.getByRole("link", { name: "All batches (24)" }))
      .toHaveAttribute("href", "/app/requests#batch-history");

    const panel = page.getByRole("region", { name: "In progress · #1042" });
    await expect(panel).toContainText("25,000 leads · FL · submitted today 09:41");
    await expect(panel.getByRole("listitem")).toHaveText([
      /Submitted\s*09:41/,
      /Waiting for approval\s*Now/,
      /Preparing your leads/,
      /Ready to download/,
    ]);
    await expect(panel).toContainText("$125.00 is held from your balance until then.");

    // Supply stays hidden and internal words never reach the Customer (V6).
    const body = page.locator("body");
    await expect(body).not.toContainText(/REQ-|Batch Artifact|Batch Hold|Licensed States/);
  });

  test("downloads a ready file from its row", async ({ page }) => {
    await mockCustomerAuth(page);
    const requests = await mockBatchRequests(page);
    await page.goto("./home");

    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("table", { name: "Recent batches" })
      .getByRole("link", { name: "Download Batch #1038" })
      .click();
    await downloadPromise;
    expect(requests.artifactDownloads).toEqual([READY_BATCH_ID]);
  });

  test("cancels the batch in progress after confirming", async ({ page }) => {
    let canceled = false;
    const auth = await mockCustomerAuth(page, {
      overview: () => (canceled ? { ...CUSTOMER_HOME, in_progress: [] } : CUSTOMER_HOME),
    });
    const requests = await mockBatchRequests(page);
    await page.route(/\/api\/me\/batch-requests\/[^/]+\/cancel$/, async (route) => {
      canceled = true;
      await route.fallback();
    });
    await page.goto("./home");

    const panel = page.getByRole("region", { name: "In progress · #1042" });
    await panel.getByRole("button", { name: "Cancel batch" }).click();
    const dialog = page.getByRole("dialog", { name: "Cancel Batch #1042?" });
    await expect(dialog).toHaveAccessibleDescription(/releases the \$125\.00 held/);
    const reads = auth.overviewRequests;
    await dialog.getByRole("button", { name: "Cancel batch" }).click();

    await expect(panel).toHaveCount(0);
    expect(requests.cancellations).toEqual([IN_PROGRESS_BATCH_ID]);
    expect(auth.overviewRequests).toBeGreaterThan(reads);
  });

  test("reads Home again on arrival from another page", async ({ page }) => {
    const auth = await mockCustomerAuth(page);
    await page.goto("./home");
    await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
    const nav = page.getByRole("navigation", { name: "Customer" });

    await nav.getByRole("link", { name: "Account" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
    const reads = auth.overviewRequests;

    await nav.getByRole("link", { name: "Home" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
    expect(auth.overviewRequests).toBe(reads + 1);
  });

  test("points a new Customer at their first request", async ({ page }) => {
    await mockCustomerAuth(page, {
      overview: NEW_CUSTOMER_HOME,
      billing: { wallet: { ...NORTHSTAR_WALLET, balanceCents: 0, activeHoldsCents: 0, availableBalanceCents: 0 } },
    });
    await page.goto("./home");

    await expect(page.getByRole("heading", { level: 2, name: "No batches yet" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Request your first batch" }))
      .toHaveAttribute("href", "/app/requests");
    await expect(page.getByRole("table")).toHaveCount(0);

    const alerts = page.getByRole("list", { name: "Needs your attention" });
    await expect(alerts).toContainText("Low balance. $0.00 covers 0 leads at $5.00 per 1,000.");
    await alerts.getByRole("button", { name: "Buy credits" }).click();
    await expect(page.getByRole("dialog", { name: "Buy credits" })).toBeVisible();
  });

  test("sends unfinished setup to Account", async ({ page }) => {
    await mockCustomerAuth(page, { overview: SETUP_INCOMPLETE_HOME });
    await page.goto("./home");

    const alerts = page.getByRole("list", { name: "Needs your attention" });
    await expect(alerts).toContainText("Setup incomplete. Add your appointed states before you request a batch.");
    await alerts.getByRole("link", { name: "Add appointed states" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Account" })).toBeVisible();
  });

  for (const [name, overview] of [
    ["new Customer", NEW_CUSTOMER_HOME],
    ["unfinished setup", SETUP_INCOMPLETE_HOME],
  ] as const) {
    test(`has no accessibility violations for a ${name}`, async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await mockCustomerAuth(page, { overview });
      await page.goto("./home");
      await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();

      const results = await new AxeBuilder({ page }).withTags(WCAG_AA_TAGS).analyze();
      expect(results.violations).toEqual([]);
    });
  }
});

test.describe("Customer Home visual baseline", () => {
  test("matches the visual baseline", async ({ page }) => {
    await page.clock.setFixedTime(RENDER_DAY);
    await mockCustomerAuth(page, {
      overview: homeAt(RENDER_DAY).CUSTOMER_HOME,
      billing: { wallet: NORTHSTAR_WALLET },
    });
    await page.goto("./home");
    await expect(page.getByRole("table", { name: "Recent batches" })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-scheme", "light");

    await expect(page).toHaveScreenshot("customer-home.png", {
      fullPage: true,
      // Variable-font rasterization shifts sub-pixel anti-aliasing by up to
      // ~2% between runs; structural regressions are far larger.
      maxDiffPixelRatio: 0.03,
      animations: "disabled",
    });
  });
});

test.describe("Overview links", () => {
  test("old Overview links land on Home with their query", async ({ page }) => {
    await mockCustomerAuth(page, { billing: { wallet: NORTHSTAR_WALLET } });
    await page.goto("./overview?purchase=cancelled");

    await expect(page.getByRole("heading", { level: 1, name: "Home" })).toBeVisible();
    // The Stripe outcome the query carried still shows, then leaves the address.
    await expect(page.getByText("Credit Purchase cancelled.", { exact: false })).toBeVisible();
    await expect(page).toHaveURL(/\/app\/home$/);
  });

  test("signing in from an old Overview link returns to Home with its query", async ({ page }) => {
    await mockCustomerAuth(page, { overviewStatus: 401 });
    await page.goto("./overview?purchase=success");

    await expect(page).toHaveURL(
      `/app/sign-in?next=${encodeURIComponent("/app/home?purchase=success")}`,
    );
  });
});
