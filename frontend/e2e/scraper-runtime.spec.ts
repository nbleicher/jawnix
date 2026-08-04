import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

import { mockAdminMFA } from "./mfa-fixtures";
import {
  CAMPAIGN_HISTORY,
  RUNTIME_PREVIEW,
  RUNTIME_WORKSPACE,
} from "./scraper-runtime-fixtures";

const HISTORY = "./admin/acquisition/scraper/workspace/history";
const RUNTIME = "./admin/acquisition/scraper/workspace/runtime";

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

interface HistoryOptions {
  failureOnce?: boolean;
}

async function openHistory(page: Page, options: HistoryOptions = {}) {
  await mockAdminMFA(page, { assurance: "aal2" });
  let failure = options.failureOnce ?? false;
  const calls: URL[] = [];
  await page.route(/\/api\/admin\/scraper\/history(?:\?.*)?$/, (route) => {
    const url = new URL(route.request().url());
    calls.push(url);
    const search = (url.searchParams.get("search") ?? "").toLocaleLowerCase();
    const state = url.searchParams.get("state") ?? "";
    const sort = url.searchParams.get("sort") ?? "last_enqueued";
    const direction = url.searchParams.get("direction") ?? "desc";
    if (failure && (search || state)) {
      failure = false;
      return json(
        route,
        { detail: "Scraper Operations is unavailable." },
        503,
      );
    }
    let rows = CAMPAIGN_HISTORY.rows.filter(
      (row) =>
        (!search || row.keyword.toLocaleLowerCase().includes(search)) &&
        (!state || row.state === state),
    );
    if (sort === "cells_posted") {
      rows = [...rows].sort((left, right) =>
        direction === "asc"
          ? left.cells_posted - right.cells_posted
          : right.cells_posted - left.cells_posted,
      );
    }
    return json(route, {
      ...CAMPAIGN_HISTORY,
      search,
      state,
      sort,
      direction,
      rows,
    });
  });
  await page.goto(HISTORY);
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Scraper Campaign History",
    }),
  ).toBeVisible();
  return calls;
}

interface RuntimeOptions {
  conflictOnce?: boolean;
  previewFailureOnce?: boolean;
}

async function openRuntime(page: Page, options: RuntimeOptions = {}) {
  await mockAdminMFA(page, { assurance: "aal2" });
  let current = structuredClone(RUNTIME_WORKSPACE.current);
  let version = RUNTIME_WORKSPACE.version;
  let conflict = options.conflictOnce ?? false;
  let previewFailure = options.previewFailureOnce ?? false;
  const calls: Array<{
    path: string;
    body: Record<string, unknown>;
  }> = [];
  await page.route(/\/api\/admin\/scraper\/runtime(?:\/.*)?$/, (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body =
      request.method() === "GET"
        ? {}
        : (request.postDataJSON() as Record<string, unknown>);
    calls.push({ path, body });
    if (path.endsWith("/runtime") && request.method() === "GET") {
      return json(route, {
        ...RUNTIME_WORKSPACE,
        current,
        version,
      });
    }
    if (path.endsWith("/preview")) {
      if (previewFailure) {
        previewFailure = false;
        return json(
          route,
          { detail: "Scraper Operations is unavailable." },
          503,
        );
      }
      return json(route, {
        ...RUNTIME_PREVIEW,
        configuration: body.configuration,
        expected_version: version,
        review_token: `review-${version}`,
      });
    }
    if (path.endsWith("/save")) {
      if (conflict) {
        conflict = false;
        current = {
          ...current,
          queue: { ...current.queue, batch_size: 200 },
        };
        version = "c".repeat(64);
        return json(
          route,
          {
            detail:
              "Runtime configuration changed after this preview. Reload the current settings and preview again.",
          },
          409,
        );
      }
      current = structuredClone(
        body.configuration as typeof RUNTIME_WORKSPACE.current,
      );
      version = "d".repeat(64);
      return json(route, {
        revision_id: "33333333-3333-4333-8333-333333333333",
        version,
        configuration: current,
        effects: RUNTIME_PREVIEW.effects,
        enqueued: Boolean(body.enqueue),
      });
    }
    return json(route, { detail: "Unexpected runtime request" }, 500);
  });
  await page.goto(RUNTIME);
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Scraper Runtime Configuration",
    }),
  ).toBeVisible();
  return calls;
}

