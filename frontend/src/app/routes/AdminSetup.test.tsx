import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AcquisitionMovedRoute, AdminSetupRoute, adminSetupLoader } from "./AdminSetup";
import type { SetupArea, SetupData } from "./AdminSetup";

function area(overrides: Partial<SetupArea> & Pick<SetupArea, "key" | "label" | "href">): SetupArea {
  return {
    state: "",
    attention: false,
    changedAt: null,
    changedNote: null,
    upcoming: null,
    ...overrides,
  };
}

function setup(overrides: Partial<SetupData> = {}): SetupData {
  return {
    generatedAt: "2026-11-09T12:00:00Z",
    areas: [
      area({ key: "scraperWorkspace", label: "Scraper workspace", href: "/app/admin/acquisition/scraper", state: "Run 42 committed", changedAt: "2026-11-09T05:12:00Z" }),
      area({ key: "scraperSettings", label: "Scraper settings", href: "/app/admin/acquisition/scraper/workspace/runtime", state: "12 active states · queue depth 50", changedAt: "2026-11-01T12:00:00Z" }),
      area({ key: "configurations", label: "Configuration versions", href: "/app/admin/setup/configurations", state: "v12 active · 1,240 segments", changedAt: "2026-11-01T12:00:00Z" }),
      area({ key: "keywords", label: "Keywords", href: "/app/admin/acquisition/scraper/workspace/keywords", state: "418 keywords in reserve", changedAt: "2026-11-04T12:00:00Z", upcoming: { label: "next refill", at: "2026-11-15T12:00:00Z" } }),
      area({ key: "nicheMappings", label: "Niche mappings", href: "/app/admin/setup/niche-mappings", state: "1 awaiting review", attention: true, changedAt: "2026-11-08T12:00:00Z" }),
      area({ key: "agencies", label: "Agencies", href: "/app/admin/agencies", state: "2 active · 14 members", changedAt: "2026-10-02T12:00:00Z" }),
      area({ key: "security", label: "Security", href: "/app/admin/security", state: "Authenticator required at every sign-in", changedAt: "2026-07-24T12:00:00Z" }),
      area({ key: "billing", label: "Billing defaults", href: "/app/admin/setup?area=billing", state: "Starting rate $5.00 per 1,000", changedNote: "Server setting" }),
    ],
    billing: { enabled: true, startingRateCentsPerThousand: 500, billedCustomers: 11 },
    scraperGranted: true,
    security: {
      assurance: "aal2",
      enforced: true,
      stage: "idle",
      factors: [
        { id: "f1", name: "Primary", status: "verified", type: "totp", createdAt: null, lastUsedAt: "2026-11-09T09:00:00Z", lastUsedFrom: null },
        { id: "f2", name: "Backup", status: "verified", type: "totp", createdAt: null, lastUsedAt: null, lastUsedFrom: null },
      ],
      throttled: false,
      lockedUntil: null,
      next: "/app/admin/today",
    },
    ...overrides,
  };
}

function renderRoute(data: SetupData, path = "/admin/setup") {
  const router = createMemoryRouter(
    [{ id: "setup", path: "/admin/setup", loader: () => data, element: <AdminSetupRoute /> }],
    { initialEntries: [path], hydrationData: { loaderData: { setup: data } } },
  );
  render(<RouterProvider router={router} />);
  return router;
}

function row(name: string) {
  return screen.getByRole("link", { name }).closest("tr")!;
}

afterEach(() => vi.restoreAllMocks());

describe("Setup", () => {
  it("lists every occasional tool with its state and last change", () => {
    renderRoute(setup());

    const table = screen.getByRole("table", { name: "Setup areas" });
    expect(within(table).getAllByRole("row")).toHaveLength(9);
    expect(within(row("Configuration versions")).getByText("v12 active · 1,240 segments")).toBeVisible();
    expect(within(row("Keywords")).getByText(/418 keywords in reserve · next refill Nov 15/)).toBeVisible();
    expect(within(row("Niche mappings")).getByText("1 awaiting review")).toHaveClass("jx-text--danger");
    expect(within(row("Agencies")).getByText("Oct 2")).toBeVisible();
    expect(within(row("Billing defaults")).getByText("Server setting")).toBeVisible();
    expect(screen.getByRole("link", { name: "Configuration versions" })).toHaveAttribute(
      "href",
      "/admin/setup/configurations",
    );
  });

  it("says the Scraper workspace stays verified until sign-out once verified", () => {
    renderRoute(setup());

    expect(within(row("Scraper workspace")).getByText(/Verified until you sign out · Run 42 committed/)).toBeVisible();
    expect(within(row("Scraper workspace")).getByText("Live")).toBeVisible();
  });

  it("asks for one verification per sign-in when there is no grant", () => {
    renderRoute(setup({ scraperGranted: false }));

    expect(within(row("Scraper workspace")).getByText(/Verify once per sign-in/)).toBeVisible();
    expect(within(row("Scraper workspace")).queryByText("Live")).toBeNull();
  });

  it("counts verified authenticators, or falls back when the provider is unreachable", () => {
    renderRoute(setup());
    expect(within(row("Security")).getByText(/2 authenticators · last used Nov 9/)).toBeVisible();
  });

  it("falls back to Jawnix's own state when the provider is unreachable", () => {
    renderRoute(setup({ security: null, scraperGranted: null }));
    expect(within(row("Security")).getByText("Authenticator required at every sign-in")).toBeVisible();
    expect(within(row("Scraper workspace")).getByText("Run 42 committed")).toBeVisible();
  });

  it("opens billing defaults beside the table", async () => {
    const user = userEvent.setup();
    const router = renderRoute(setup());

    await user.click(screen.getByRole("link", { name: "Billing defaults" }));

    expect(router.state.location.search).toBe("?area=billing");
    const panel = await screen.findByRole("complementary", { name: "Billing defaults" });
    expect(within(panel).getByText("$5.00 per 1,000")).toBeVisible();
    expect(within(panel).getByText("11")).toBeVisible();
  });

  it("reads the Scraper grant and authenticators without failing when either is down", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url === "/api/admin/setup") {
        return new Response(JSON.stringify({ generatedAt: "x", areas: [], billing: setup().billing }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ detail: "Unavailable" }), { status: 503 });
    });

    await expect(adminSetupLoader()).resolves.toMatchObject({ scraperGranted: null, security: null, areas: [] });
  });
});

describe("the former Acquisition address", () => {
  function land(entry: string) {
    const router = createMemoryRouter(
      [
        { path: "/admin/acquisition", element: <AcquisitionMovedRoute /> },
        { path: "*", element: <p>landed</p> },
      ],
      { initialEntries: [entry] },
    );
    render(<RouterProvider router={router} />);
    return router;
  }

  it("sends a Telegram review link to the nightly reviews on Insights", () => {
    const router = land("/admin/acquisition#nightly-reviews");
    expect(router.state.location.pathname).toBe("/admin/insights");
    expect(router.state.location.hash).toBe("#nightly-reviews");
  });

  it("sends everything else to Setup", () => {
    expect(land("/admin/acquisition").state.location.pathname).toBe("/admin/setup");
  });

  it("sends held anomalies to Today, where they are decided", () => {
    expect(land("/admin/acquisition#held-scrape-anomalies").state.location.pathname).toBe("/admin/today");
  });
});
