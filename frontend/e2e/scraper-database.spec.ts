import { readFile } from "node:fs/promises";

import { expect, test } from "@playwright/test";
import type { Download, Page, Route } from "@playwright/test";

import {
  DATABASE_STATE,
  DATABASE_WORKSPACE,
} from "./scraper-database-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";

const DATABASE = "./admin/acquisition/scraper/workspace/database";
const OH = `${DATABASE}/states/OH`;
const DAY = "2026-07-29";

interface DatabaseMockState {
  browseRequests: URLSearchParams[];
  regenerationCalls: number;
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function csv(route: Route, filename: string, body: string) {
  return route.fulfill({
    status: 200,
    contentType: "text/csv",
    headers: {
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
    body,
  });
}

async function mockDatabase(
  page: Page,
  workspace = DATABASE_WORKSPACE,
): Promise<DatabaseMockState> {
  await mockAdminMFA(page, { assurance: "aal2" });
  const state: DatabaseMockState = {
    browseRequests: [],
    regenerationCalls: 0,
  };

  // Registered after the shared Scraper handler so this spec owns every #67
  // request, including binary download responses.
  await page.route(/\/api\/admin\/scraper\/database(?:[/?].*)?$/, (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/admin/scraper/database") {
      state.browseRequests.push(url.searchParams);
      const pageNumber = Number(url.searchParams.get("page") ?? "1");
      const search = url.searchParams.get("search") ?? "";
      const selectedState = url.searchParams.get("state") ?? "";
      return json(route, {
        ...workspace,
        browse: workspace.browse
          ? {
              ...workspace.browse,
              search,
              state: selectedState,
              page: pageNumber,
              records:
                pageNumber === 2
                  ? [
                      {
                        title: "Second Page Roofing",
                        phone: "216-555-0199",
                        website: null,
                        state: "OH",
                        niche: "roofers",
                        last_seen: "Jul 28, 11:57",
                      },
                    ]
                  : workspace.browse.records,
              has_previous: pageNumber > 1,
              has_next: pageNumber < 2,
            }
          : null,
      });
    }
    if (path.endsWith("/states/OH")) {
      return json(route, DATABASE_STATE);
    }
    if (path.endsWith("/exports/OH/regenerate")) {
      state.regenerationCalls += 1;
      return json(route, {
        generated: "OH.csv",
        stored_exports: workspace.stored_exports,
      });
    }
    if (path.endsWith("/exports/states")) {
      const states = url.searchParams.getAll("state");
      const label = states.length <= 4
        ? states.join("-")
        : `${states.length}-states`;
      return csv(
        route,
        `${label}-phone-leads-${DAY}.csv`,
        [
          "business_name,phone_number,state",
          ...states.map((value) => `${value} Business,5550000000,${value}`),
          "",
        ].join("\n"),
      );
    }
    if (path.endsWith("/exports/state/OH")) {
      const niches = url.searchParams.getAll("niche");
      const label = niches.length === 0
        ? "all"
        : niches.length === 1
          ? niches[0] === "__uncategorized__"
            ? "uncategorized"
            : niches[0]
          : `${niches.length}-niches`;
      return csv(
        route,
        `OH-${label}-phone-leads-${DAY}.csv`,
        "business_name,phone_number,state\nBuckeye Plumbing,6145550101,OH\n",
      );
    }
    if (path.endsWith("/exports/stored/OH.csv")) {
      return csv(
        route,
        "OH.csv",
        "phone,title\n6145550101,Buckeye Plumbing\n",
      );
    }
    return json(route, { detail: "Unexpected database request" }, 500);
  });
  return state;
}

async function contents(download: Download): Promise<string> {
  const path = await download.path();
  if (!path) throw new Error("The browser did not materialize the download.");
  return readFile(path, "utf8");
}

test.describe("Scraper database browsing and exports", () => {
  test("preserves totals, supported search, state filters, paging and Niche context", async ({
    page,
  }) => {
    const calls = await mockDatabase(page);
    await page.goto(DATABASE);

    await expect(
      page.getByRole("heading", { level: 1, name: "Scraper Database" }),
    ).toBeVisible();
    await expect(page.getByLabel("All businesses")).toContainText("9,244,326");
    await expect(page.getByLabel("Exportable phones")).toContainText("2,305,025");
    await expect(page.getByText("Buckeye Plumbing")).toHaveCount(0);
    await expect(page.getByText("Search before browsing records")).toBeVisible();

    await page.getByRole("searchbox", { name: "Search records" }).fill("buckeye.example");
    await page.getByRole("combobox", {
      name: "State",
      exact: true,
    }).selectOption("OH");
    await page.getByRole("button", { name: "Search" }).click();
    await expect(page.getByText("Buckeye Plumbing")).toBeVisible();
    await expect(page.getByText("plumbers", { exact: true })).toBeVisible();
    await expect(page.getByText("51 matching businesses")).toBeVisible();
    await expect.poll(() => calls.browseRequests.length).toBe(2);
    expect(calls.browseRequests.at(-1)?.get("search")).toBe("buckeye.example");
    expect(calls.browseRequests.at(-1)?.get("state")).toBe("OH");

    await page.getByRole("link", { name: "Next →" }).click();
    await expect(page.getByText("Second Page Roofing")).toBeVisible();
    await expect(page.getByText("Page 2 of 2")).toBeVisible();
    expect(calls.browseRequests.at(-1)?.get("page")).toBe("2");

    await page.goto(OH);
    await expect(page.getByLabel("State businesses")).toContainText("136,150");
    await expect(page.getByLabel("State unique phones")).toContainText("71,204");
    await expect(page.getByRole("region", {
      name: "Niches",
      exact: true,
    })).toContainText("Uncategorized");
    await expect(
      page.getByRole("link", { name: "Download entire state" }),
    ).toBeVisible();
  });

  test("retains every current CSV shape and filename semantic", async ({
    page,
  }) => {
    await mockDatabase(page);
    await page.goto(DATABASE);

    await page.getByRole("region", { name: "State exports" }).locator("summary").click();
    await page.getByRole("checkbox", { name: "Select OH" }).check();
    await page.getByRole("checkbox", { name: "Select PA" }).check();
    const bulkPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download selected states" }).click();
    const bulk = await bulkPromise;
    expect(bulk.suggestedFilename()).toBe(`OH-PA-phone-leads-${DAY}.csv`);
    expect(await contents(bulk)).toBe(
      "business_name,phone_number,state\n"
      + "OH Business,5550000000,OH\n"
      + "PA Business,5550000000,PA\n",
    );

    await page.getByRole("region", { name: "Stored exports" }).locator("summary").click();
    const storedPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "Download stored export" }).first().click();
    const stored = await storedPromise;
    expect(stored.suggestedFilename()).toBe("OH.csv");
    expect(await contents(stored)).toBe(
      "phone,title\n6145550101,Buckeye Plumbing\n",
    );

    await page.goto(OH);
    const allPromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "Download entire state" }).click();
    const all = await allPromise;
    expect(all.suggestedFilename()).toBe(`OH-all-phone-leads-${DAY}.csv`);
    expect(await contents(all)).toContain(
      "business_name,phone_number,state",
    );

