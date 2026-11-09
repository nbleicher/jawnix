import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminSourcePerformanceRoute } from "./AdminSourcePerformance";
import type {
  SourcePerformanceData,
  SourcePerformanceRow,
} from "./AdminSourcePerformance";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";

function row(
  overrides: Partial<SourcePerformanceRow> = {},
): SourcePerformanceRow {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    date: "2026-07-28",
    segment: "TX::roofing contractor",
    state: "TX",
    keyword: "roofing contractor",
    niche: "Roofing",
    nicheConfirmed: true,
    counts: { delivered: 40, worked: 32, rated: 12, good: 8, poor: 4 },
    rates: { good: 0.667, positiveResponse: 0.3125, appointmentBooked: 0.125 },
    intervals: {},
    trend: { positiveResponse: 0.02 },
    confidence: "eligible",
    actionState: "prescriptive_dormant",
    evidenceChecksum: "e".repeat(64),
    ...overrides,
  };
}

function performance(
  overrides: Partial<SourcePerformanceData> = {},
): SourcePerformanceData {
  return {
    cohorts: [],
    segments: [],
    global: {
      delivered: 400,
      worked: 320,
      rated: 120,
      good: 80,
      poor: 40,
      positiveResponses: 100,
      appointmentsBooked: 40,
      rates: {
        good: 0.667,
        positiveResponse: 0.3125,
        appointmentBooked: 0.125,
      },
      prescriptive: false,
    },
    legacy: { delivered: 15, excludedFromRecommendations: true },
    rows: [row()],
    ...overrides,
  };
}

function renderRoute(data: SourcePerformanceData) {
  const router = createMemoryRouter(
    [
      {
        id: "source-performance",
        path: "/admin/insights/sources",
        loader: () => data,
        element: <AdminSourcePerformanceRoute />,
      },
    ],
    {
      initialEntries: ["/admin/insights/sources"],
      hydrationData: { loaderData: { "source-performance": data } },
    },
  );
  return render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("Source performance under Insights", () => {
  it("is an Insights page with a way back", () => {
    renderRoute(performance());

    expect(
      screen.getByRole("heading", { level: 1, name: "Source performance" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Back to Insights" })).toHaveAttribute(
      "href",
      "/app/admin/insights",
    );
  });

  it("shows the all-time summary from global counts and rates", () => {
    renderRoute(performance());

    const summary = screen.getByLabelText("All-time summary");
    expect(within(summary).getByText("400")).toBeVisible();
    expect(within(summary).getByText("320")).toBeVisible();
    expect(within(summary).getByText("66.7%")).toBeVisible();
    expect(within(summary).getByText(/15 legacy-source deliveries/)).toBeVisible();
  });

  it("lists nightly rows with their state, keyword, and niche", () => {
    renderRoute(performance());

    const table = screen.getByRole("table", { name: /nightly/i });
    expect(within(table).getByRole("button", { name: "roofing contractor" })).toBeVisible();
    expect(within(table).getByText("TX")).toBeVisible();
    expect(within(table).getByText("Roofing")).toBeVisible();
    expect(within(table).getByText("Eligible")).toBeVisible();
    expect(within(table).getByText("31.3%")).toBeVisible();
  });

  it("shows no rate, not 0%, for a segment without outcomes", () => {
    renderRoute(
      performance({
        global: {
          ...performance().global,
          worked: 0,
          rated: 0,
          rates: { good: 0, positiveResponse: 0, appointmentBooked: 0 },
        },
        rows: [
          row({
            counts: { delivered: 84_210, worked: 0, rated: 0 },
            rates: { good: 0, positiveResponse: 0, appointmentBooked: 0 },
          }),
        ],
      }),
    );

    const table = screen.getByRole("table", { name: /nightly/i });
    expect(within(table).queryByText("0.0%")).toBeNull();
    expect(within(table).getAllByText("—").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText("Rates stay empty until customers report outcomes.")).toBeVisible();
  });

  it("opens a history dialog for a keyword, not an alert", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          rows: [
            {
              ...row(),
              date: "2026-07-27",
              suggestionNote: "prescriptive_dormant: retained evidence.",
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderRoute(performance());

    await user.click(screen.getByRole("button", { name: "roofing contractor" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleName("History · roofing contractor · TX");
    expect(await within(dialog).findByText(/Jul 27/)).toBeVisible();
    expect(within(dialog).getByText(/retained evidence/)).toBeVisible();
    expect(fetchSpy).toHaveBeenCalledWith(
      "/api/admin/source-performance/TX%3A%3Aroofing%20contractor/history",
      expect.anything(),
    );
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("shows no nightly rows without pretending the filters failed", () => {
    renderRoute(performance({ rows: [] }));

    expect(screen.getByText(/No nightly rows match these filters/)).toBeVisible();
  });
});
