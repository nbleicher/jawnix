import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import {
  ScraperStepUpRoute,
  ScraperWorkspaceRoute,
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
      { factors, idleExpiresIn: 900 },
    );

    expect(
      screen.getByRole("heading", {
        level: 1,
        name: "Verify access to Scraper Operations",
      }),
    ).toBeVisible();
    expect(
      screen.getByRole("region", {
        name: "Scraper Operations terminal",
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
      screen.getByText(/closes after 15 minutes without Scraper activity/i),
    ).toBeVisible();
    expect(document.documentElement).toHaveAttribute(
      "data-theme",
      "terminal",
    );
  });

  it("fails closed without rendering operational controls", () => {
    renderRoute(
      <ScraperWorkspaceRoute />,
      {
        available: false,
        lastSuccessfulAt: "2026-07-28T12:00:00Z",
      },
      "/admin/acquisition/scraper/workspace",
    );

    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Scraper Operations unavailable",
      }),
    ).toBeVisible();
    expect(screen.getByText(/No controls are available/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Back to Acquisition" }),
    ).toHaveAttribute("href", "/app/admin/acquisition");
    expect(screen.queryByText("Pause pipeline")).not.toBeInTheDocument();
  });
});
