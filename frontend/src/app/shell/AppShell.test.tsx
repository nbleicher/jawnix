import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { AppShell } from "./AppShell";
import type { NavigationDestination } from "./Navigation";
import { ThemeProvider } from "../../design-system/theme/ThemeProvider";
import { Page } from "../../design-system/primitives/layout";

const DESTINATIONS: NavigationDestination[] = [
  { to: "/overview", label: "Overview" },
  { to: "/requests", label: "Requests" },
];

function Screen({ title }: { title: string }) {
  return <Page title={title}>content</Page>;
}

/** Renders the shell with a loader whose resolution the test controls, so the
 *  pending state can be observed rather than raced. */
function renderShell(options: { gateRequests?: boolean } = {}) {
  let releaseRequests = () => {};
  const requestsGate = new Promise<void>((resolve) => {
    releaseRequests = resolve;
  });

  const router = createMemoryRouter(
    [
      {
        element: <AppShell audience="Customer" destinations={DESTINATIONS} />,
        children: [
          { path: "/overview", element: <Screen title="Overview" /> },
          {
            path: "/requests",
            loader: options.gateRequests ? async () => requestsGate.then(() => null) : () => null,
            element: <Screen title="Requests" />,
          },
        ],
      },
    ],
    { initialEntries: ["/overview"] },
  );

  const result = render(
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>,
  );

  return { ...result, releaseRequests };
}

describe("AppShell", () => {
  it("exposes banner, navigation, and main landmarks exactly once", () => {
    renderShell();

    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    expect(screen.getAllByRole("main")).toHaveLength(1);
  });

  it("puts a skip link ahead of the content, targeting main", () => {
    renderShell();

    const skipLink = screen.getByRole("link", { name: "Skip to main content" });
    expect(skipLink).toHaveAttribute("href", "#jx-main");
    expect(document.querySelector("main")).toHaveAttribute("id", "jx-main");
  });

  it("shows the pending indicator only while a navigation is in flight", async () => {
    const user = userEvent.setup();
    const { releaseRequests } = renderShell({ gateRequests: true });

    const progress = document.querySelector(".jx-shell__progress");
    expect(progress).not.toHaveAttribute("data-active");

    await user.click(screen.getByRole("link", { name: "Requests" }));

    // The gated loader holds the navigation open, so the indicator is visible.
    await waitFor(() => {
      expect(document.querySelector(".jx-shell__progress")).toHaveAttribute("data-active", "true");
    });

    releaseRequests();

    await screen.findByRole("heading", { level: 1, name: "Requests" });
    await waitFor(() => {
      expect(document.querySelector(".jx-shell__progress")).not.toHaveAttribute("data-active");
    });
  });

  it("hides the pending indicator from assistive technology, which the announcer serves instead", () => {
    renderShell();

    expect(document.querySelector(".jx-shell__progress")).toHaveAttribute("aria-hidden", "true");
  });
});
