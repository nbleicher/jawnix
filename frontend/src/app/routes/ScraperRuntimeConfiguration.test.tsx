import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import { ScraperCampaignHistoryRoute } from "./ScraperCampaignHistory";
import { ScraperRuntimeConfigurationRoute } from "./ScraperRuntimeConfiguration";
import type {
  CampaignHistory,
  RuntimePreview,
  RuntimeWorkspace,
} from "./scraperRuntimeApi";

const VERSION = "a".repeat(64);
const NEXT_VERSION = "b".repeat(64);

const HISTORY: CampaignHistory = {
  service_state: "connected",
  last_successful_at: "2026-07-29T12:00:00Z",
  idle_expires_in: 900,
  search: "",
  state: "",
  sort: "last_enqueued",
  direction: "desc",
  all_states: ["KY", "OH", "PA"],
  rows: [
    {
      keyword: "Acoustic Guitar Lessons",
      state: "OH",
      cells_posted: 240,
      first_enqueued: "Jul 27, 13:41",
      latest_enqueued: "Jul 29, 00:00",
      campaign_date: "Jul 29, 2026",
    },
  ],
};

const WORKSPACE: RuntimeWorkspace = {
  service_state: "connected",
  last_successful_at: "2026-07-29T12:00:00Z",
  idle_expires_in: 900,
  version: VERSION,
  all_states: ["KY", "OH", "PA"],
  current: {
    states: ["KY", "OH"],
    settings: {
      zoom: 15,
      radius: 10000,
      depth: 3,
      lang: "en",
      fast_mode: true,
      timeout: 300,
    },
    queue: {
      target_depth: 50,
      target_per_worker: 25,
      min_target_depth: 25,
      max_target_depth: 500,
      batch_size: 100,
      poll_secs: 5,
      skip_recent_days: 0,
    },
    overrides: {},
  },
  cells: [
    { state: "KY", cells: 324 },
    { state: "OH", cells: 240 },
  ],
  total_cells: 564,
  bounds: {
    runtime: {
      zoom: { minimum: 1, maximum: 21, step: 1 },
      radius: { minimum: 100, maximum: 100000, step: 1 },
      depth: { minimum: 1, maximum: 100, step: 1 },
      timeout: { minimum: 1, maximum: 300, step: 1 },
    },
    queue: {
      target_depth: { minimum: 1, maximum: 10000, step: 1 },
      target_per_worker: { minimum: 1, maximum: 100, step: 1 },
      min_target_depth: { minimum: 1, maximum: 10000, step: 1 },
      max_target_depth: { minimum: 1, maximum: 100000, step: 1 },
      batch_size: { minimum: 1, maximum: 10000, step: 1 },
      poll_secs: { minimum: 5, maximum: 3600, step: 1 },
      skip_recent_days: { minimum: 0, maximum: 365, step: 1 },
    },
    override: {
      cell_size_km: { minimum: 1, maximum: 500, step: 0.5 },
      zoom: { minimum: 1, maximum: 21, step: 1 },
    },
    language_max_length: 10,
  },
};

const PREVIEW: RuntimePreview = {
  configuration: {
    ...WORKSPACE.current,
    settings: { ...WORKSPACE.current.settings, depth: 5 },
  },
  expected_version: VERSION,
  proposed_version: NEXT_VERSION,
  review_token: "signed-runtime-review",
  effects: {
    cells: WORKSPACE.cells,
    current_total_cells: 564,
    proposed_total_cells: 564,
    total_cell_delta: 0,
    states_added: [],
    states_removed: [],
    runtime_changes: ["depth"],
    queue_changes: [],
    override_changes: [],
  },
};

