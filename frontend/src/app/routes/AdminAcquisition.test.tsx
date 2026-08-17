import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AdminAcquisitionRoute } from "./AdminAcquisition";
import type {
  AcquisitionData,
  ConfigurationRow,
  RecommendationRow,
  ScrapeAnomalyRow,
} from "./AdminAcquisition";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";

function anomaly(overrides: Partial<ScrapeAnomalyRow> = {}): ScrapeAnomalyRow {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    status: "pending",
    scraperRunId: 42,
    configurationId: "22222222-2222-4222-8222-222222222222",
    datasetChecksum: "a".repeat(64),
    decisionBy: "",
    decisionReason: "",
    decidedAt: null,
    createdAt: "2026-07-28T02:00:00Z",
    runStatus: "held_anomaly",
    anomalousSegments: [
      { key: "roofing-austin-tx", reasons: ["more_than_50_percent_down"] },
    ],
    decidable: true,
    ...overrides,
  };
}

function recommendation(
  overrides: Partial<RecommendationRow> = {},
): RecommendationRow {
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

function configuration(
  overrides: Partial<ConfigurationRow> = {},
): ConfigurationRow {
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
    segmentCount: 2,
    anomalyThresholds: {},
    ...overrides,
  };
}

function acquisition(
  overrides: Partial<AcquisitionData> = {},
): AcquisitionData {
  return {
    nightlyReviews: [],
    scrapeAnomalies: [anomaly()],
    sourceRecommendations: [recommendation()],
    nicheMappings: [],
    scraperConfigurations: [configuration()],
    ...overrides,
  };
}

function renderRoute(data: AcquisitionData) {
  const router = createMemoryRouter(
    [
      {
        id: "acquisition",
        path: "/admin/acquisition",
        loader: () => data,
        element: <AdminAcquisitionRoute />,
      },
    ],
    {
      initialEntries: ["/admin/acquisition"],
      hydrationData: { loaderData: { acquisition: data } },
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
}

beforeEach(() => {
  document.cookie = "jawnix_csrf=test-csrf";
  vi.restoreAllMocks();
});

describe("the Acquisition workspace keeps the Operations identity", () => {
  it("renders inside the Match workspace frame", () => {
    renderRoute(acquisition());

    expect(
      screen.getByRole("region", { name: "Acquisition workspace" }),
    ).toBeVisible();
    expect(screen.getByText("Operations")).toBeVisible();
  });

  it("says in words when work is held rather than relying on colour", () => {
    renderRoute(acquisition());

    expect(screen.getByText("HELD / 1 AWAITING DECISION")).toBeVisible();
  });

  it("reads nominal when nothing is held", () => {
    renderRoute(
      acquisition({ scrapeAnomalies: [], sourceRecommendations: [] }),
    );

    expect(screen.getByText("ONLINE / NOMINAL")).toBeVisible();
  });
});

describe("Scrape Anomaly decisions", () => {
  it("shows the evidence the decision rests on", () => {
    renderRoute(acquisition());

    expect(screen.getByText(/roofing-austin-tx/)).toBeVisible();
    expect(screen.getByText(/more than 50 percent down/)).toBeVisible();
  });

  it("posts confirm to the shared decision endpoint with its reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "confirmed" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderRoute(acquisition());

    const held = screen.getByRole("region", {
      name: "Held Scrape Anomalies",
    });
    await user.click(within(held).getByRole("button", { name: "Confirm" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAccessibleDescription(/Publishes the held staged/);
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Deliberate source change.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/scrape-anomalies/11111111-1111-4111-8111-111111111111/confirm",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ reason: "Deliberate source change." }),
        }),
      );
    });
  });

  it("refuses to submit a decision without a reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderRoute(acquisition());

    const held = screen.getByRole("region", { name: "Held Scrape Anomalies" });
    await user.click(within(held).getByRole("button", { name: "Deny" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Deny" }));

    expect(await screen.findByText("A reason is required.")).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("offers no decision on an anomaly that was already decided", () => {
    renderRoute(
      acquisition({
        scrapeAnomalies: [
          anomaly({
            status: "superseded",
            decidable: false,
            decisionBy: "telegram:12345",
            decidedAt: "2026-07-28T03:00:00Z",
          }),
        ],
      }),
    );

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "No Scrape Anomalies are held",
      }),
    ).toBeVisible();
  });
});

describe("Source Recommendations stay human-controlled", () => {
  it("shows the evidence before offering a decision", () => {
    renderRoute(acquisition());

    expect(
      screen.getByText(/Worked 120 · rated 40 · eligibility eligible/),
    ).toBeVisible();
    expect(
      screen.getByText(/Compared against 3 same-niche peers/),
    ).toBeVisible();
  });

  it("binds the decision to the evidence checksum it displayed", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "approved" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderRoute(acquisition());

    const section = screen.getByRole("region", {
      name: "Source Recommendations",
    });
    await user.click(within(section).getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Peer evidence supports this.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/source-recommendations/33333333-3333-4333-8333-333333333333/approve",
        expect.objectContaining({
          body: JSON.stringify({
            evidenceChecksum: "e".repeat(64),
            reason: "Peer evidence supports this.",
          }),
        }),
      );
    });
  });

  it("states that approval schedules a version rather than changing acquisition", async () => {
    const user = userEvent.setup();
    renderRoute(acquisition());

    const section = screen.getByRole("region", {
      name: "Source Recommendations",
    });
    await user.click(within(section).getByRole("button", { name: "Approve" }));

    expect(await screen.findByRole("dialog")).toHaveAccessibleDescription(
      /no existing version is rewritten and no Scrape Run starts/,
    );
  });

  it("surfaces a refused decision without pretending it applied", async () => {
    const user = userEvent.setup();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: "The evidence changed since it was shown; production was not changed.",
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderRoute(acquisition());

    const section = screen.getByRole("region", {
      name: "Source Recommendations",
    });
    await user.click(within(section).getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Acting on older numbers.",
    );
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    expect(
      await screen.findByText(
        "The evidence changed since it was shown; production was not changed.",
      ),
    ).toBeVisible();
  });
});

