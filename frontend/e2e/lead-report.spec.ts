import { expect, test } from "@playwright/test";

import { mockFulfillment } from "./fulfillment-fixtures";
import {
  HOLD_RELEASE,
  OPEN_REPORT_ID,
  RESOLVED_REPORT_ID,
  mockLeadReports,
} from "./lead-report-fixtures";
import { mockAdminMFA } from "./mfa-fixtures";
import type { RecordedCall } from "./lead-report-fixtures";

/**
 * Lead Report eligibility controls in a real browser (#58).
 *
 * Both Playwright projects run these, so the report stays readable and every
 * decision stays operable at the Pixel 7 viewport as well as the desktop one.
 * Reports are reached from Today's Decisions (today.spec.ts); suppressed Leads
 * are restored from Inventory (inventory.spec.ts).
 */

let calls: RecordedCall[];

test.beforeEach(async ({ page }) => {
  await mockAdminMFA(page, { assurance: "aal2" });
  await mockFulfillment(page);
  calls = await mockLeadReports(page);
});

test.describe("The report is evidence", () => {
  test("the Customer's words are readable and not editable", async ({ page }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    const report = page.getByRole("region", { name: "Report" });
    await expect(
      report.getByText(/rings a dental office/),
    ).toBeVisible();
    // The report is what the Customer said. Nothing on this screen retypes it.
    await expect(report.getByRole("textbox")).toHaveCount(0);
  });

  test("the delivered Distribution Event says it is never rewritten", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    const delivered = page.getByRole("region", { name: "What was delivered" });
    await expect(delivered.getByText("+15125550123")).toBeVisible();
    await expect(
      delivered.getByText(/never rewrites this Distribution Event/),
    ).toBeVisible();
  });

  test("the decision comes first and the batch it came from is linked", async ({ page }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    const decisionBox = await page.getByRole("region", { name: "Decision" }).boundingBox();
    const reportBox = await page.getByRole("region", { name: "Report" }).boundingBox();
    expect(decisionBox!.y).toBeLessThan(reportBox!.y);
    await expect(
      page.getByRole("region", { name: "What was delivered" }).getByRole("link", { name: "Batch #1031" }),
    ).toHaveAttribute("href", "/app/admin/batches/1031");
  });
});

test.describe("The Eligibility Hold rule is visible", () => {
  test("states the release rule verbatim and that the Customer cannot bypass it", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    const controls = page.getByRole("region", { name: "Current controls" });
    await expect(controls.getByText(HOLD_RELEASE)).toBeVisible();
    await expect(
      controls.getByText(/A Customer cannot release or bypass this Eligibility Hold/),
    ).toBeVisible();
    await expect(controls.getByText("Held")).toBeVisible();
  });
});

