import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import { ScraperKeywordsRoute } from "./ScraperKeywords";
import type { KeywordWorkspace } from "./scraperKeywordApi";

const VERSION = "a".repeat(64);
const NEXT_VERSION = "b".repeat(64);

function workspace(
  overrides: Partial<KeywordWorkspace> = {},
): KeywordWorkspace {
  return {
    service_state: "connected",
    last_successful_at: "2026-07-29T12:00:00Z",
    current: ["plumbers", "electricians"],
    version: VERSION,
    ai_enabled: true,
    idle_expires_in: 900,
    rollover: {
      enabled: false,
      state: "off",
      label: "Off",
      detail: "Manual keyword batches",
      percent_complete: 60,
      posted_jobs: null,
      expected_jobs: null,
      last_status: "generated",
      last_event: "Jul 28 · 12:00 UTC",
    },
    winners: [
      {
        rank: 1,
        keyword: "plumbers",
        phone_businesses: 2480,
        businesses: 4000,
        posted_cells: 1000,
        phones_per_cell: 2.48,
        phone_rate: 0.62,
        last_used: "Jul 28",
      },
      {
        rank: 2,
        keyword: "roof repair",
        phone_businesses: 1200,
        businesses: 2000,
        posted_cells: 600,
        phones_per_cell: 2,
        phone_rate: 0.6,
        last_used: "Jul 27",
      },
    ],
    performance: [
      {
        keyword: "plumbers",
        delivered: 100,
        positive: 12,
        positives_per_delivered: 0.12,
      },
      {
        keyword: "electricians",
        delivered: 0,
        positive: 0,
        positives_per_delivered: null,
      },
    ],
    prescriptive_mode: "dormant_worked_leads",
    ...overrides,
  };
}

const DIFF = {
  proposed: ["Plumbers", "roofers"],
  added: ["roofers"],
  removed: ["electricians"],
  unchanged: ["Plumbers"],
  expected_version: VERSION,
  review_token: "signed-keyword-review",
};

function renderKeywords(data: KeywordWorkspace = workspace()) {
  const router = createMemoryRouter(
    [
      {
        id: "keywords",
        path: "/admin/acquisition/scraper/workspace/keywords",
        loader: () => data,
        element: <ScraperKeywordsRoute />,
      },
    ],
    {
      initialEntries: ["/admin/acquisition/scraper/workspace/keywords"],
      hydrationData: { loaderData: { keywords: data } },
    },
  );
  const view = render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
  view.container.querySelectorAll("details").forEach((item) => {
    item.open = true;
  });
  return view;
}

interface PlannedResponse {
  path: string;
  body: unknown;
  status?: number;
}

