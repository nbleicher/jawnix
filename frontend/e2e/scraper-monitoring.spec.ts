import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import { mockAdminMFA } from "./mfa-fixtures";
import {
  monitoringRegion,
  monitoringSnapshot,
  pausedPipelineResult,
} from "./scraper-monitoring-fixtures";
import type { RegionKey } from "./scraper-monitoring-fixtures";

const WORKSPACE = "./admin/acquisition/scraper/workspace";

interface OverrideOptions {
  snapshot?: unknown;
  pipeline?: unknown;
  pipelineStatus?: number;
  failingRegions?: RegionKey[];
}

async function openWorkspace(page: Page, options: OverrideOptions = {}) {
  await mockAdminMFA(page, { assurance: "aal2" });
  const writes: unknown[] = [];

  // Registered after mockAdminMFA so these win for the paths they claim.
  await page.route(/\/api\/admin\/scraper\/monitoring$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(options.snapshot ?? monitoringSnapshot()),
    }),
  );
  await page.route(/\/api\/admin\/scraper\/monitoring\/[^/]+$/, (route) => {
    const region = new URL(route.request().url()).pathname.split("/").pop() as RegionKey;
    const failed = (options.failingRegions ?? []).includes(region);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(
        failed
          ? monitoringRegion(region, { state: "unavailable", data: null })
          : monitoringRegion(region),
      ),
    });
  });
  await page.route(/\/api\/admin\/scraper\/pipeline$/, (route) => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({
      status: options.pipelineStatus ?? 200,
      contentType: "application/json",
      body: JSON.stringify(options.pipeline ?? pausedPipelineResult()),
    });
  });

  await page.goto(WORKSPACE);
  await expect(
    page.getByRole("heading", { level: 1, name: "Scraper Operations" }),
  ).toBeVisible();
  return writes;
}

test.describe("Scraper monitoring", () => {
  test("keeps every dashboard region and its cadence", async ({ page }) => {
    await openWorkspace(page);

    await expect(page.getByRole("region", { name: "Overall status" })).toBeVisible();
    for (const name of [
      "Host and stack",
      "Pipeline activity",
      "Headline totals",
      "Database activity",
      "Performance trends",
      "Workers",
      "Pipeline alerts",
      "Top states",
    ]) {
      await expect(page.getByRole("group", { name })).toBeVisible();
      await page.getByRole("group", { name }).getByRole("heading", { name }).click();
    }

    await expect(page.getByRole("region", { name: "Overall status" })).toContainText(
      "Attention needed",
    );
    await expect(page.getByRole("group", { name: "Headline totals" })).toContainText(
      "9,244,326",
    );
    await expect(page.getByRole("group", { name: "Workers" })).toContainText(
      "gms-worker-1",
    );
    await expect(page.getByRole("group", { name: "Database activity" })).toContainText(
      "2s",
    );
  });

  test("keeps the terminal identity and never exposes upstream internals", async ({
    page,
  }) => {
    await openWorkspace(page);

    await expect(page.getByText("GMS / OPS")).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "opaline");
    await expect(page.locator("body")).not.toContainText("river_job");
    await expect(page.locator("body")).not.toContainText("10.77.0.2");
  });

  test("shows the whole overview without horizontal scrolling", async ({ page }) => {
    await openWorkspace(page);

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
  });

  test("a dark region keeps its last reading and leaves the rest alone", async ({
    page,
  }) => {
    await openWorkspace(page, {
      snapshot: monitoringSnapshot({}, ["workers"]),
    });

    const workers = page.getByRole("group", { name: "Workers" });
    await workers.getByRole("heading", { name: "Workers" }).click();
    await expect(workers).toContainText("Not refreshing");
    await expect(workers).toContainText("gms-worker-1");
    await expect(page.getByRole("group", { name: "Top states" })).not.toContainText(
      "Not refreshing",
    );
  });

  test("a full outage fails closed with the last successful connection", async ({
    page,
  }) => {
    await openWorkspace(page, {
      snapshot: monitoringSnapshot({
        service_state: "unavailable",
        last_successful_at: "2026-07-28T11:00:00Z",
      }),
    });

    await expect(
      page.getByRole("heading", { name: "Scraper Operations unavailable" }),
    ).toBeVisible();
    await expect(page.getByRole("alert")).toContainText("Last successful connection");
    await expect(page.getByRole("button", { name: /Pause/ })).toHaveCount(0);
    await expect(
      page.getByRole("link", { name: "Back to Acquisition" }),
    ).toBeVisible();
  });
});

