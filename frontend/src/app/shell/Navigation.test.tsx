import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { Navigation } from "./Navigation";
import type { NavigationDestination } from "./Navigation";

const DESTINATIONS: NavigationDestination[] = [
  { to: "/app/overview", label: "Overview" },
  { to: "/app/requests", label: "Requests" },
  { to: "/app/feedback", label: "Feedback" },
  { to: "/app/account", label: "Account" },
];

function renderNavigation(initialPath: string) {
  const router = createMemoryRouter(
    [
      {
        path: "*",
        element: <Navigation label="Customer" destinations={DESTINATIONS} />,
      },
    ],
    { initialEntries: [initialPath] },
  );
  return render(<RouterProvider router={router} />);
}

describe("Navigation", () => {
  it("exposes every primary destination", () => {
    renderNavigation("/app/overview");

    const nav = screen.getByRole("navigation", { name: "Customer" });
    for (const destination of DESTINATIONS) {
      expect(within(nav).getByRole("link", { name: destination.label })).toBeInTheDocument();
    }
  });

  it("renders one navigation landmark, so mobile and desktop do not duplicate it", () => {
    renderNavigation("/app/overview");

    expect(screen.getAllByRole("navigation")).toHaveLength(1);
  });

  it("marks the active destination as the current page", () => {
    renderNavigation("/app/requests");

    expect(screen.getByRole("link", { name: "Requests" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("treats a nested route as being within its destination", () => {
    renderNavigation("/app/requests/1234");

    expect(screen.getByRole("link", { name: "Requests" })).toHaveAttribute("aria-current", "page");
  });

  it("does not mark a sibling whose path is only a string prefix", () => {
    renderNavigation("/app/requests-archive");

    expect(screen.getByRole("link", { name: "Requests" })).not.toHaveAttribute("aria-current");
  });
});

describe("Navigation with a destination that decides its own current page", () => {
  const OPERATOR: NavigationDestination[] = [
    { to: "/admin/acquisition/performance", label: "Insights" },
    {
      to: "/admin/acquisition",
      label: "Setup",
      match: (pathname) =>
        pathname.startsWith("/admin/acquisition") &&
        !pathname.startsWith("/admin/acquisition/performance"),
    },
  ];

  function renderOperator(path: string) {
    const router = createMemoryRouter(
      [{ path: "*", element: <Navigation label="Administration" destinations={OPERATOR} /> }],
      { initialEntries: [path] },
    );
    render(<RouterProvider router={router} />);
  }

  it("marks only the destination its rule claims", () => {
    renderOperator("/admin/acquisition/performance");
    expect(screen.getByRole("link", { name: "Insights" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Setup" })).not.toHaveAttribute("aria-current");
  });

  it("keeps a section current across its own path trees", () => {
    renderOperator("/admin/acquisition/runs/42");
    expect(screen.getByRole("link", { name: "Setup" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Insights" })).not.toHaveAttribute("aria-current");
  });
});
