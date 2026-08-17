import { expect, test } from "@playwright/test";

import {
  ANOMALY_ID,
  EVIDENCE_CHECKSUM,
  RECOMMENDATION_ID,
  mockAcquisition,
} from "./acquisition-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import type { RecordedCall } from "./acquisition-fixtures";

/**
 * Acquisition review and optimization in a real browser (#68).
 *
 * Both Playwright projects run these, so every assertion holds at the Pixel 7
 * and desktop viewports.
 */

let calls: RecordedCall[];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  calls = await mockAcquisition(page);
});

test.describe("The Operations identity is consistent", () => {
  test("acquisition wears the Match operations frame", async ({ page }) => {
    await page.goto("./admin/acquisition");

    await expect(page.locator("html")).toHaveAttribute("data-theme", "match");
    await expect(
      page.getByRole("region", { name: "Acquisition workspace" }),
    ).toBeVisible();
    await expect(page.getByText("Operations", { exact: true })).toBeVisible();
  });

  test("the rail reaches Scraper Operations and back out", async ({ page }) => {
    await page.goto("./admin/acquisition");

    const rail = page.getByRole("navigation", {
      name: "Acquisition workspace sections",
    });
    await expect(
      rail.getByRole("link", { name: "Scraper Operations" }),
    ).toHaveAttribute("href", "/app/admin/acquisition/scraper");
    await expect(
      rail.getByRole("link", { name: "Exit to Overview" }),
    ).toBeVisible();
  });

  test("leaving acquisition preserves the admin Match theme", async ({ page }) => {
    await page.goto("./admin/acquisition");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "match");

    await page
      .getByRole("navigation", { name: "Administration" })
      .getByRole("link", { name: "Overview" })
      .click();

    await expect(page.locator("html")).toHaveAttribute("data-theme", "match");
  });
});

test.describe("Scrape Anomaly decisions", () => {
  test("the held anomaly shows what tripped before asking for a decision", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    const held = page.getByRole("region", { name: "Held Scrape Anomalies" });
    await expect(held.getByText(/roofing-austin-tx/)).toBeVisible();
    await expect(held.getByText(/more than 50 percent down/)).toBeVisible();
  });

  test("confirming states that it publishes, and posts a reason", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    await page
      .getByRole("region", { name: "Held Scrape Anomalies" })
      .getByRole("button", { name: "Confirm" })
      .click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toHaveAccessibleDescription(
      /Publishes the held staged dataset/,
    );
    await dialog
      .getByLabel("Reason (required)")
      .fill("Counts reflect a deliberate source change.");
    await dialog.getByRole("button", { name: "Confirm" }).click();

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/scrape-anomalies/${ANOMALY_ID}/confirm`);
    expect(calls.at(-1)?.body["reason"]).toBe(
      "Counts reflect a deliberate source change.",
    );
  });

  test("denying says the active dataset stays authoritative", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    await page
      .getByRole("region", { name: "Held Scrape Anomalies" })
      .getByRole("button", { name: "Deny" })
      .click();

    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(
      /last successful Scraper Dataset stays authoritative/,
    );
  });

  test("an empty reason is refused before anything is sent", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    await page
      .getByRole("region", { name: "Held Scrape Anomalies" })
      .getByRole("button", { name: "Confirm" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("button", { name: "Confirm" }).click();

    await expect(dialog.getByText("A reason is required.")).toBeVisible();
    expect(calls).toHaveLength(0);
  });
});

test.describe("Source Recommendations stay human-controlled", () => {
  test("evidence is readable before the decision is offered", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    const section = page.getByRole("region", {
      name: "Source Recommendations",
    });
    await section.locator("summary").click();
    await expect(
      section.getByText(/Worked 120 · rated 40 · eligibility eligible/),
    ).toBeVisible();
    await expect(
      section.getByText(/Compared against 3 same-niche peers/),
    ).toBeVisible();
  });

  test("the decision carries the evidence checksum it displayed", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    await page
      .getByRole("region", { name: "Source Recommendations" })
      .locator("summary")
      .click();
    await page
      .getByRole("region", { name: "Source Recommendations" })
      .getByRole("button", { name: "Approve" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Reason (required)")
      .fill("Peer evidence supports reducing this segment.");
    await dialog.getByRole("button", { name: "Approve" }).click();

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/source-recommendations/${RECOMMENDATION_ID}/approve`);
    expect(calls.at(-1)?.body["evidenceChecksum"]).toBe(EVIDENCE_CHECKSUM);
  });

  test("approval promises a scheduled version, not a behaviour change", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    await page
      .getByRole("region", { name: "Source Recommendations" })
      .locator("summary")
      .click();
    await page
      .getByRole("region", { name: "Source Recommendations" })
      .getByRole("button", { name: "Approve" })
      .click();

    await expect(page.getByRole("dialog")).toHaveAccessibleDescription(
      /no existing version is rewritten and no Scrape Run starts/,
    );
  });
});