function mockRequests(plans: PlannedResponse[]) {
  const calls: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const plan = plans.shift();
      if (!plan) throw new Error(`Unexpected request: ${input}`);
      expect(input).toBe(plan.path);
      calls.push({
        path: input,
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      const status = plan.status ?? 200;
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(plan.body),
      } as Response);
    }),
  );
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("keyword parity", () => {
  it("keeps the workspace frame available when the private service is offline", () => {
    renderKeywords(workspace({
      service_state: "unavailable",
      last_successful_at: "2026-07-28T11:00:00Z",
      current: [],
      winners: [],
    }));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Scraper keywords unavailable",
    );
    expect(screen.queryByRole("textbox", { name: /Keyword list/ }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Keywords" }))
      .toHaveAttribute("aria-current", "page");
  });

  it("renders the active editor, rollover metrics and every winner metric", () => {
    renderKeywords();

    expect(screen.getByRole("textbox", { name: /Keyword list/ })).toHaveValue(
      "plumbers\nelectricians",
    );
    expect(screen.getByRole("region", { name: "Automatic keyword rollover" }))
      .toHaveTextContent("Manual keyword batches");
    expect(screen.getByRole("progressbar", { name: "Current keyword coverage" }))
      .toHaveValue(60);
    const analytics = screen.getByRole("region", { name: "Keyword outcome analytics" });
    expect(analytics).toHaveTextContent("100");
    expect(analytics).toHaveTextContent("12.0%");
    expect(analytics).toHaveTextContent("No deliveries");
    const rankings = screen.getByRole("region", { name: "Scraper yield reference" });
    expect(rankings).toHaveTextContent("2,480");
    expect(rankings).toHaveTextContent("4,000");
    expect(rankings).toHaveTextContent("1,000");
    expect(rankings).toHaveTextContent("2.48");
    expect(rankings).toHaveTextContent("62.0%");
    expect(screen.getByRole("button", { name: "Save reviewed list" })).toBeDisabled();
  });

  it("requires an exact preview before save and preserves enqueue", async () => {
    const user = userEvent.setup();
    const calls = mockRequests([
      {
        path: "/api/admin/scraper/keywords/preview",
        body: DIFF,
      },
      {
        path: "/api/admin/scraper/keywords/save",
        body: {
          saved: true,
          enqueued: true,
          current: DIFF.proposed,
          version: NEXT_VERSION,
          diff: DIFF,
        },
      },
    ]);
    renderKeywords();
    const editor = screen.getByRole("textbox", { name: /Keyword list/ });
    await user.clear(editor);
    await user.type(editor, "Plumbers\nroofers");
    await user.click(
      screen.getByRole("checkbox", { name: /Request enqueue after save/ }),
    );

    await user.click(screen.getByRole("button", { name: "Preview changes" }));

    const preview = await screen.findByRole("region", {
      name: "Keyword change preview",
    });
    expect(within(preview).getByRole("list", { name: "Keywords added" }))
      .toHaveTextContent("roofers");
    expect(within(preview).getByRole("list", { name: "Keywords removed" }))
      .toHaveTextContent("electricians");
    expect(screen.getByRole("button", { name: "Save reviewed list" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Save reviewed list" }));

    await screen.findByText("Saved 2 keywords; enqueue requested.");
    expect(calls[1]?.body).toEqual({
      text: "Plumbers\nroofers",
      expected_version: VERSION,
      review_token: "signed-keyword-review",
      enqueue: true,
    });
  });

  it("invalid input is recoverable and never enables save", async () => {
    const user = userEvent.setup();
    mockRequests([
      {
        path: "/api/admin/scraper/keywords/preview",
        status: 422,
        body: { detail: "At least one keyword is required." },
      },
      {
        path: "/api/admin/scraper/keywords",
        body: workspace(),
      },
    ]);
    renderKeywords();
    await user.clear(screen.getByRole("textbox", { name: /Keyword list/ }));

    await user.click(screen.getByRole("button", { name: "Preview changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "At least one keyword is required.",
    );
    expect(screen.getByRole("button", { name: "Save reviewed list" })).toBeDisabled();
  });

  it("loads a broad AI result as a review-only editor draft", async () => {
    const user = userEvent.setup();
    const generated = {
      generation_id: "33333333-3333-4333-8333-333333333333",
      mode: "broad",
      seed_keyword: null,
      keywords: Array.from(
        { length: 25 },
        (_, index) => `Unused Service ${index + 1}`,
      ),
      excluded_count: 7,
      notice: "nothing has been saved or enqueued",
    };
    const calls = mockRequests([
      {
        path: "/api/admin/scraper/keywords/generate",
        body: generated,
      },
    ]);
    renderKeywords();

    await user.click(
      screen.getByRole("button", { name: "Generate 25 broad keywords" }),
    );

    expect(await screen.findByText(/Nothing has been saved or enqueued/))
      .toBeVisible();
    expect(
      (screen.getByRole("textbox", {
        name: /Keyword list/,
      }) as HTMLTextAreaElement).value,
    ).toContain("Unused Service 25");
    expect(screen.getByRole("button", { name: "Save reviewed list" })).toBeDisabled();
    expect(calls).toHaveLength(1);
  });

  it("generates adjacent keywords only from the selected winner", async () => {
    const user = userEvent.setup();
    const generated = {
      generation_id: "33333333-3333-4333-8333-333333333333",
      mode: "adjacent",
      seed_keyword: "plumbers",
      keywords: ["drain cleaning", "septic service"],
      excluded_count: 3,
      notice: "nothing has been saved or enqueued",
    };
    const calls = mockRequests([
      {
        path: "/api/admin/scraper/keywords/generate",
        body: generated,
      },
    ]);
    renderKeywords();

    await user.click(
      screen.getAllByRole("button", { name: "Generate adjacent" })[0]!,
    );

    await screen.findByText(/keywords adjacent to plumbers/);
    expect(calls[0]?.body).toEqual({
      mode: "adjacent",
      seed_keyword: "plumbers",
    });
  });

  it("imports supported text into the draft without saving", async () => {
    const user = userEvent.setup();
    renderKeywords();
    const file = new File(["ignored"], "keywords.txt", {
      type: "text/plain",
    });
    Object.defineProperty(file, "text", {
      value: () => Promise.resolve("hvac\nHVAC\n# note\n"),
    });

    await user.upload(
      screen.getByLabelText("Import a text file"),
      file,
    );

    expect(
      await screen.findByText(/Imported keywords.txt into the editor/),
    ).toBeVisible();
    expect(screen.getByRole("textbox", { name: /Keyword list/ }))
      .toHaveValue("hvac\nHVAC\n# note\n");
    expect(screen.getByRole("button", { name: "Save reviewed list" })).toBeDisabled();
  });

  it("retains rollover controls and updates their measured state", async () => {
    const user = userEvent.setup();
    const calls = mockRequests([
      {
        path: "/api/admin/scraper/keywords/rollover",
        body: {
          ...workspace().rollover,
          enabled: true,
          state: "working",
          label: "Current batch active",
          detail: "12 of 20 coverage jobs enqueued",
          posted_jobs: 12,
          expected_jobs: 20,
        },
      },
    ]);
    renderKeywords();

    await user.click(
      screen.getByRole("button", { name: "Enable automatic rollover" }),
    );

    expect(await screen.findByText("12 / 20")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Disable automatic rollover" }),
    ).toBeVisible();
    expect(calls[0]?.body).toEqual({ action: "enable" });
  });

  it("keeps a draft after a concurrent change and supports re-preview", async () => {
    const user = userEvent.setup();
    mockRequests([
      {
        path: "/api/admin/scraper/keywords/preview",
        body: DIFF,
      },
      {
        path: "/api/admin/scraper/keywords/save",
        status: 409,
        body: {
          detail:
            "Active keywords changed after this preview. Reload the current list and preview again.",
        },
      },
      {
        path: "/api/admin/scraper/keywords",
        body: workspace({
          current: ["plumbers", "electricians", "hvac"],
          version: NEXT_VERSION,
        }),
      },
    ]);
    renderKeywords();
    const editor = screen.getByRole("textbox", { name: /Keyword list/ });
    await user.clear(editor);
    await user.type(editor, "Plumbers\nroofers");
    await user.click(screen.getByRole("button", { name: "Preview changes" }));
    await user.click(await screen.findByRole("button", { name: "Save reviewed list" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Active keywords changed after this preview",
    );
    await user.click(
      screen.getByRole("button", { name: "Reload current keywords" }),
    );

    await waitFor(() =>
      expect(screen.getByText(/Your draft is still in the editor/)).toBeVisible(),
    );
    expect(editor).toHaveValue("Plumbers\nroofers");
    expect(screen.getByRole("button", { name: "Save reviewed list" })).toBeDisabled();
  });

  it("keeps the editor draft mounted when reloading an unavailable workspace", async () => {
    const user = userEvent.setup();
    mockRequests([
      {
        path: "/api/admin/scraper/keywords/preview",
        status: 503,
        body: { detail: "Scraper Operations is unavailable." },
      },
      {
        path: "/api/admin/scraper/keywords",
        body: workspace({
          service_state: "unavailable",
          current: [],
          winners: [],
        }),
      },
    ]);
    renderKeywords();
    const editor = screen.getByRole("textbox", { name: /Keyword list/ });
    await user.clear(editor);
    await user.type(editor, "Plumbers\nroofers");
    await user.click(screen.getByRole("button", { name: "Preview changes" }));

    await user.click(
      await screen.findByRole("button", { name: "Reload current keywords" }),
    );

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Current keywords are still unavailable",
      ),
    );
    expect(screen.queryByText(/Current active keywords reloaded/))
      .not.toBeInTheDocument();
    expect(editor).toHaveValue("Plumbers\nroofers");
  });
});
