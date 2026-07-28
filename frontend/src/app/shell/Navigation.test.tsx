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