function renderRoute(
  kind: "history" | "runtime",
  data: CampaignHistory | RuntimeWorkspace,
) {
  const path =
    kind === "history"
      ? "/admin/acquisition/scraper/workspace/history"
      : "/admin/acquisition/scraper/workspace/runtime";
  const id = kind;
  const router = createMemoryRouter(
    [
      {
        id,
        path,
        loader: () => data,
        element:
          kind === "history" ? (
            <ScraperCampaignHistoryRoute />
          ) : (
            <ScraperRuntimeConfigurationRoute />
          ),
      },
    ],
    {
      initialEntries: [path],
      hydrationData: { loaderData: { [id]: data } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

interface Plan {
  match: string | RegExp;
  body: unknown;
  status?: number;
}

function mockRequests(plans: Plan[]) {
  const calls: Array<{ path: string; body: unknown }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: string, init?: RequestInit) => {
      const plan = plans.shift();
      if (!plan) throw new Error(`Unexpected request: ${input}`);
      if (typeof plan.match === "string") expect(input).toBe(plan.match);
      else expect(input).toMatch(plan.match);
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      calls.push({ path: input, body });
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

describe("offline workspace states", () => {
  it("renders campaign history in place when Scale is unavailable", () => {
    renderRoute("history", {
      ...HISTORY,
      service_state: "unavailable",
      rows: [],
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Campaign history unavailable",
    );
    expect(screen.queryByRole("form", { name: "Campaign history filters" }))
      .not.toBeInTheDocument();
  });

  it("renders runtime configuration in place when Scale is unavailable", () => {
    renderRoute("runtime", {
      ...WORKSPACE,
      service_state: "unavailable",
      all_states: [],
      cells: [],
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Runtime configuration unavailable",
    );
    expect(screen.queryByRole("button", { name: "Save reviewed configuration" }))
      .not.toBeInTheDocument();
  });
});

describe("campaign history parity", () => {
  it("shows every row detail and retains all filter controls", () => {
    renderRoute("history", HISTORY);

    const table = screen.getByRole("table");
    expect(within(table).getByText("Acoustic Guitar Lessons")).toBeVisible();
    expect(within(table).getByText("OH")).toBeVisible();
    expect(within(table).getByText("240")).toBeVisible();
    expect(within(table).getByText("Jul 27, 13:41")).toBeVisible();
    expect(within(table).getByText("Jul 29, 00:00")).toBeVisible();
    expect(within(table).getByText("Jul 29, 2026")).toBeVisible();
    expect(screen.getByRole("searchbox", { name: "Search keywords" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "State" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Sort by" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Direction" })).toBeVisible();
  });

  it("queries search, state, sort and direction together", async () => {
    const user = userEvent.setup();
    const filtered = {
      ...HISTORY,
      search: "guitar",
      state: "OH",
      sort: "cells_posted" as const,
      direction: "asc" as const,
    };
    mockRequests([
      {
        match:
          /\/api\/admin\/scraper\/history\?.*search=guitar.*state=OH.*sort=cells_posted.*direction=asc/,
        body: filtered,
      },
    ]);
    renderRoute("history", HISTORY);

    await user.type(
      screen.getByRole("searchbox", { name: "Search keywords" }),
      "guitar",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "State" }),
      "OH",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Sort by" }),
      "cells_posted",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Direction" }),
      "asc",
    );

    await waitFor(() =>
      expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1),
    );
  });
});

describe("runtime configuration parity", () => {
  it("keeps save unavailable until exact preview and preserves enqueue", async () => {
    const user = userEvent.setup();
    const calls = mockRequests([
      {
        match: "/api/admin/scraper/runtime/preview",
        body: PREVIEW,
      },
      {
        match: "/api/admin/scraper/runtime/save",
        body: {
          revision_id: "33333333-3333-4333-8333-333333333333",
          version: NEXT_VERSION,
          configuration: PREVIEW.configuration,
          effects: PREVIEW.effects,
          enqueued: true,
        },
      },
    ]);
    renderRoute("runtime", WORKSPACE);
    const depth = screen.getByRole("spinbutton", { name: /Depth/ });
    await user.clear(depth);
    await user.type(depth, "5");
    await user.click(
      screen.getByRole("button", { name: "Preview calculated effects" }),
    );

    expect(
      await screen.findByRole("region", { name: "Runtime change preview" }),
    ).toHaveTextContent("Validation passed");
    const save = screen.getByRole("button", {
      name: "Save reviewed runtime configuration",
    });
    expect(save).toBeDisabled();
    await user.type(
      screen.getByRole("textbox", { name: /Change reason/ }),
      "Tune the reviewed campaign",
    );
    await user.click(
      screen.getByRole("checkbox", { name: /Request enqueue after save/ }),
    );
    expect(save).toBeEnabled();
    await user.click(save);

    expect(
      await screen.findByText(
        "Scale runtime configuration saved and enqueue requested.",
      ),
    ).toBeVisible();
    expect(calls[1]?.body).toMatchObject({
      configuration: PREVIEW.configuration,
      expected_version: VERSION,
      review_token: "signed-runtime-review",
      enqueue: true,
      reason: "Tune the reviewed campaign",
    });
  });

  it("explains bounds before making a preview request", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderRoute("runtime", WORKSPACE);
    const zoom = screen.getAllByRole("spinbutton", { name: "Zoom" })[0]!;
    await user.clear(zoom);
    await user.type(zoom, "22");
    await user.click(
      screen.getByRole("button", { name: "Preview calculated effects" }),
    );

    expect(
      await screen.findByText(/Zoom must be between 1 and 21/),
    ).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", {
        name: "Save reviewed runtime configuration",
      }),
    ).toBeDisabled();
  });
});
