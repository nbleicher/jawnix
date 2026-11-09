import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminConfigurationsRoute, AdminNicheMappingsRoute } from "./AdminAcquisition";
import type {
  AcquisitionData,
  ConfigurationRow,
  NicheRow,
  RecommendationRow,
} from "./AdminAcquisition";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";

function recommendation(overrides: Partial<RecommendationRow> = {}): RecommendationRow {
  return {
    id: "33333333-3333-4333-8333-333333333333",
    niche: "Roofing",
    segment: "PA::roof repair",
    action: "reduce",
    status: "pending",
    evidence: {
      counts: { worked: 120, rated: 40 },
      analysis: { eligibility: "eligible", peerSegmentCount: 3 },
    },
    evidenceChecksum: "e".repeat(64),
    configurationVersion: 4,
    decisionBy: "",
    decisionReason: "",
    decidedAt: null,
    createdAt: "2026-07-28T02:00:00Z",
    resultingConfigurationId: null,
    decidable: true,
    ...overrides,
  };
}

function configuration(overrides: Partial<ConfigurationRow> = {}): ConfigurationRow {
  return {
    id: "44444444-4444-4444-8444-444444444444",
    version: 4,
    checksum: "c".repeat(64),
    status: "active",
    reason: "Baseline",
    createdAt: "2026-07-01T00:00:00Z",
    scheduledAt: null,
    activatedAt: "2026-07-01T08:00:00Z",
    basedOnConfigurationId: null,
    segmentCount: 1240,
    anomalyThresholds: {},
    ...overrides,
  };
}

function mapping(overrides: Partial<NicheRow> = {}): NicheRow {
  return {
    segment: "TX::roofing contractor",
    state: "TX",
    keyword: "roofing contractor",
    niche: "Roofing",
    confirmed: false,
    proposalSource: "keyword_match",
    evidence: {},
    confirmedBy: "",
    confirmedAt: null,
    denied: false,
    deniedBy: "",
    deniedAt: null,
    ...overrides,
  };
}

function acquisition(overrides: Partial<AcquisitionData> = {}): AcquisitionData {
  return {
    scrapeAnomalies: [{ id: "a1", status: "pending", scraperRunId: 42, decidable: true }],
    sourceRecommendations: [recommendation()],
    nicheMappings: [
      mapping(),
      mapping({
        segment: "FL::dentist",
        state: "FL",
        keyword: "dentist",
        niche: "Dental",
        confirmed: true,
        confirmedAt: "2026-07-20T12:00:00Z",
      }),
      mapping({ segment: "OH::insurance agency", state: "OH", keyword: "insurance agency", denied: true }),
    ],
    scraperConfigurations: [
      configuration({ id: "55555555-5555-4555-8555-555555555555", version: 5, status: "scheduled", activatedAt: null, scheduledAt: "2026-07-29T08:00:00Z", basedOnConfigurationId: "44444444-4444-4444-8444-444444444444", reason: "Reduce roof repair" }),
      configuration(),
    ],
    ...overrides,
  };
}