async function openPipelineControls(page: Page, options: OverrideOptions = {}) {
  const writes = await openWorkspace(page, options);
  // PipelineControls lives inside the "Pipeline activity" disclosure, which
  // ships collapsed. Only click it open when it is actually closed, so a
  // future default-open Panel does not get toggled shut here.
  const panel = page.getByRole("group", { name: "Pipeline activity" });
  const open = await panel.evaluate(
    (element) => (element as HTMLDetailsElement).open,
  );
  if (!open) {
    await panel.getByRole("heading", { name: "Pipeline activity" }).click();
  }
  await expect(panel).toHaveJSProperty("open", true);
  return writes;
}

test.describe("Pipeline controls", () => {
  test("will not change the pipeline without a recorded reason", async ({ page }) => {
    const writes = await openPipelineControls(page);

    await page.getByRole("button", { name: "Pause, keep queue" }).click();

    await expect(page.getByRole("alert")).toContainText(
      "Record why you are changing the pipeline",
    );
    expect(writes).toEqual([]);
  });

  test("pauses while keeping the queue", async ({ page }) => {
    const writes = await openPipelineControls(page);

    await page.getByRole("textbox", { name: /Reason/ }).fill("Source quality");
    await page.getByRole("button", { name: "Pause, keep queue" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Work already queued is kept");
    await dialog.getByRole("button", { name: "Pause, keep queue" }).click();

    await expect(page.getByText("Pipeline paused; the queue was kept.")).toBeVisible();
    expect(writes).toEqual([
      { action: "pause", clear_queue: false, reason: "Source quality" },
    ]);
  });

  test("makes clearing the queue a separate, warned choice", async ({ page }) => {
    const writes = await openPipelineControls(page, {
      pipeline: pausedPipelineResult(812),
    });

    await page.getByRole("textbox", { name: /Reason/ }).fill("Bad keyword set");
    await page.getByRole("button", { name: "Pause and clear queue" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("cannot be restored");
    await dialog.getByRole("button", { name: "Pause and clear queue" }).click();

    await expect(page.getByText(/812 queued jobs cancelled/)).toBeVisible();
    expect(writes).toEqual([
      { action: "pause", clear_queue: true, reason: "Bad keyword set" },
    ]);
  });

  test("offers resume only once the pipeline is paused", async ({ page }) => {
    const paused = monitoringSnapshot();
    const regions = (paused as { regions: { region: string; data: Record<string, unknown> }[] })
      .regions;
    const activity = regions.find((entry) => entry.region === "activity");
    if (activity) {
      activity.data = {
        ...activity.data,
        pipeline_state: {
          key: "paused",
          label: "Paused",
          detail: "No new scrape jobs will be queued",
        },
      };
    }
    const writes = await openPipelineControls(page, {
      snapshot: paused,
      pipeline: {
        ok: true,
        pipeline_state: "running",
        cancelled_jobs: 0,
        region: monitoringRegion("activity"),
      },
    });

    await expect(page.getByRole("button", { name: "Pause, keep queue" })).toHaveCount(0);
    await page.getByRole("textbox", { name: /Reason/ }).fill("Inventory recovered");
    await page.getByRole("button", { name: "Resume pipeline" }).click();

    await expect(page.getByText("Pipeline resumed.")).toBeVisible();
    expect(writes).toEqual([{ action: "resume", reason: "Inventory recovered" }]);
  });
});
