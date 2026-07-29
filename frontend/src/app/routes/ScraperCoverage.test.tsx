import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import {
  ScraperStateCoverageRoute,
  ScraperStateDetailRoute,
} from "./ScraperCoverage";
import type {
  CoverageFeed,
  StateCoverageDetail,
  StateCoverageSnapshot,
  StateGridCoverage,
  StateKeywordActivity,
} from "./scraperCoverageData";

const keywords: StateKeywordActivity[] = [
  {
    keyword: "24 Hour Pharmacy",
    businesses: 124,
    posted_cells: 110,
    total_cells: 220,
    coverage: 50,
    empty_rate: 0.125,
    last_enqueued: "Jul 28, 11:59",
  },
  {
    keyword: "Abatement Service",
    businesses: 38,
    posted_cells: 0,
    total_cells: 220,
    coverage: 0,
    empty_rate: 0.75,
    last_enqueued: null,
  },
];

const grid: StateGridCoverage = {
  posted: 1,
  reserved: 1,
  failed: 1,
  uncovered: 1,
  cells: [
    {
      index: 1,
      cell: "40.000000,-80.000000",
      status: "posted",
    },
    {
      index: 2,
      cell: "40.000000,-79.750000",
      status: "reserved",
    },
    {
      index: 3,
      cell: "40.000000,-79.500000",
      status: "failed",
    },
    {
      index: 4,
      cell: "40.000000,-79.250000",
      status: "uncovered",
    },
  ],
};

function feed<T>(data: T, refreshSeconds: number): CoverageFeed<T> {
  return {
    state: "ok",
    refresh_seconds: refreshSeconds,
    fetched_at: "2026-07-29T12:00:00Z",
    data,
  };
}

function overview(): StateCoverageSnapshot {
  return {
    service_state: "connected",
    last_successful_at: "2026-07-29T12:00:00Z",
    idle_expires_in: 900,
    states: feed(
      [
        {
          state: "PA",
          businesses: 161_863,
          posted_cells: 110,
          total_cells: 220,
          active_keywords: 25,
          coverage: 50,
          status: "partial",
        },
        {
          state: "OH",
          businesses: 136_150,
          posted_cells: 240,
          total_cells: 240,
          active_keywords: 25,
          coverage: 100,
          status: "covered",
        },
      ],
      20,
    ),
  };
}

function detail(): StateCoverageDetail {
  return {
    state: "PA",
    service_state: "connected",
    last_successful_at: "2026-07-29T12:00:00Z",
    idle_expires_in: 900,
    keywords: feed(keywords, 10),
    cells: feed(grid, 15),
  };
}

function renderRoute(
  path: string,
  data: StateCoverageSnapshot | StateCoverageDetail,
  element: React.ReactNode,
) {
  const router = createMemoryRouter(
    [
      {
        id: "coverage",
        path,
        loader: () => data,
        element,
      },
    ],
    {
      initialEntries: [path],
      hydrationData: { loaderData: { coverage: data } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

describe("state coverage overview parity", () => {
  it("keeps state status, counts, coverage, keywords, and navigation", () => {
    renderRoute(
      "/admin/acquisition/scraper/workspace/states",
      overview(),
      <ScraperStateCoverageRoute />,
    );

    expect(
      screen.getByRole("heading", { level: 1, name: "States" }),
    ).toBeVisible();
    const pa = screen.getByRole("link", {
      name: /PA: In progress, 50% coverage, 161,863 businesses/,
    });
    expect(pa).toHaveTextContent("110/220 cells");
    expect(pa).toHaveTextContent("25 keywords");
    expect(within(pa).getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
    expect(
      screen.getByRole("link", { name: "Configuration versions" }),
    ).toBeVisible();
    expect(screen.getByText("20s refresh")).toBeVisible();
  });
});

describe("state detail parity and keyboard operation", () => {
  it("keeps every keyword activity field", () => {
    renderRoute(
      "/admin/acquisition/scraper/workspace/states/PA",
      detail(),
      <ScraperStateDetailRoute />,
    );

    const table = screen.getByRole("table", {
      name: "Per-state keyword activity and coverage",
    });
    expect(table).toHaveTextContent("24 Hour Pharmacy");
    expect(table).toHaveTextContent("124");
    expect(table).toHaveTextContent("110/220");
    expect(table).toHaveTextContent("50%");
    expect(table).toHaveTextContent("12.5%");
    expect(table).toHaveTextContent("Jul 28, 11:59");
    expect(table).toHaveTextContent("75.0%");
    expect(screen.getByText("10s refresh")).toBeVisible();
  });

  it("states all four grid statuses in words and preserves every cell", () => {
    renderRoute(
      "/admin/acquisition/scraper/workspace/states/PA",
      detail(),
      <ScraperStateDetailRoute />,
    );

    const totals = screen.getByRole("list", {
      name: "Grid status totals",
    });
    for (const status of ["Posted", "Reserved", "Failed", "Uncovered"]) {
      expect(totals).toHaveTextContent(status);
    }
    expect(
      screen.getAllByRole("button", { name: /^Cell \d:/ }),
    ).toHaveLength(4);
    expect(screen.getByText("15s refresh")).toBeVisible();
  });

  it("opens any cell drill-down with the keyboard and keeps navigation actions", async () => {
    const user = userEvent.setup();
    renderRoute(
      "/admin/acquisition/scraper/workspace/states/PA",
      detail(),
      <ScraperStateDetailRoute />,
    );

    const failed = screen.getByRole("button", {
      name: "Cell 3: 40.000000,-79.500000 — Failed",
    });
    failed.focus();
    await user.keyboard("{Enter}");

    expect(failed).toHaveAttribute("aria-pressed", "true");
    const selected = screen.getByLabelText("Selected grid cell");
    expect(selected).toHaveTextContent("Cell 3 of 4");
    expect(selected).toHaveTextContent("Failed");
    expect(
      within(selected).getByRole("button", { name: "Previous cell" }),
    ).toBeVisible();
    expect(
      within(selected).getByRole("button", { name: "Next cell" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "All states" })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Configuration versions" }),
    ).toBeVisible();
  });

  it("filters large keyword and cell sets without discarding the source data", async () => {
    const user = userEvent.setup();
    renderRoute(
      "/admin/acquisition/scraper/workspace/states/PA",
      detail(),
      <ScraperStateDetailRoute />,
    );

    const search = screen.getByRole("searchbox", {
      name: "Find keyword activity",
    });
    await user.type(search, "Abatement");
    expect(search).toHaveFocus();
    expect(screen.getByRole("table")).toHaveTextContent("Abatement Service");
    expect(screen.getByRole("table")).not.toHaveTextContent(
      "24 Hour Pharmacy",
    );
    expect(screen.getByText("1 of 2 keywords shown.")).toBeVisible();

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Grid status" }),
      "failed",
    );
    expect(
      screen.getAllByRole("button", { name: /^Cell \d:/ }),
    ).toHaveLength(1);
    expect(screen.getByText("1 of 4 cells shown.")).toBeVisible();
  });
});