    const nichePromise = page.waitForEvent("download");
    await page.getByRole("link", { name: "Download plumbers" }).click();
    const niche = await nichePromise;
    expect(niche.suggestedFilename()).toBe(
      `OH-plumbers-phone-leads-${DAY}.csv`,
    );

    await page.getByRole("checkbox", { name: "Select plumbers" }).check();
    await page.getByRole("checkbox", { name: "Select Uncategorized" }).check();
    const selectedPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download selected Niches" }).click();
    const selected = await selectedPromise;
    expect(selected.suggestedFilename()).toBe(
      `OH-2-niches-phone-leads-${DAY}.csv`,
    );
  });

  test("regenerates stored exports through a deliberate audited action", async ({
    page,
  }) => {
    const state = await mockDatabase(page);
    await page.goto(DATABASE);

    await page.getByRole("region", { name: "Stored exports" }).locator("summary").click();
    await page.getByRole("button", { name: "Regenerate stored exports" }).click();
    const dialog = page.getByRole("dialog", {
      name: "Regenerate stored exports?",
    });
    await expect(dialog).toContainText(
      "replace its stored STATE.csv files",
    );
    await dialog.getByRole("button", { name: "Regenerate exports" }).click();

    await expect(
      page.getByText("OH.csv regenerated. Stored downloads are current."),
    ).toBeVisible();
    expect(state.regenerationCalls).toBe(1);
    await expect(
      page.getByRole("link", { name: "Download stored export" }),
    ).toHaveCount(2);
  });

  test("a service outage leaves a safe, topology-free recovery screen", async ({
    page,
  }) => {
    const unavailable = {
      ...DATABASE_WORKSPACE,
      service_state: "unavailable",
      last_successful_at: null,
      totals: null,
      states: [],
      browse: null,
      stored_exports: [],
    } as const;
    await mockDatabase(page);
    await page.route(
      /\/api\/admin\/scraper\/database(?:\?.*)?$/,
      (route) => json(route, unavailable),
    );
    await page.goto(DATABASE);

    const error = page.getByRole("alert");
    await expect(error).toContainText("Scraper database unavailable");
    await expect(error).toContainText("Last successful connection: never");
    await expect(error).not.toContainText("10.77.0.2");
    await expect(error).not.toContainText("credential");
    await expect(
      page.getByRole("button", { name: "Try again" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Download|Regenerate/ }),
    ).toHaveCount(0);
  });

  test("remains usable without horizontal overflow on mobile and desktop", async ({
    page,
  }) => {
    await mockDatabase(page);
    await page.goto(OH);

    const overflow = await page.evaluate(
      () =>
        document.documentElement.scrollWidth
        - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await expect(
      page.getByRole("button", { name: "Download selected Niches" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Download Uncategorized" }),
    ).toBeVisible();
  });
});
