import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, Link, Outlet, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { RouteAnnouncer } from "./RouteAnnouncer";
import { useDocumentTitle } from "./useDocumentTitle";

function Screen({ title }: { title: string }) {
  useDocumentTitle(title);
  return (
    <>
      <h1>{title}</h1>
      <Link to="/app/requests">Go to requests</Link>
    </>
  );
}

function renderApp() {
  const router = createMemoryRouter(
    [
      {
        path: "/app",
        element: (
          <>
            <Outlet />
            <RouteAnnouncer />
          </>
        ),
        children: [
          { index: true, element: <Screen title="Overview" /> },
          { path: "requests", element: <Screen title="Requests" /> },
        ],
      },
    ],
    { initialEntries: ["/app"] },
  );
  return render(<RouterProvider router={router} />);
}

describe("RouteAnnouncer", () => {
  it("provides a polite live region for route changes", () => {
    renderApp();

    const announcer = screen.getByTestId("jx-route-announcer");
    expect(announcer).toHaveAttribute("aria-live", "polite");
    expect(announcer).toHaveAttribute("role", "status");
  });

  it("stays silent on first render, because the page load already announces", () => {
    renderApp();

    expect(screen.getByTestId("jx-route-announcer")).toHaveTextContent("");
  });

  it("announces the destination after a client-side navigation", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByRole("link", { name: "Go to requests" }));

    // The announcement is deferred a frame so it reports the destination title,
    // so poll the content rather than asserting synchronously.
    await waitFor(() => {
      expect(screen.getByTestId("jx-route-announcer")).toHaveTextContent("Requests");
    });
  });
});

describe("useDocumentTitle", () => {
  it("sets a suffixed document title", () => {
    renderApp();

    expect(document.title).toBe("Overview · Jawnix");
  });
});
