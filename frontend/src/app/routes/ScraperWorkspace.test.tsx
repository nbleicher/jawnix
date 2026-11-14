import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import {
  ScraperStepUpRoute,
  scraperEntryLoader,
} from "./ScraperWorkspace";

const factors = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Jawnix primary",
    type: "totp",
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Jawnix backup",
    type: "totp",
  },
];

function renderRoute(
  element: React.ReactNode,
  data: unknown,
  path = "/admin/acquisition/scraper",
) {
  const router = createMemoryRouter(
    [{ id: "scraper", path, loader: () => data, element }],
    {
      initialEntries: [path],
      hydrationData: { loaderData: { scraper: data } },
    },
  );
  render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );
}

describe("Scraper Operations workspace", () => {
  it("explains and collects the mandatory fresh challenge", async () => {
    const user = userEvent.setup();
    renderRoute(
      <ScraperStepUpRoute />,
      { granted: false, factors },
    );

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Verify access to Scraper Operations",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("region", {
        name: "Scraper Operations workspace",
      }),
    ).toBeVisible();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.getByText("Jawnix primary", { exact: true })).toBeVisible();
    expect(screen.queryByText("Jawnix backup", { exact: true })).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "I cannot use my primary authenticator",
      }),
    );
    expect(screen.getAllByRole("radio")).toHaveLength(2);
    expect(
      screen.getByText(/lasts until\s+you sign out or your administrator session expires/i),
    ).toBeVisible();
  });
});

describe("entering the Scraper workspace", () => {
  afterEach(() => vi.restoreAllMocks());

  function entry(body: unknown) {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
  }

  it("reuses this sign-in's live grant instead of asking again", async () => {
    entry({ granted: true, factors: [] });

    const outcome = await scraperEntryLoader().catch((thrown: unknown) => thrown);

    expect(outcome).toBeInstanceOf(Response);
    expect((outcome as Response).headers.get("Location")).toBe(
      "/admin/acquisition/scraper/workspace",
    );
  });

  it("asks for the challenge when this sign-in has no grant", async () => {
    entry({ granted: false, factors });

    await expect(scraperEntryLoader()).resolves.toEqual({ granted: false, factors });
  });
});