async function editReviewedDraft(page: Page) {
  await page.getByRole("checkbox", { name: "MO", exact: true }).uncheck();
  await page.getByRole("checkbox", { name: "PA", exact: true }).check();
  const depth = page.getByRole("spinbutton", {
    name: "Depth",
    exact: true,
  });
  await depth.fill("5");
  await page.getByRole("spinbutton", { name: "Fallback depth" }).fill("80");
  const ohio = page.getByRole("group", { name: "OH" });
  await ohio.getByRole("spinbutton", { name: "Cell size (km)" }).fill("30");
  await ohio.getByRole("spinbutton", { name: "Zoom" }).fill("16");
}

test.describe("Scraper campaign history", () => {
  test("retains every row detail and terminal destination", async ({ page }) => {
    await openHistory(page);

    const row = page.getByRole("row", { name: /Acoustic Guitar Lessons/ });
    await expect(row).toContainText("OH");
    await expect(row).toContainText("240");
    await expect(row).toContainText("Jul 27, 13:41");
    await expect(row).toContainText("Jul 29, 00:00");
    await expect(row).toContainText("Jul 29, 2026");
    await expect(
      page.getByRole("link", { name: "Runtime configuration" }),
    ).toBeVisible();
  });

  test("combines search, state, sort and direction", async ({ page }) => {
    const calls = await openHistory(page);
    await page.getByRole("searchbox", { name: "Search keywords" }).fill("farm");
    await page.getByRole("combobox", { name: "State" }).selectOption("KY");
    await page
      .getByRole("combobox", { name: "Sort by" })
      .selectOption("cells_posted");
    await page
      .getByRole("combobox", { name: "Direction" })
      .selectOption("asc");

    await expect(
      page.getByRole("row", { name: /Farm Equipment Dealer/ }),
    ).toBeVisible();
    await expect(
      page.getByRole("row", { name: /Acoustic Guitar Lessons/ }),
    ).toHaveCount(0);
    const last = calls.at(-1)!;
    expect(last.searchParams.get("search")).toBe("farm");
    expect(last.searchParams.get("state")).toBe("KY");
    expect(last.searchParams.get("sort")).toBe("cells_posted");
    expect(last.searchParams.get("direction")).toBe("asc");
  });

  test("empty filters and failed queries remain recoverable", async ({ page }) => {
    await openHistory(page, { failureOnce: true });
    await page.getByRole("searchbox", { name: "Search keywords" }).fill("none");
    await expect(
      page.getByRole("heading", { name: "Campaign history unavailable" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Retry this query" }).click();
    await expect(
      page.getByRole("heading", { name: "No campaign history" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Clear filters" }).click();
    await expect(
      page.getByRole("row", { name: /Acoustic Guitar Lessons/ }),
    ).toBeVisible();
  });
});

test.describe("Scraper runtime configuration", () => {
  test("retains coverage, runtime, queue, and override controls", async ({
    page,
  }) => {
    await openRuntime(page);

    await expect(
      page.getByRole("checkbox", { name: "AZ", exact: true }),
    ).toBeChecked();
    await expect(
      page.getByRole("checkbox", { name: "TX", exact: true }),
    ).not.toBeChecked();
    await expect(page.getByRole("spinbutton", {
      name: "Depth",
      exact: true,
    })).toHaveValue(
      "3",
    );
    await expect(
      page.getByRole("spinbutton", { name: "Jobs per worker" }),
    ).toHaveValue("25");
    await expect(
      page.getByRole("group", { name: "OH" }).getByRole("spinbutton", {
        name: "Cell size (km)",
      }),
    ).toHaveValue("");
    await expect(page.getByText("Scraper runtime controls only.")).toBeVisible();
  });

  test("previews calculated effects before audited save and enqueue", async ({
    page,
  }) => {
    const calls = await openRuntime(page);
    await editReviewedDraft(page);
    const save = page.getByRole("button", {
      name: "Save reviewed runtime configuration",
    });
    await expect(save).toBeDisabled();
    await page
      .getByRole("button", { name: "Preview calculated effects" })
      .click();

    const preview = page.getByRole("region", {
      name: "Runtime change preview",
    });
    await expect(preview).toContainText("806");
    await expect(preview).toContainText("763");
    await expect(preview).toContainText("-43 cells");
    await expect(preview).toContainText("Added: PA");
    await expect(preview).toContainText("Removed: MO");
    await page
      .getByRole("textbox", { name: /Change reason/ })
      .fill("Tune the reviewed campaign");
    await page
      .getByRole("checkbox", { name: /Request enqueue after save/ })
      .check();
    await expect(save).toBeEnabled();
    await save.click();
    await expect(
      page.getByText(
        "Scraper runtime configuration saved and enqueue requested.",
      ),
    ).toBeVisible();

    const saveCall = calls.find((call) => call.path.endsWith("/save"))!;
    expect(saveCall.body).toMatchObject({
      enqueue: true,
      reason: "Tune the reviewed campaign",
      expected_version: RUNTIME_WORKSPACE.version,
      review_token: `review-${RUNTIME_WORKSPACE.version}`,
    });
  });

  test("an edit after preview invalidates the review receipt", async ({ page }) => {
    await openRuntime(page);
    await editReviewedDraft(page);
    await page
      .getByRole("button", { name: "Preview calculated effects" })
      .click();
    await expect(
      page.getByRole("region", { name: "Runtime change preview" }),
    ).toBeVisible();
    await page
      .getByRole("spinbutton", { name: "Depth", exact: true })
      .fill("6");

    await expect(
      page.getByRole("region", { name: "Runtime change preview" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("button", {
        name: "Save reviewed runtime configuration",
      }),
    ).toBeDisabled();
  });

  test("invalid bounds are explained before preview", async ({ page }) => {
    const calls = await openRuntime(page);
    await page
      .getByRole("spinbutton", { name: "Depth", exact: true })
      .fill("101");
    await page
      .getByRole("button", { name: "Preview calculated effects" })
      .click();

    await expect(page.getByText(/Depth must be between 1 and 100/)).toBeVisible();
    expect(calls.filter((call) => call.path.endsWith("/preview"))).toHaveLength(
      0,
    );
  });

  test("concurrent changes preserve the draft for re-preview", async ({
    page,
  }) => {
    await openRuntime(page, { conflictOnce: true });
    await editReviewedDraft(page);
    await page
      .getByRole("button", { name: "Preview calculated effects" })
      .click();
    await page
      .getByRole("textbox", { name: /Change reason/ })
      .fill("Tune campaign");
    await page
      .getByRole("button", {
        name: "Save reviewed runtime configuration",
      })
      .click();

    await expect(
      page.getByText(/Your draft is preserved; preview it again/),
    ).toBeVisible();
    await expect(page.getByRole("spinbutton", {
      name: "Depth",
      exact: true,
    })).toHaveValue(
      "5",
    );
    await expect(
      page.getByRole("button", {
        name: "Save reviewed runtime configuration",
      }),
    ).toBeDisabled();
  });

  test("an upstream preview failure can be retried without losing edits", async ({
    page,
  }) => {
    await openRuntime(page, { previewFailureOnce: true });
    await page
      .getByRole("spinbutton", { name: "Depth", exact: true })
      .fill("5");
    await page
      .getByRole("button", { name: "Preview calculated effects" })
      .click();
    await expect(
      page.getByRole("heading", { name: "Runtime action not completed" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Preview calculated effects" })
      .click();

    await expect(
      page.getByRole("region", { name: "Runtime change preview" }),
    ).toBeVisible();
    await expect(page.getByRole("spinbutton", {
      name: "Depth",
      exact: true,
    })).toHaveValue(
      "5",
    );
  });
});