test.describe("Three decisions stay three decisions", () => {
  test("each decision has its own button and its own consequence", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);
    const decision = page.getByRole("region", { name: "Decision" });

    const expected: Array<[string, RegExp]> = [
      ["Dismiss", /returns the Lead to the eligible pool unchanged/],
      ["Correct", /overrides the listing this Lead came from/],
      ["Suppress", /Withholds this Lead from every future Batch Request/],
    ];

    for (const [label, consequence] of expected) {
      await decision.getByRole("button", { name: label }).click();
      const dialog = page.getByRole("dialog");
      await expect(dialog).toHaveAccessibleName(label);
      await expect(dialog).toHaveAccessibleDescription(consequence);
      await dialog.getByRole("button", { name: "Cancel" }).click();
      await expect(dialog).toBeHidden();
    }
  });

  test("a dismissal records the operator's note against the report", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    await page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Dismiss" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Number verified.");
    await dialog.getByRole("button", { name: "Dismiss" }).click();

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/lead-reports/${OPEN_REPORT_ID}/dismiss`);
    expect(calls.at(-1)?.body).toEqual({ note: "Number verified." });
  });
});

test.describe("A Lead Correction sits beside what it overrides", () => {
  test("the evidence is on screen while the override is typed", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    await page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Correct" })
      .click();
    const dialog = page.getByRole("dialog");

    await expect(
      dialog.getByRole("heading", { name: "What this overrides" }),
    ).toBeVisible();
    await expect(dialog.getByText("Ridgeline Roofing")).toBeVisible();

    // Nothing is pre-filled: the override has to be typed, not accepted.
    const title = dialog.getByLabel("Corrected title");
    const state = dialog.getByLabel("Corrected state");
    await expect(title).toHaveValue("");
    await expect(state).toHaveValue("");

    // Both are legible at once — the operator reads the evidence while typing.
    await title.fill("Ridgeline Dental");
    await expect(dialog.getByText("Ridgeline Roofing")).toBeVisible();
    await expect(title).toHaveValue("Ridgeline Dental");
  });

  test("only the typed override is sent", async ({ page }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    await page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Correct" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Corrected title").fill("Ridgeline Dental");
    await dialog.getByLabel("Reason (required)").fill("Listing moved.");
    await dialog.getByRole("button", { name: "Correct" }).click();

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/lead-reports/${OPEN_REPORT_ID}/correct`);
    expect(calls.at(-1)?.body).toEqual({
      note: "Listing moved.",
      title: "Ridgeline Dental",
    });
  });

  test("a correction that overrides nothing is refused before it is sent", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    await page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Correct" })
      .click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason (required)").fill("Listing is stale.");
    await dialog.getByRole("button", { name: "Correct" }).click();

    await expect(
      dialog.getByText("Enter a corrected title, a corrected state, or both."),
    ).toBeVisible();
    expect(calls).toHaveLength(0);
  });
});

test.describe("A decided Lead Report", () => {
  test("shows its resolution and offers no second decision", async ({ page }) => {
    await page.goto(`./admin/fulfillment/reports/${RESOLVED_REPORT_ID}`);

    const decision = page.getByRole("region", { name: "Decision" });
    await expect(
      decision.getByText("Number verified as reachable."),
    ).toBeVisible();
    await expect(
      decision.getByText("This Lead Report is closed and cannot be decided again."),
    ).toBeVisible();
    for (const label of ["Dismiss", "Correct", "Suppress"]) {
      await expect(decision.getByRole("button", { name: label })).toHaveCount(0);
    }
  });
});

test.describe("Keyboard operation", () => {
  test("a decision can be taken without a pointer", async ({ page }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    const suppress = page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Suppress" });
    await suppress.focus();
    await expect(suppress).toBeFocused();
    await page.keyboard.press("Enter");

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await dialog.getByLabel("Reason (required)").focus();
    await page.keyboard.type("Two Customers reported it.");
    await dialog.getByRole("button", { name: "Suppress" }).focus();
    await page.keyboard.press("Enter");

    await expect
      .poll(() => calls.map((call) => call.url))
      .toContain(`/api/admin/lead-reports/${OPEN_REPORT_ID}/suppress`);
  });

  test("Escape abandons a decision and returns focus to its button", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    const correct = page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Correct" });
    await correct.click();
    await expect(page.getByRole("dialog")).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toBeHidden();
    await expect(correct).toBeFocused();
    expect(calls).toHaveLength(0);
  });
});

test.describe("Mobile operation", () => {
  test("the whole report is readable and operable at the current viewport", async ({
    page,
  }) => {
    await page.goto(`./admin/fulfillment/reports/${OPEN_REPORT_ID}`);

    for (const section of [
      "Report",
      "What was delivered",
      "Evidence",
      "Current controls",
      "Decision",
    ]) {
      await expect(page.getByRole("region", { name: section })).toBeVisible();
    }

    const dismiss = page
      .getByRole("region", { name: "Decision" })
      .getByRole("button", { name: "Dismiss" });
    const box = await dismiss.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.height).toBeGreaterThanOrEqual(44);
  });
});