test.describe("Immutable Scraper Configuration versions", () => {
  test("versions are listed newest first with no way to rewrite one", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    const section = page.getByRole("region", {
      name: "Scraper Configuration versions",
    });
    await section.locator("summary").click();
    await expect(
      section.getByRole("heading", { level: 3, name: "Version 5" }),
    ).toBeVisible();
    await expect(
      section.getByRole("heading", { level: 3, name: "Version 4" }),
    ).toBeVisible();
    await expect(
      section.getByRole("button", { name: /edit|rewrite|update/i }),
    ).toHaveCount(0);
  });

  test("a rolled-forward version says what it came from", async ({ page }) => {
    await page.goto("./admin/acquisition");

    await page
      .getByRole("region", { name: "Scraper Configuration versions" })
      .locator("summary")
      .click();
    await expect(
      page.getByText(/rolled forward from an earlier version/),
    ).toBeVisible();
  });
});

test.describe("Nightly Reviews", () => {
  test("an unknown Telegram delivery is named as needing reconciliation", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    const section = page.getByRole("region", { name: "Nightly Reviews" });
    await section.locator("summary").click();
    await expect(section.getByText(/Telegram delivery: unknown/)).toBeVisible();
    await expect(
      section.getByText(/needs reconciling before this review can be updated/),
    ).toBeVisible();
  });
});

test.describe("Compact review queues", () => {
  test("Niche mapping offers correction and denial without a long list", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    const mappings = page.getByRole("region", { name: "Niche mappings" });
    await expect(mappings.getByText("Proposal 1 of 1", { exact: true })).toBeVisible();
    await expect(mappings.getByRole("searchbox", { name: "Find a Niche mapping" }))
      .toBeVisible();
    await expect(mappings.getByRole("button", { name: "Review and confirm" }))
      .toBeVisible();
    await mappings.getByRole("button", { name: "Deny proposal" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Wrong taxonomy match.");
    await dialog.getByRole("button", { name: "Deny proposal" }).click();
    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain("/api/admin/source-niches/TX%3A%3Aroofing%20contractor/deny");
  });

  test("global exclusion impact has an explicit confirm and deny home", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    const exclusions = page.getByRole("region", { name: "Exclusion review" });
    await expect(exclusions).toContainText("Liberty Roofing");
    await expect(exclusions).toContainText("61 pool impact");
    await expect(exclusions.getByRole("button", { name: "Confirm globally" }))
      .toBeVisible();
    await expect(exclusions.getByRole("button", { name: "Deny global effect" }))
      .toBeVisible();
  });
});

test.describe("Mobile operation", () => {
  test("every area of work is present and operable at this viewport", async ({
    page,
  }) => {
    await page.goto("./admin/acquisition");

    for (const section of [
      "Held Scrape Anomalies",
      "Source Recommendations",
      "Niche mappings",
      "Scraper Configuration versions",
      "Nightly Reviews",
    ]) {
      await expect(page.getByRole("region", { name: section })).toBeVisible();
    }

    const confirm = page
      .getByRole("region", { name: "Held Scrape Anomalies" })
      .getByRole("button", { name: "Confirm" });
    const box = await confirm.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(44);

    await confirm.click();
    await expect(page.getByRole("dialog")).toBeVisible();
  });
});