describe("Niche mapping confirmation", () => {
  const unconfirmed = {
    segment: "TX::roofing contractor",
    state: "TX",
    keyword: "roofing contractor",
    niche: "Roofing",
    confirmed: false,
    proposalSource: "ai_openrouter",
    evidence: {},
    confirmedBy: "",
    confirmedAt: null,
  };

  it("requires an audit reason before confirming", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderRoute(acquisition({ nicheMappings: [unconfirmed] }));

    const section = screen.getByRole("region", { name: "Niche mappings" });
    await user.click(
      within(section).getByRole("button", { name: "Review and confirm" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm Niche" }),
    );

    expect(await screen.findByText("A reason is required.")).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("posts the corrected Niche with its reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ confirmed: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    renderRoute(acquisition({ nicheMappings: [unconfirmed] }));

    const section = screen.getByRole("region", { name: "Niche mappings" });
    await user.click(
      within(section).getByRole("button", { name: "Review and confirm" }),
    );
    const dialog = await screen.findByRole("dialog");

    const nicheField = within(dialog).getByLabelText("Niche (required)");
    await user.clear(nicheField);
    await user.type(nicheField, "Roof Replacement");
    await user.type(
      within(dialog).getByLabelText("Reason (required)"),
      "Corrected from the delivered listings.",
    );
    await user.click(
      within(dialog).getByRole("button", { name: "Confirm Niche" }),
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        "/api/admin/source-niches/TX%3A%3Aroofing%20contractor/confirm",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            niche: "Roof Replacement",
            reason: "Corrected from the delivered listings.",
          }),
        }),
      );
    });
  });
});

describe("Scraper Configuration versions", () => {
  it("lists versions with their checksum and status, offering no edit", () => {
    renderRoute(
      acquisition({
        scraperConfigurations: [
          configuration({ version: 5, status: "scheduled" }),
          configuration({
            id: "55555555-5555-4555-8555-555555555555",
            version: 4,
            status: "superseded",
          }),
        ],
      }),
    );

    const section = screen.getByRole("region", {
      name: "Scraper Configuration versions",
    });
    expect(
      within(section).getByRole("heading", { level: 3, name: "Version 5" }),
    ).toBeVisible();
    expect(
      within(section).getByRole("heading", { level: 3, name: "Version 4" }),
    ).toBeVisible();
    // Immutability is structural: the screen offers no way to rewrite one.
    expect(
      within(section).queryByRole("button", { name: /edit/i }),
    ).toBeNull();
  });
});

describe("administrator Exclusion List bulk upload", () => {
  it("posts the typed CSV with its audit reason as multipart form data", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "55555555-5555-4555-8555-555555555555",
          type: "dnc",
          filename: "junk.csv",
          status: "queued",
          totalRows: 0,
          acceptedRows: 0,
          invalidRows: 0,
          duplicateRows: 0,
          poolImpact: 0,
          global: false,
          error: "",
          createdAt: "2026-08-04T00:00:00Z",
          ingestedAt: null,
          decidedAt: null,
        }),
        { status: 202, headers: { "Content-Type": "application/json" } },
      ),
    );
    renderRoute(acquisition());

    const region = screen.getByRole("region", { name: "Exclusion review" });
    await user.selectOptions(within(region).getByLabelText(/Type/), "dnc");
    await user.upload(
      within(region).getByLabelText(/CSV file/),
      new File(["phone\n2155550000\n"], "junk.csv", { type: "text/csv" }),
    );
    await user.type(
      within(region).getByLabelText(/Reason/),
      "Purge junk phones",
    );
    await user.click(
      within(region).getByRole("button", { name: "Upload globally" }),
    );

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const [path, init] = fetchSpy.mock.calls[0]!;
    expect(path).toBe("/api/admin/exclusion-lists");
    const body = (init as RequestInit).body as FormData;
    expect(body.get("type")).toBe("dnc");
    expect(body.get("reason")).toBe("Purge junk phones");
    expect((body.get("file") as File).name).toBe("junk.csv");
    expect(await within(region).findByRole("status")).toHaveTextContent(
      "Processing junk.csv",
    );
  });

  it("refuses to upload without an audit reason", async () => {
    const user = userEvent.setup();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    renderRoute(acquisition());

    const region = screen.getByRole("region", { name: "Exclusion review" });
    await user.upload(
      within(region).getByLabelText(/CSV file/),
      new File(["phone\n2155550000\n"], "junk.csv", { type: "text/csv" }),
    );
    await user.click(
      within(region).getByRole("button", { name: "Upload globally" }),
    );

    expect(
      await within(region).findByText("An upload reason is required."),
    ).toBeVisible();
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
