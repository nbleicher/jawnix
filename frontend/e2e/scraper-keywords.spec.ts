import { expect, test } from "@playwright/test";
import type { Page, Route } from "@playwright/test";

import { mockAdminMFA } from "./mfa-fixtures";
import {
  KEYWORD_GENERATION,
  KEYWORD_WORKSPACE,
} from "./scraper-keywords-fixtures";

const KEYWORDS = "./admin/acquisition/scraper/workspace/keywords";
const NEXT_VERSION = "b".repeat(64);

interface RolloverState {
  enabled: boolean;
  state: "off" | "working" | "draining" | "ready";
  label: string;
  detail: string;
  percent_complete: number;
  posted_jobs: number | null;
  expected_jobs: number | null;
  last_status: "generated" | "error" | null;
  last_event: string | null;
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function parse(text: string): string[] {
  const seen = new Set<string>();
  return text
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter((value) => {
      const key = value.toLocaleLowerCase();
      if (!value || value.startsWith("#") || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function diff(current: string[], text: string, version: string) {
  const proposed = parse(text);
  const currentKeys = new Set(current.map((value) => value.toLocaleLowerCase()));
  const proposedKeys = new Set(
    proposed.map((value) => value.toLocaleLowerCase()),
  );
  return {
    proposed,
    added: proposed.filter(
      (value) => !currentKeys.has(value.toLocaleLowerCase()),
    ),
    removed: current.filter(
      (value) => !proposedKeys.has(value.toLocaleLowerCase()),
    ),
    unchanged: proposed.filter((value) =>
      currentKeys.has(value.toLocaleLowerCase()),
    ),
    expected_version: version,
    review_token: `review-${version}`,
  };
}

interface OpenOptions {
  aiEnabled?: boolean;
  generationFailureOnce?: boolean;
  conflictOnce?: boolean;
}

async function openKeywords(page: Page, options: OpenOptions = {}) {
  await mockAdminMFA(page, { assurance: "aal2" });
  let current = [...KEYWORD_WORKSPACE.current];
  let version = KEYWORD_WORKSPACE.version;
  let rollover: RolloverState = {
    ...KEYWORD_WORKSPACE.rollover,
    state: "off",
    last_status: "generated",
  };
  let generationFailure = options.generationFailureOnce ?? false;
  let conflict = options.conflictOnce ?? false;
  const calls: { path: string; body: Record<string, unknown> }[] = [];

  await page.route(/\/api\/admin\/scraper\/keywords(?:\/.*)?$/, async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.method() === "GET"
      ? {}
      : (request.postDataJSON() as Record<string, unknown>);
    calls.push({ path, body });

    if (path.endsWith("/keywords") && request.method() === "GET") {
      return json(route, {
        ...KEYWORD_WORKSPACE,
        current,
        version,
        ai_enabled: options.aiEnabled ?? true,
        rollover,
      });
    }
    if (path.endsWith("/preview")) {
      const result = diff(current, String(body.text ?? ""), version);
      if (!result.proposed.length) {
        return json(route, { detail: "At least one keyword is required." }, 422);
      }
      return json(route, result);
    }
    if (path.endsWith("/save")) {
      if (conflict) {
        conflict = false;
        current = [...current, "hvac"];
        version = NEXT_VERSION;
        return json(
          route,
          {
            detail:
              "Active keywords changed after this preview. Reload the current list and preview again.",
          },
          409,
        );
      }
      const result = diff(current, String(body.text ?? ""), version);
      current = result.proposed;
      version = NEXT_VERSION;
      return json(route, {
        saved: true,
        enqueued: Boolean(body.enqueue),
        current,
        version,
        diff: result,
      });
    }
    if (path.endsWith("/generate")) {
      if (generationFailure) {
        generationFailure = false;
        return json(
          route,
          { detail: "DeepSeek is temporarily unavailable; try again" },
          503,
        );
      }
      const mode = body.mode === "adjacent" ? "adjacent" : "broad";
      return json(route, {
        ...KEYWORD_GENERATION,
        mode,
        seed_keyword:
          mode === "adjacent" ? String(body.seed_keyword) : null,
        keywords:
          mode === "adjacent"
            ? ["drain cleaning", "septic service", "water heater repair"]
            : KEYWORD_GENERATION.keywords,
      });
    }
    if (path.endsWith("/rollover")) {
      const enabled = body.action === "enable";
      rollover = {
        ...rollover,
        enabled,
        state: enabled ? "working" : "off",
        label: enabled ? "Current batch active" : "Off",
        detail: enabled
          ? "12 of 20 coverage jobs enqueued"
          : "Manual keyword batches",
        posted_jobs: enabled ? 12 : null,
        expected_jobs: enabled ? 20 : null,
      };
      return json(route, rollover);
    }
    return json(route, { detail: "Unexpected keyword request" }, 500);
  });

  await page.goto(KEYWORDS);
  await expect(
    page.getByRole("heading", { level: 1, name: "Scraper Keywords" }),
  ).toBeVisible();
  return calls;
}

async function openDisclosure(page: Page, name: string) {
  const details = page.getByRole("region", { name }).locator("details");
  if (!(await details.evaluate((node) => (node as HTMLDetailsElement).open))) {
    await details.locator("summary").click();
  }
  await expect(details).toHaveJSProperty("open", true);
}

test.describe("Keyword editor and import", () => {
  test("previews the exact diff before a direct save and preserves enqueue", async ({
    page,
  }) => {
    const calls = await openKeywords(page);
    await openDisclosure(page, "Keyword editor");
    const editor = page.getByRole("textbox", { name: /Keyword list/ });
    await editor.fill("Plumbers\nroofers\nROOFERS\n# skip");
    await page
      .getByRole("checkbox", { name: /Request enqueue after save/ })
      .check();

    await page.getByRole("button", { name: "Preview changes" }).click();

    const preview = page.getByRole("region", {
      name: "Keyword change preview",
    });
    await expect(preview.getByRole("list", { name: "Keywords added" }))
      .toContainText("roofers");
    await expect(preview.getByRole("list", { name: "Keywords removed" }))
      .toContainText("electricians");
    await page.getByRole("button", { name: "Save reviewed list" }).click();

    await expect(page.getByText("Saved 2 keywords; enqueue requested."))
      .toBeVisible();
    const save = calls.find((call) => call.path.endsWith("/save"));
    expect(save?.body).toMatchObject({
      text: "Plumbers\nroofers\nROOFERS\n# skip",
      enqueue: true,
      expected_version: KEYWORD_WORKSPACE.version,
      review_token: `review-${KEYWORD_WORKSPACE.version}`,
    });
  });

  test("imports a supported text file into review without saving", async ({
    page,
  }) => {
    const calls = await openKeywords(page);
    await openDisclosure(page, "Keyword editor");
    await page.getByLabel("Import a text file").setInputFiles({
      name: "campaign.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("hvac\nHVAC\n# note\n"),
    });

    await expect(page.getByText(/Imported campaign.txt into the editor/))
      .toBeVisible();
    await expect(page.getByRole("button", { name: "Save reviewed list" }))
      .toBeDisabled();
    expect(calls.filter((call) => call.path.endsWith("/save"))).toEqual([]);

    await page.getByRole("button", { name: "Preview changes" }).click();
    await expect(
      page
        .getByRole("region", { name: "Keyword change preview" })
        .getByRole("list", { name: "Keywords added" }),
    ).toContainText("hvac");
  });

  test("invalid input stays editable and cannot be saved", async ({ page }) => {
    const calls = await openKeywords(page);
    await openDisclosure(page, "Keyword editor");
    await page.getByRole("textbox", { name: /Keyword list/ }).fill("# only");

    await page.getByRole("button", { name: "Preview changes" }).click();

    await expect(page.getByRole("alert")).toContainText(
      "At least one keyword is required.",
    );
    await expect(page.getByRole("button", { name: "Save reviewed list" }))
      .toBeDisabled();
    expect(calls.filter((call) => call.path.endsWith("/save"))).toEqual([]);
  });
});

test.describe("AI drafts and rankings", () => {
  test("ranks Customer outcomes by positives per delivered and marks prescriptions dormant", async ({
    page,
  }) => {
    await openKeywords(page);

    const analytics = page.getByRole("region", {
      name: "Keyword outcome analytics",
    });
    await expect(analytics).toContainText("12.0%");
    await expect(analytics).toContainText("No deliveries");
    await expect(analytics).toContainText("Worked-Leads prescription is dormant");
  });

  test("broad generation cannot save before human review", async ({ page }) => {
    const calls = await openKeywords(page);
    await openDisclosure(page, "Keyword editor");

    await page
      .getByRole("button", { name: "Generate 25 broad keywords" })
      .click();

    await expect(page.getByText(/Nothing has been saved or enqueued/))
      .toBeVisible();
    await expect
      .poll(() =>
        page.getByRole("textbox", { name: /Keyword list/ }).inputValue(),
      )
      .toContain("Unused Service 25");
    await expect(page.getByRole("button", { name: "Save reviewed list" }))
      .toBeDisabled();
    expect(calls.filter((call) => call.path.endsWith("/save"))).toEqual([]);

    await page.getByRole("button", { name: "Preview changes" }).click();
    await page.getByRole("button", { name: "Save reviewed list" }).click();
    const save = calls.find((call) => call.path.endsWith("/save"));
    expect(save?.body.generation_id).toBe(KEYWORD_GENERATION.generation_id);
  });

  test("winner metrics and adjacent action keep their current meaning", async ({
    page,
  }) => {
    const calls = await openKeywords(page);
    await openDisclosure(page, "Scraper yield reference");
    const rankings = page.getByRole("region", { name: "Scraper yield reference" });

    await expect(rankings).toContainText("2,480");
    await expect(rankings).toContainText("4,000");
    await expect(rankings).toContainText("1,000");
    await expect(rankings).toContainText("2.48");
    await expect(rankings).toContainText("62.0%");
    await rankings
      .getByRole("button", { name: "Generate adjacent" })
      .first()
      .click();

    await expect(page.getByText(/keywords adjacent to plumbers/)).toBeVisible();
    const generation = calls.find((call) => call.path.endsWith("/generate"));
    expect(generation?.body).toEqual({
      mode: "adjacent",
      seed_keyword: "plumbers",
    });
    expect(calls.filter((call) => call.path.endsWith("/save"))).toEqual([]);
  });

  test("AI failure is visible, recoverable, and leaves the active list alone", async ({
    page,
  }) => {
    const calls = await openKeywords(page, { generationFailureOnce: true });
    await openDisclosure(page, "Keyword editor");

    await page
      .getByRole("button", { name: "Generate 25 broad keywords" })
      .click();
    await expect(page.getByRole("alert")).toContainText(
      "DeepSeek is temporarily unavailable",
    );
    await expect(page.getByRole("textbox", { name: /Keyword list/ }))
      .toHaveValue("plumbers\nelectricians");

    await page
      .getByRole("button", { name: "Generate 25 broad keywords" })
      .click();
    await expect(page.getByText(/25 broad local-business keywords/)).toBeVisible();
    expect(calls.filter((call) => call.path.endsWith("/save"))).toEqual([]);
  });

  test("AI-unavailable mode retains manual editing and import", async ({
    page,
  }) => {
    await openKeywords(page, { aiEnabled: false });
    await openDisclosure(page, "Keyword editor");

    await expect(page.getByText(/AI generation is unavailable/)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Generate 25 broad keywords" }),
    ).toBeDisabled();
    await expect(page.getByRole("button", { name: "Preview changes" }))
      .toBeEnabled();
    await expect(page.getByLabel("Import a text file")).toBeEnabled();
  });
});

test.describe("Rollover and concurrent changes", () => {
  test("retains automatic rollover metrics and controls", async ({ page }) => {
    const calls = await openKeywords(page);
    await openDisclosure(page, "Automatic rollover");
    await expect(
      page.getByRole("progressbar", { name: "Current keyword coverage" }),
    ).toHaveJSProperty("value", 60);

    await page
      .getByRole("button", { name: "Enable automatic rollover" })
      .click();

    await expect(page.getByText("12 / 20")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Disable automatic rollover" }),
    ).toBeVisible();
    const write = calls.find((call) => call.path.endsWith("/rollover"));
    expect(write?.body).toEqual({ action: "enable" });
  });

  test("recovers from a concurrent save without losing the operator draft", async ({
    page,
  }) => {
    const calls = await openKeywords(page, { conflictOnce: true });
    await openDisclosure(page, "Keyword editor");
    const editor = page.getByRole("textbox", { name: /Keyword list/ });
    await editor.fill("Plumbers\nroofers");
    await page.getByRole("button", { name: "Preview changes" }).click();
    await page.getByRole("button", { name: "Save reviewed list" }).click();

    await expect(page.getByRole("alert")).toContainText(
      "Active keywords changed after this preview",
    );
    await page
      .getByRole("button", { name: "Reload current keywords" })
      .click();
    await expect(page.getByText(/Your draft is still in the editor/))
      .toBeVisible();
    await expect(editor).toHaveValue("Plumbers\nroofers");

    await page.getByRole("button", { name: "Preview changes" }).click();
    await page.getByRole("button", { name: "Save reviewed list" }).click();
    await expect(page.getByText("Saved 2 keywords.")).toBeVisible();
    expect(calls.filter((call) => call.path.endsWith("/save"))).toHaveLength(2);
  });

  test("keeps all metrics and actions reachable on mobile", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await openKeywords(page);
    await openDisclosure(page, "Keyword editor");
    await openDisclosure(page, "Automatic rollover");
    await openDisclosure(page, "Scraper yield reference");

    await expect(page.getByLabel("Import a text file")).toBeVisible();
    await expect(page.getByRole("button", { name: "Preview changes" }))
      .toBeVisible();
    await expect(
      page.getByRole("button", { name: "Enable automatic rollover" }),
    ).toBeVisible();
    const adjacent = page.getByRole("button", { name: "Generate adjacent" });
    await expect(adjacent).toHaveCount(2);
    await expect(adjacent.last()).toBeVisible();
    await expect(
      page.locator('td[data-label="Phones / cell"]').first(),
    ).toBeVisible();
  });
});
