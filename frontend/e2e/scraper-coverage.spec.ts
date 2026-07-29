import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

import { mockAdminMFA } from "./mfa-fixtures";
import {
  coverageFeed,
  coverageGrid,
  coverageKeywords,
  stateCoverageDetail,
  stateCoverageSnapshot,
} from "./scraper-coverage-fixtures";

const STATES = "./admin/acquisition/scraper/workspace/states";
const PA = `${STATES}/PA`;

interface CoverageOptions {
  refreshSeconds?: { keywords: number; cells: number };
  keywordRefresh?: (route: Route) => Promise<unknown>;
  cellRefresh?: (route: Route) => Promise<unknown>;
}

async function mockCoverage(page: Page, options: CoverageOptions = {}) {
  await mockAdminMFA(page, { assurance: "aal2" });
  const refresh = options.refreshSeconds ?? { keywords: 10, cells: 15 };

  // Registered after the shared Scraper mock so these handlers own #64's
  // exact contract.
  await page.route(/\/api\/admin\/scraper\/coverage$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(stateCoverageSnapshot()),
    }),
  );
  await page.route(/\/api\/admin\/scraper\/coverage\/PA$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(stateCoverageDetail(refresh)),
    }),
  );
  await page.route(
    /\/api\/admin\/scraper\/coverage\/PA\/keywords$/,
    (route) =>
      options.keywordRefresh?.(route) ??
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          coverageFeed(coverageKeywords, refresh.keywords),
        ),
      }),
  );
  await page.route(
    /\/api\/admin\/scraper\/coverage\/PA\/cells$/,
    (route) =>
      options.cellRefresh?.(route) ??
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(coverageFeed(coverageGrid, refresh.cells)),
      }),
  );
}

test.describe("Scraper state coverage", () => {
  test("preserves the state-card hierarchy, counts, status and navigation", async ({
    page,
  }) => {
    await mockCoverage(page);
    await page.goto(STATES);

    await expect(
      page.getByRole("heading", { level: 1, name: "States" }),
    ).toBeVisible();
    const pa = page.getByRole("link", {
      name: /PA: In progress, 50% coverage, 161,863 businesses/,
    });
    await expect(pa).toContainText("110/220 cells");
    await expect(pa).toContainText("25 keywords");
    await expect(
      page.getByRole("link", { name: "Configuration versions" }),
    ).toBeVisible();
    await expect(
      page.getByRole("navigation", {
        name: "Scraper Operations terminal sections",
      }).getByRole("link", { name: "Overview" }),
    ).toBeVisible();
  });

  test("preserves keyword activity and every grid-cell state", async ({
    page,
  }) => {
    await mockCoverage(page);
    await page.goto(PA);

    const keywords = page.getByRole("region", { name: "Keywords" });
    await expect(keywords).toContainText("24 Hour Pharmacy");
    await expect(keywords).toContainText("110/220");
    await expect(keywords).toContainText("12.5%");
    await expect(keywords).toContainText("Jul 28, 11:59");

    const totals = page.getByRole("list", { name: "Grid status totals" });
    for (const status of ["Posted", "Reserved", "Failed", "Uncovered"]) {
      await expect(totals).toContainText(status);
    }
    await expect(
      page.getByRole("button", { name: /^Cell \d:/ }),
    ).toHaveCount(4);
  });

  test("a transient refresh failure keeps prior data and focused input", async ({
    page,
  }) => {
    await mockCoverage(page, {
      refreshSeconds: { keywords: 0.4, cells: 15 },
      keywordRefresh: (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(coverageFeed(null, 0.4)),
        }),
    });
    await page.goto(PA);

    const search = page.getByRole("searchbox", {
      name: "Find keyword activity",
    });
    await search.focus();
    await expect(search).toBeFocused();

    const region = page.getByRole("region", { name: "Keywords" });
    await expect(region.getByRole("status")).toContainText("Not refreshing");
    await expect(search).toBeFocused();
    await expect(region).toContainText("24 Hour Pharmacy");
  });

  test("grid drill-down is keyboard-operable and refresh does not move focus", async ({
    page,
  }) => {
    await mockCoverage(page, {
      refreshSeconds: { keywords: 10, cells: 0.4 },
    });
    await page.goto(PA);

    const failed = page.getByRole("button", {
      name: "Cell 3: 40.000000,-79.500000 — Failed",
    });
    await failed.focus();
    await page.keyboard.press("Enter");
    await expect(failed).toHaveAttribute("aria-pressed", "true");
    await expect(
      page.getByLabel("Selected grid cell"),
    ).toContainText("Cell 3 of 4");

    await page.waitForTimeout(550);
    await expect(failed).toBeFocused();
    await expect(
      page.getByRole("button", { name: "Previous cell" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Next cell" }),
    ).toBeVisible();
  });

  test("has no horizontal overflow", async ({ page }) => {
    await mockCoverage(page);
    await page.goto(PA);

    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("mobile turns every grid square into a labeled drill-down without hiding actions", async ({
    page,
  }) => {
    test.skip(
      (page.viewportSize()?.width ?? 1000) > 600,
      "Mobile presentation assertion",
    );
    await mockCoverage(page);
    await page.goto(PA);

    for (const name of [
      "Cell 1: 40.000000,-80.000000 — Posted",
      "Cell 2: 40.000000,-79.750000 — Reserved",
      "Cell 3: 40.000000,-79.500000 — Failed",
      "Cell 4: 40.000000,-79.250000 — Uncovered",
    ]) {
      const cell = page.getByRole("button", { name });
      await expect(cell).toBeVisible();
      expect((await cell.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    }
    await expect(
      page.getByRole("combobox", { name: "Grid status" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Previous cell" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Next cell" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Configuration versions" }),
    ).toBeVisible();
  });

  test("state overview visual regression", async ({ page }) => {
    await mockCoverage(page);
    await page.goto(STATES);

    await expect(page).toHaveScreenshot("scraper-state-overview.png", {
      fullPage: true,
      animations: "disabled",
      // GitHub's Ubuntu image carries additional system fonts beyond the
      // Playwright container used to produce the Linux baselines. Keep the
      // comparison strict while allowing sub-pixel glyph rasterization noise.
      maxDiffPixelRatio: 0.002,
    });
  });

  test("state detail visual regression", async ({ page }) => {
    await mockCoverage(page);
    await page.goto(PA);

    await expect(page).toHaveScreenshot("scraper-state-detail.png", {
      fullPage: true,
      animations: "disabled",
      maxDiffPixelRatio: 0.002,
    });
  });
});