function renderRoute(element: React.ReactNode, data: AcquisitionData, path: string) {
  const router = createMemoryRouter(
    [{ id: "page", path: path.split("?")[0] ?? path, loader: () => data, element }],
    { initialEntries: [path], hydrationData: { loaderData: { page: data } } },
  );
  render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
  return router;
}

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("Configuration versions", () => {
  it("lists every version newest first with no way to rewrite one", () => {
    renderRoute(<AdminConfigurationsRoute />, acquisition(), "/admin/setup/configurations");

    const table = screen.getByRole("table", { name: "Scraper Configuration versions" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByRole("link", { name: "v5" })).toHaveAttribute(
      "href",
      "/admin/acquisition/configurations/55555555-5555-4555-8555-555555555555",
    );
    expect(within(rows[0]!).getByText("Scheduled")).toBeVisible();
    expect(within(rows[0]!).getByText(/rolled forward from an earlier version/)).toBeVisible();
    expect(within(rows[1]!).getByText("1,240")).toBeVisible();
    expect(screen.queryByRole("button", { name: /edit|rewrite|update/i })).toBeNull();
  });

  it("points held runs at Today, where their decisions live", () => {
    renderRoute(<AdminConfigurationsRoute />, acquisition(), "/admin/setup/configurations");

    expect(screen.getByText(/1 held Scrape Run is waiting/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Today" })).toHaveAttribute("href", "/admin/today");
  });

  it("shows a recommendation's evidence before its decision, and sends the checksum it showed", async () => {
    const user = userEvent.setup();
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true }));
    renderRoute(<AdminConfigurationsRoute />, acquisition(), "/admin/setup/configurations");

    await user.click(screen.getByRole("link", { name: "roof repair · PA" }));
    const panel = await screen.findByRole("complementary", { name: "Reduce · roof repair · PA" });
    expect(within(panel).getByText("120")).toBeVisible();
    expect(within(panel).getByText("Eligible")).toBeVisible();

    await user.click(within(panel).getByRole("button", { name: "Approve" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(/no existing version is rewritten and no Scrape Run starts/);
    await user.type(within(dialog).getByLabelText(/Reason/), "Peer evidence supports reducing.");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("/api/admin/source-recommendations/33333333-3333-4333-8333-333333333333/approve");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      reason: "Peer evidence supports reducing.",
      evidenceChecksum: "e".repeat(64),
    });
  });

  it("says plainly when no recommendation waits", () => {
    renderRoute(
      <AdminConfigurationsRoute />,
      acquisition({ sourceRecommendations: [] }),
      "/admin/setup/configurations",
    );

    expect(screen.getByText("No source recommendation is waiting.")).toBeVisible();
  });
});

describe("Niche mappings", () => {
  it("lists proposals awaiting review first, with a badge for each status", () => {
    renderRoute(<AdminNicheMappingsRoute />, acquisition(), "/admin/setup/niche-mappings");

    const table = screen.getByRole("table", { name: "Niche mappings" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("roofing contractor · TX")).toBeVisible();
    expect(within(rows[0]!).getByText("Awaiting review")).toBeVisible();
    expect(within(table).getByText("Confirmed")).toBeVisible();
    expect(within(table).getByText("Denied")).toBeVisible();
    expect(screen.getByText("Showing 1–3 of 3")).toBeVisible();
  });

  it("opens a mapping from Today's link and confirms a corrected Niche with a reason", async () => {
    const user = userEvent.setup();
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true }));
    renderRoute(
      <AdminNicheMappingsRoute />,
      acquisition(),
      "/admin/setup/niche-mappings?segment=TX%3A%3Aroofing%20contractor",
    );

    const panel = screen.getByRole("complementary", { name: "roofing contractor · TX" });
    expect(within(panel).getByText("Niche mapping · Awaiting review")).toBeVisible();
    await user.click(within(panel).getByRole("button", { name: "Confirm Niche" }));
    const dialog = screen.getByRole("dialog");
    const niche = within(dialog).getByRole("textbox", { name: /Niche/ });
    expect(niche).toHaveValue("Roofing");
    await user.clear(niche);
    await user.type(niche, "Roofing contractors");
    await user.type(within(dialog).getByLabelText(/Reason/), "Taxonomy correction.");
    await user.click(within(dialog).getByRole("button", { name: "Confirm Niche" }));

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    const [url, init] = fetch.mock.calls[0]!;
    expect(url).toBe("/api/admin/source-niches/TX%3A%3Aroofing%20contractor/confirm");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      niche: "Roofing contractors",
      reason: "Taxonomy correction.",
    });
  });

  it("offers a correction, not a denial, for a confirmed mapping", () => {
    renderRoute(<AdminNicheMappingsRoute />, acquisition(), "/admin/setup/niche-mappings?segment=FL%3A%3Adentist");

    const panel = screen.getByRole("complementary", { name: "dentist · FL" });
    expect(within(panel).getByRole("button", { name: "Correct Niche" })).toBeVisible();
    expect(within(panel).queryByRole("button", { name: "Deny proposal" })).toBeNull();
  });

  it("filters by status from the URL", () => {
    renderRoute(<AdminNicheMappingsRoute />, acquisition(), "/admin/setup/niche-mappings?status=confirmed");

    const table = screen.getByRole("table", { name: "Niche mappings" });
    expect(within(table).getAllByRole("row")).toHaveLength(2);
    expect(within(table).getByText("dentist · FL")).toBeVisible();
  });
});
